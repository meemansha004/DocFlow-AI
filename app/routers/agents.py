"""
HTTP surface for the Drafting + Scanner agents — the CLI's /draft loop exposed
over HTTP.

  POST /agents/draft/message            one drafting turn { session_id, message }
  GET  /agents/draft/download/{filename} download a finalized draft

DECOUPLED FROM PERSISTENCE (MERGE_DECISIONS §4): finalizing a draft here scores
it with the Structure Scanner and writes a standalone Markdown file under
drafts/ — it never creates a Document row, never touches has_permission(), and
never asks about a stage/team/project. Real persistence is POST
/documents/upload. The endpoints only require authentication; there is no ABAC
because there is no project resource involved.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.dependencies import get_current_user
from app.services.auth import ResolvedIdentity
from app.services.draft_chat import run_draft_turn
from app.services.draft_export import DRAFTS_DIR

router = APIRouter(prefix="/agents", tags=["agents"])


class DraftMessageRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=200)
    message: str = Field(default="", max_length=20000)


class DraftMessageResponse(BaseModel):
    reply: str
    drafted: bool = False
    finalized: bool = False
    scan: dict | None = None
    scan_error: str | None = None
    final_content: str | None = None
    download_url: str | None = None
    filename: str | None = None


@router.post("/draft/message", response_model=DraftMessageResponse)
def draft_message(
    body: DraftMessageRequest,
    identity: ResolvedIdentity = Depends(get_current_user),
):
    # session_id is the caller's own opaque conversation id (a UUID from the
    # frontend). It scopes the on-disk working file — see draft_workspace.
    safe_id = Path(body.session_id).name
    if safe_id != body.session_id:
        raise HTTPException(status_code=422, detail="Invalid session_id")

    try:
        turn = run_draft_turn(safe_id, body.message)
    except Exception as exc:  # noqa: BLE001 — Groq / rate-limit / parse failures
        raise HTTPException(
            status_code=502,
            detail=f"The drafting agent could not complete this turn: {exc}",
        ) from exc

    return DraftMessageResponse(
        reply=turn["reply"],
        drafted=turn["drafted"],
        finalized=turn["finalized"],
        scan=turn["scan"],
        scan_error=turn["scan_error"],
        final_content=turn["final_content"],
        filename=turn["filename"],
        download_url=(
            f"/agents/draft/download/{turn['filename']}" if turn["filename"] else None
        ),
    )


@router.get("/draft/download/{filename}")
def download_draft(
    filename: str,
    identity: ResolvedIdentity = Depends(get_current_user),
):
    safe = Path(filename).name
    if safe != filename or not safe.endswith(".md"):
        raise HTTPException(status_code=422, detail="Invalid filename")
    path = DRAFTS_DIR / safe
    if not path.is_file():
        raise HTTPException(status_code=404, detail="That drafted file was not found")
    return FileResponse(path, media_type="text/markdown", filename=safe)
