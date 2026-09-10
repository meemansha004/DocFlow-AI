"""
Tools for the RAG Agent — thin @tool wrappers over the retrieval pipeline
(app/services/rag/retrieval.retrieve), the direct generation/summarisation
calls (app/services/rag/generation), the ABAC document check
(app/services/access_control), and the confidential-access request service
(app/services/access_requests_service).

WHO is asking and WHICH project is NOT a tool argument — it comes from
app/services/rag_context (set per-turn by run_rag_turn), so a chat message
can never make a tool act as another user or in another project. The only
things the LLM supplies are the query text, an optional stage name, a
document reference, and a team reference — never an identity, never a
document_id, never a raw access decision.

Each tool returns a dict with a "status" field. The agent relays these
verbatim in spirit — it must not invent an answer, a summary, a citation, or
a confirmation that a tool did not return.
"""

import re
import uuid

from sqlalchemy import func

from app.database import SessionLocal
from app.models.document import Document, DocumentTeamVisibility, DocumentVersion
from app.models.stage import Stage
from app.models.team import Team, UserTeamMembership
from app.models.user import User
from app.services.access_control import (
    DocumentVisibility,
    _get_team_membership,
    classify_document_visibility,
)
from app.services.access_requests_service import (
    AccessRequestError,
    request_confidential_access as _request_confidential_access,
)
from app.services.rag.generation import GenerationError, generate_answer, summarize_full_document
from app.services.rag.retrieval import NoProjectAccessError, retrieve
from app.services.rag_context import get_rag_context

from agno.tools import tool


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _stage_name_map(db, project_id: uuid.UUID, stage_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not stage_ids:
        return {}
    rows = db.query(Stage).filter(
        Stage.project_id == project_id, Stage.stage_id.in_(stage_ids)
    ).all()
    return {s.stage_id: s.name for s in rows}


def _resolve_stage(db, project_id: uuid.UUID, stage_ref: str) -> uuid.UUID | None:
    """Accepts a stage UUID string OR a stage name (case-insensitive). None if unresolvable."""
    stage_ref = (stage_ref or "").strip()
    if not stage_ref:
        return None
    try:
        as_uuid = uuid.UUID(stage_ref)
        exists = db.query(Stage.stage_id).filter(
            Stage.stage_id == as_uuid, Stage.project_id == project_id, Stage.deleted_at.is_(None)
        ).first()
        return as_uuid if exists else None
    except ValueError:
        pass
    row = db.query(Stage).filter(
        Stage.project_id == project_id,
        Stage.deleted_at.is_(None),
        func.lower(Stage.name) == stage_ref.lower(),
    ).first()
    return row.stage_id if row else None


def _requestable_teams_for_docs(db, user_id: uuid.UUID, document_ids: list[uuid.UUID]) -> list[dict]:
    """
    For documents the user was blocked from BY SENSITIVITY, the team to
    request access on is the one the document is visible to AND the user
    already belongs to (that's exactly the team classify_document_visibility
    found them a member of). Returns unique [{"team_id", "team_name"}].
    """
    out: dict[uuid.UUID, str] = {}
    for doc_id in document_ids:
        visible_team_ids = [
            r.team_id for r in db.query(DocumentTeamVisibility).filter(
                DocumentTeamVisibility.document_id == doc_id
            )
        ]
        for team_id in visible_team_ids:
            if team_id in out:
                continue
            if _get_team_membership(db, user_id, team_id) is not None:
                team = db.get(Team, team_id)
                if team is not None:
                    out[team_id] = team.name
    return [{"team_id": str(k), "team_name": v} for k, v in out.items()]


def _resolve_team(db, project_id: uuid.UUID, team_ref: str) -> uuid.UUID | None:
    team_ref = (team_ref or "").strip()
    if not team_ref:
        return None
    try:
        as_uuid = uuid.UUID(team_ref)
        exists = db.query(Team.team_id).filter(
            Team.team_id == as_uuid, Team.project_id == project_id
        ).first()
        return as_uuid if exists else None
    except ValueError:
        pass
    row = db.query(Team).filter(
        Team.project_id == project_id, func.lower(Team.name) == team_ref.lower()
    ).first()
    return row.team_id if row else None


# ---------------------------------------------------------------------------
# 1. search_documents — grounded Q&A
# ---------------------------------------------------------------------------

@tool
def search_documents(query: str, stage_id: str | None = None) -> dict:
    """
    Answer a question from the project's indexed documents. This is the tool
    for ANY genuine information question ("what does X say", "how do we do Y",
    "what was decided about Z").

    It retrieves the most relevant approved document chunks the CURRENT USER
    is allowed to see, then generates a cited answer from ONLY those chunks.
    Every claim in the answer carries a citation naming the source and the
    stage it came from. It never uses outside knowledge.

    Args:
        query: the user's question, in natural language.
        stage_id: OPTIONAL. A stage name (e.g. "Requirements") or stage id to
            restrict the search to that stage and the stages it references.
            Omit to search everything the user can access.

    Returns a dict with "status":
        - "answered": {"answer", "sources": [...], "confidential_content_also_present"}
        - "blocked_by_sensitivity": relevant confidential content exists on a
          team the user is on, but their clearance doesn't reach it. Carries
          "requestable_teams" — offer to request access, and if the user
          agrees call request_confidential_access with that team_id.
        - "no_results": nothing relevant in the accessible documents. Say so
          honestly; do NOT guess or offer a near-miss.
        - "no_project_access": the user has no access to this project at all.
        - "error": a stage name could not be resolved, or generation failed.
    """
    ctx = get_rag_context()
    db = SessionLocal()
    try:
        resolved_stage: uuid.UUID | None = None
        if stage_id:
            resolved_stage = _resolve_stage(db, ctx.project_id, stage_id)
            if resolved_stage is None:
                return {
                    "status": "error",
                    "message": f"No stage matching '{stage_id}' exists in this project.",
                }

        try:
            result = retrieve(db, ctx.user_id, ctx.project_id, query, resolved_stage)
        except NoProjectAccessError:
            return {
                "status": "no_project_access",
                "message": "You don't have access to this project.",
            }

        if result.chunks:
            stage_names = _stage_name_map(
                db, ctx.project_id, {c.stage_id for c in result.chunks}
            )
            doc_names = {
                d.document_id: d.original_filename
                for d in db.query(Document).filter(
                    Document.document_id.in_({c.document_id for c in result.chunks})
                )
            }
            sources = []
            source_meta = []
            for i, ch in enumerate(result.chunks, start=1):
                stage_name = stage_names.get(ch.stage_id, "Unknown")
                label = f"Source {i} — {stage_name} stage"
                sources.append(
                    {"label": label, "section": ch.section_title, "text": ch.chunk_text}
                )
                source_meta.append({
                    "label": label,
                    "stage": stage_name,
                    "document": doc_names.get(ch.document_id, "(unknown document)"),
                    "section": ch.section_title,
                })

            try:
                answer = generate_answer(query, sources)
            except GenerationError as exc:
                return {"status": "error", "message": f"Could not generate an answer: {exc}"}

            payload = {
                "status": "answered",
                "answer": answer,
                "sources": source_meta,
                "confidential_content_also_present": False,
            }
            if result.blocked_by_sensitivity:
                payload["confidential_content_also_present"] = True
                payload["requestable_teams"] = _requestable_teams_for_docs(
                    db, ctx.user_id, result.blocked_document_ids
                )
            return payload

        if result.blocked_by_sensitivity:
            teams = _requestable_teams_for_docs(db, ctx.user_id, result.blocked_document_ids)
            return {
                "status": "blocked_by_sensitivity",
                "message": (
                    "Relevant content exists but it is classified confidential on a "
                    "team you're on, and your current clearance doesn't reach it."
                ),
                "requestable_teams": teams,
            }

        return {
            "status": "no_results",
            "message": "No relevant information was found in the documents you can access.",
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 2. summarize_document — whole-document summary (NOT retrieval)
# ---------------------------------------------------------------------------

@tool
def summarize_document(document_reference: str) -> dict:
    """
    Summarise ONE whole document, named by title or filename. Use this — NOT
    search_documents — whenever the user asks to "summarise", "give me an
    overview of", or "tl;dr" a SPECIFIC named document.

    The document is resolved by a direct title/filename lookup within the
    current project (not a search). Access is checked with the same ABAC rule
    as everywhere else. If allowed, the ENTIRE current approved text is
    summarised — never fragments.

    Args:
        document_reference: the document's title or filename, as the user
            said it (e.g. "the onboarding guide", "vacation-policy.md").

    Returns a dict with "status":
        - "summarized": {"document", "summary"}
        - "not_found": no document in this project matches that reference
          (this also covers documents the user cannot see at all — do not
          speculate about whether such a document exists).
        - "ambiguous": {"matches": [...]} — several documents match; ask the
          user which one.
        - "blocked_by_sensitivity": the document exists and is on the user's
          team but is confidential above their clearance. Carries
          "requestable_teams" — offer to request access.
        - "unavailable": the document has no finalized content to summarise.
        - "error": summarisation failed.
    """
    ctx = get_rag_context()
    db = SessionLocal()
    try:
        ref = (document_reference or "").strip()
        if not ref:
            return {"status": "not_found", "message": "No document reference was given."}

        candidates = _match_documents(db, ctx.project_id, ref)
        if not candidates:
            return {
                "status": "not_found",
                "message": f"No document matching '{ref}' was found in this project.",
            }
        if len(candidates) > 1:
            return {
                "status": "ambiguous",
                "matches": [d.original_filename for d in candidates],
                "message": "Multiple documents match that reference.",
            }

        doc = candidates[0]
        visibility = classify_document_visibility(db, ctx.user_id, doc)

        if visibility == DocumentVisibility.not_visible:
            # Never confirm the document exists to someone with no visibility.
            return {
                "status": "not_found",
                "message": f"No document matching '{ref}' was found in this project.",
            }

        if visibility == DocumentVisibility.blocked_by_sensitivity:
            teams = _requestable_teams_for_docs(db, ctx.user_id, [doc.document_id])
            return {
                "status": "blocked_by_sensitivity",
                "document": doc.original_filename,
                "message": (
                    f"'{doc.original_filename}' is classified confidential and your "
                    f"current clearance doesn't reach it."
                ),
                "requestable_teams": teams,
            }

        # fully_allowed
        if doc.current_version_id is None:
            return {
                "status": "unavailable",
                "document": doc.original_filename,
                "message": "That document has no finalized content yet.",
            }
        version = db.get(DocumentVersion, doc.current_version_id)
        if version is None:
            return {
                "status": "unavailable",
                "document": doc.original_filename,
                "message": "That document's current version is missing.",
            }
        try:
            content = version.file_data.decode("utf-8")
        except UnicodeDecodeError:
            return {
                "status": "unavailable",
                "document": doc.original_filename,
                "message": "That document's content is not readable as text.",
            }

        try:
            summary = summarize_full_document(doc.original_filename, content)
        except GenerationError as exc:
            return {"status": "error", "message": f"Could not summarise the document: {exc}"}

        return {
            "status": "summarized",
            "document": doc.original_filename,
            "summary": summary,
        }
    finally:
        db.close()


_REF_STOPWORDS = {
    "the", "a", "an", "document", "doc", "file", "of", "for", "about", "please",
    "whole", "entire", "this", "that", "our", "my", "summary", "overview",
}
_DOC_EXT_RE = re.compile(r"\.(md|pdf|docx|doc|txt)$", re.IGNORECASE)


def _normalize_ref(s: str) -> str:
    s = s.lower()
    for ch in "-_./\\'\"":
        s = s.replace(ch, " ")
    return " ".join(s.split())


def _match_documents(db, project_id: uuid.UUID, ref: str) -> list[Document]:
    """
    Direct Postgres lookup (NOT a vector search) — resolve a free-form
    reference to document(s) by `original_filename` within the project.

    Matching is done in Python over the project's documents (there are few per
    project) so it can be forgiving about phrasing:
      - '-' / '_' / '.' / slashes are treated as spaces, case-insensitive;
      - a filename extension on either side is ignored;
      - the reference contains the filename stem, OR the filename contains the
        reference (so "the company policy handbook document" and
        "company-policy-handbook.md" both resolve), OR every significant word
        of the filename appears in the reference.

    Returns the matches (exact-ish first); the caller treats 0 as not-found
    and >1 as ambiguous.
    """
    ref_n = _normalize_ref(ref)
    ref_stem = _DOC_EXT_RE.sub("", ref_n).strip()
    if not ref_stem:
        return []
    ref_tokens = {t for t in ref_stem.split() if t not in _REF_STOPWORDS and len(t) > 2}

    docs = (
        db.query(Document)
        .filter(Document.project_id == project_id)
        .order_by(Document.created_at.desc())
        .all()
    )

    strong: list[Document] = []
    weak: list[Document] = []
    for d in docs:
        fn = _normalize_ref(d.original_filename)
        stem = _DOC_EXT_RE.sub("", fn).strip()
        if not stem:
            continue
        if ref_stem == stem or ref_stem in fn or stem in ref_n:
            strong.append(d)
            continue
        fn_tokens = {t for t in stem.split() if t not in _REF_STOPWORDS and len(t) > 2}
        if fn_tokens and fn_tokens <= ref_tokens:
            weak.append(d)

    chosen = strong or weak
    seen, unique = set(), []
    for d in chosen:
        if d.document_id not in seen:
            seen.add(d.document_id)
            unique.append(d)
    return unique


# ---------------------------------------------------------------------------
# 3. request_confidential_access — conversational access request
# ---------------------------------------------------------------------------

@tool
def request_confidential_access(team_id: str) -> dict:
    """
    Submit a confidential-access request for the CURRENT USER on a team, to be
    reviewed by that team's lead. Call this ONLY after search_documents or
    summarize_document returned "blocked_by_sensitivity", you offered to
    request access, and the user explicitly said yes.

    Args:
        team_id: the team id (or team name) from the "requestable_teams" field
            of the blocked tool result. Do not invent one.

    Returns a dict with "status":
        - "requested": {"request_id", "team"} — a real pending request now
          exists; the team's lead(s) will review it.
        - "error": {"message"} — e.g. the user already has access or an open
          request, or is not on that team. Relay the message as-is.
    """
    ctx = get_rag_context()
    db = SessionLocal()
    try:
        team_uuid = _resolve_team(db, ctx.project_id, team_id)
        if team_uuid is None:
            return {
                "status": "error",
                "message": f"No team matching '{team_id}' exists in this project.",
            }
        user = db.get(User, ctx.user_id)
        try:
            req = _request_confidential_access(
                db,
                user_id=ctx.user_id,
                team_id=team_uuid,
                expected_tenant_id=user.tenant_id if user else None,
            )
        except AccessRequestError as exc:
            return {"status": "error", "message": exc.message}

        team = db.get(Team, team_uuid)
        return {
            "status": "requested",
            "request_id": str(req.request_id),
            "team": team.name if team else str(team_uuid),
            "message": (
                f"A confidential-access request for the {team.name if team else 'team'} "
                f"team has been submitted and is pending review by the team lead."
            ),
        }
    finally:
        db.close()
