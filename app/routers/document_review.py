"""
The "upload + scan + revise-in-chat + index" flow's HTTP surface.

  POST /documents/upload-file     upload a REAL file (PDF/DOCX/TXT/MD),
                                   auto-scan it, seed a review chat session
  POST /documents/review/message  one turn of that review conversation —
                                   revise via the drafting agent, or finalize
                                   (writes a new DocumentVersion, indexes on
                                   a passing/unflagged scan)

Distinct from POST /documents/upload (pasted text, Phase 3) and from
/agents/draft/* (Phase 4 chat-drafting, decoupled from persistence,
completely untouched by this work).
"""

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.api.dependencies import get_current_user
from app.database import get_db
from app.models.document import Document
from app.models.project import Project
from app.models.team import Team
from app.services.access_control import has_permission
from app.services.auth import ResolvedIdentity
from app.services.document_parser import DocumentParseError, UnsupportedDocumentTypeError
from app.services.document_persistence import PermissionDeniedError, StageNotFoundError
from app.services.document_review_chat import run_review_turn
from app.services.document_upload_review import upload_and_scan

router = APIRouter(prefix="/documents", tags=["document-review"])


# --- models ------------------------------------------------------------

class ScanOut(BaseModel):
    overall_score: int
    criteria: list[dict]
    summary: str


class UploadFileResponse(BaseModel):
    document_id: str
    version_id: str
    stage_id: str
    stage_name: str
    sensitivity_level: str
    uploaded_as_team_id: str
    status: str  # "pending_review" — every new upload
    session_id: str  # review chat session id, pass to /review/message
    scan: ScanOut | None
    scan_error: str | None
    scan_skipped: bool  # True when the content-hash marker matched (reused score)
    reformed_content: str | None
    injection_flagged: bool
    injection_findings: list[dict]
    reply: str  # initial chat message summarizing the auto-scan result


class ReviewMessageRequest(BaseModel):
    document_id: uuid.UUID
    session_id: str
    message: str = ""


class ReviewMessageResponse(BaseModel):
    reply: str
    drafted: bool = False
    finalized: bool = False
    version_id: str | None = None
    version_number: int | None = None
    status: str | None = None
    scan: ScanOut | None = None
    scan_error: str | None = None
    reformed_content: str | None = None
    injection_flagged: bool | None = None
    injection_findings: list[dict] | None = None
    should_index: bool | None = None


# --- helpers -------------------------------------------------------------

def _scan_summary_reply(outcome: dict, stage_name: str) -> str:
    scan = outcome["scan"]
    lines = []
    if outcome["scan_skipped"]:
        lines.append(
            f"Uploaded to **{stage_name}**. This file matches a document already scanned "
            f"during chat-drafting — reusing that score instead of re-scanning."
        )
    else:
        lines.append(f"Uploaded to **{stage_name}**. Running the Structure Scanner...")

    if scan is not None:
        lines.append(f"\n**Structure Scanner: {scan['overall_score']}/60**")
        if scan.get("summary"):
            lines.append(scan["summary"])
    elif outcome["scan_error"]:
        lines.append(f"\nThe Structure Scanner could not run: {outcome['scan_error']}")

    if outcome["reformed_content"]:
        lines.append(
            "\nThe score was below the quality threshold — a suggested reform was generated. "
            "Ask me to revise the document, or tell me if the reform looks good and I'll fold it in."
        )

    if outcome["injection_flagged"]:
        lines.append(
            "\n⚠️ **This document was flagged by the Injection Scanner** for prompt-injection-style "
            "content and needs human review before it can be indexed, regardless of its structural score."
        )

    lines.append(
        "\nMake any changes you'd like here in chat, then say it looks good to finalize — that writes "
        "a new version of this document and, on a passing scan, indexes it."
    )
    return "\n".join(lines)


# --- endpoints -----------------------------------------------------------

@router.post("/upload-file", response_model=UploadFileResponse, status_code=201)
async def upload_file(
    file: UploadFile = File(...),
    stage_id: uuid.UUID = Form(...),
    team_id: uuid.UUID = Form(...),
    sensitivity_level: str = Form("internal"),
    identity: ResolvedIdentity = Depends(get_current_user),
    db=Depends(get_db),
):
    team = db.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")

    project = db.get(Project, team.project_id)
    if project is None or project.tenant_id != identity.tenant_id:
        raise HTTPException(status_code=403, detail="That team is not in your organization")

    role = identity.role_on_team(team_id, project.project_id)
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=422, detail="The uploaded file is empty")

    session_id = str(uuid.uuid4())

    try:
        outcome = upload_and_scan(
            db,
            user_id=identity.user_id, team_id=team_id, project_id=project.project_id, role=role,
            stage_id=stage_id, original_filename=file.filename or "upload",
            mime_type=file.content_type or "application/octet-stream",
            file_data=file_bytes, session_id=session_id, sensitivity=sensitivity_level,
        )
    except PermissionDeniedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except StageNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UnsupportedDocumentTypeError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except DocumentParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:  # bad sensitivity name, unknown user, etc.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    created = outcome["created"]
    return UploadFileResponse(
        document_id=str(created.document_id),
        version_id=str(created.version_id),
        stage_id=str(created.stage_id),
        stage_name=created.stage_name,
        sensitivity_level=created.sensitivity_level.name,
        uploaded_as_team_id=str(team_id),
        status="pending_review",
        session_id=session_id,
        scan=outcome["scan"],
        scan_error=outcome["scan_error"],
        scan_skipped=outcome["scan_skipped"],
        reformed_content=outcome["reformed_content"],
        injection_flagged=outcome["injection_flagged"],
        injection_findings=outcome["injection_findings"],
        reply=_scan_summary_reply(outcome, created.stage_name),
    )


@router.post("/review/message", response_model=ReviewMessageResponse)
def review_message(
    body: ReviewMessageRequest,
    identity: ResolvedIdentity = Depends(get_current_user),
    db=Depends(get_db),
):
    document = db.get(Document, body.document_id)
    if document is None or document.tenant_id != identity.tenant_id:
        raise HTTPException(status_code=404, detail="Document not found")

    # Same bar as uploading: the acting user must still have "upload" rights
    # on this document's team (org_admin/project_admin bypass, as everywhere
    # else). Deliberately not scoped to "only the original uploader" — any
    # teammate with upload rights on this stage/team can continue the review.
    if not has_permission(db, identity.user_id, "upload", document.uploaded_as_team_id, document.project_id):
        raise HTTPException(
            status_code=403,
            detail="You do not have permission to revise documents for this team.",
        )

    try:
        turn = run_review_turn(
            db, session_id=body.session_id, document_id=document.document_id,
            user_id=identity.user_id, message=body.message,
        )
    except Exception as exc:  # noqa: BLE001 — Groq / rate-limit / parse failures
        raise HTTPException(
            status_code=502,
            detail=f"The document-review agent could not complete this turn: {exc}",
        ) from exc

    return ReviewMessageResponse(**turn)
