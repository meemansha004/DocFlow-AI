"""
Direct-Postgres resolution of free-form references to documents, stages, and
teams within a project. NOT a vector search — plain lookups over the handful
of rows a project has, forgiving about phrasing.

Extracted from app/tools/rag_tools.py so the Query Agent's tools
(app/tools/query_tools.py) can resolve a "document reference" the same way
summarize_document does, without importing the RAG retrieval pipeline (and
its embedding-model stack) just to reuse a string matcher.
"""

import re
import uuid

from sqlalchemy import func

from app.models.document import Document
from app.models.stage import Stage
from app.models.team import Team


# ---------------------------------------------------------------------------
# document reference -> Document rows
# ---------------------------------------------------------------------------

_REF_STOPWORDS = {
    "the", "a", "an", "document", "doc", "file", "of", "for", "about", "please",
    "whole", "entire", "this", "that", "our", "my", "summary", "overview",
}
_DOC_EXT_RE = re.compile(r"\.(md|pdf|docx|doc|txt)$", re.IGNORECASE)


def normalize_ref(s: str) -> str:
    s = s.lower()
    for ch in "-_./\\'\"":
        s = s.replace(ch, " ")
    return " ".join(s.split())


def match_documents(db, project_id: uuid.UUID, ref: str) -> list[Document]:
    """
    Resolve a free-form reference to document(s) by `original_filename` within
    the project. Matching is done in Python (few docs per project) so it can be
    forgiving:
      - '-' / '_' / '.' / slashes are treated as spaces, case-insensitive;
      - a filename extension on either side is ignored;
      - the reference contains the filename stem, OR the filename contains the
        reference, OR every significant word of the filename appears in the
        reference.

    Returns matches (exact-ish first); the caller treats 0 as not-found and
    >1 as ambiguous.
    """
    ref_n = normalize_ref(ref)
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
        fn = normalize_ref(d.original_filename)
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
# stage / team reference -> id
# ---------------------------------------------------------------------------

def resolve_stage(db, project_id: uuid.UUID, stage_ref: str) -> uuid.UUID | None:
    """A stage UUID string OR a stage name (case-insensitive). None if unresolvable."""
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


def resolve_team(db, project_id: uuid.UUID, team_ref: str) -> uuid.UUID | None:
    """A team UUID string OR a team name (case-insensitive). None if unresolvable."""
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
