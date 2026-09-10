"""
One turn of the post-upload document-review conversation.

Reuses the EXACT SAME drafting_agent + draft_workspace.py machinery Phase 4's
chat-drafting is built on (disk-based working file, full-content-preserving
revisions, draft_document / confirm_draft tools) — seeded with an uploaded
document's parsed content instead of starting blank
(document_upload_review.upload_and_scan already does the seeding).

Unlike Phase 4 (decoupled from persistence, ends in a local downloadable
file), "finalize" here means something different: confirm_draft triggers
document_finalize.finalize_document_revision(), which writes a NEW
DocumentVersion on the SAME Document row and flips status to `indexed` only
on a passing, unflagged scan.
"""

import uuid

from sqlalchemy.orm import Session

from app.agents.drafting_agent import drafting_agent
from app.services import draft_workspace
from app.services.draft_chat import build_context_prefix
from app.services.document_finalize import finalize_document_revision


def _tool_called(response, name: str) -> bool:
    return bool(getattr(response, "tools", None)) and any(
        t.tool_name == name for t in response.tools
    )


def run_review_turn(db: Session, *, session_id: str, document_id: uuid.UUID, user_id: uuid.UUID, message: str) -> dict:
    """
    Run one document-review turn for `session_id`.

    Returns:
        {
          "reply": str, "drafted": bool, "finalized": bool,
          "version_id": str | None, "version_number": int | None,
          "status": str | None,           # new version's status, finalize only
          "scan": dict | None, "scan_error": str | None,
          "reformed_content": str | None,
          "injection_flagged": bool | None, "injection_findings": list | None,
        }
    """
    prefix = build_context_prefix(session_id)
    before = draft_workspace.read_working_draft(session_id)
    response = drafting_agent.run(prefix + (message or "continue"), session_id=session_id)
    after = draft_workspace.read_working_draft(session_id)
    reply = getattr(response, "content", "") or ""

    drafted_this_turn = _tool_called(response, "draft_document") or (
        after is not None and after != before
    )

    base = {
        "reply": reply, "drafted": False, "finalized": False,
        "version_id": None, "version_number": None, "status": None,
        "scan": None, "scan_error": None, "reformed_content": None,
        "injection_flagged": None, "injection_findings": None,
    }

    if _tool_called(response, "confirm_draft"):
        if not draft_workspace.has_working_draft(session_id):
            base["reply"] = reply or "There is no draft in this conversation to finalize yet."
            return base
        outcome = finalize_document_revision(
            db, document_id=document_id, user_id=user_id, session_id=session_id
        )
        base.update(
            finalized=True,
            version_id=outcome["version_id"],
            version_number=outcome["version_number"],
            status=outcome["status"],
            scan=outcome["scan"],
            scan_error=outcome["scan_error"],
            reformed_content=outcome["reformed_content"],
            injection_flagged=outcome["injection_flagged"],
            injection_findings=outcome["injection_findings"],
        )
        return base

    base["drafted"] = drafted_this_turn
    return base
