"""
Finalizing a document-review chat session (upload -> auto-scan -> revise in
chat -> finalize), distinct in meaning from Phase 4's chat-drafting finalize:
this writes a NEW DocumentVersion on the SAME Document row (never a new
Document — single-stage-per-upload; cross-stage relevance is handled by
stage_references, not multi-stage uploads) and, only on a passing/unflagged
scan, flips status from pending_review to indexed. That indexed transition
is what Phase B's RAG-indexing trigger fires on.

run_full_scan() is shared by the upload-time auto-scan
(document_upload_review.py) and this finalize step: score_document, then
(mirroring the Scanner Agent's own documented sequence) reform_document if
the score is below threshold, then check_injection — always, regardless of
score, since it's a cheap deterministic gate, not an expensive rescan being
guarded against.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.document import (
    Document,
    DocumentScan,
    DocumentStatus,
    DocumentVersion,
    ScanReviewStatus,
)
from app.services import draft_workspace
from app.services.audit import record_audit
from app.services.injection_scan import scan_for_injection
from app.services.scan_prompts import REFORMATION_THRESHOLD
from app.services.scan_reformer import ReformationError, reform_document
from app.services.scan_score import ScoringError, score_document


class DocumentNotFoundError(Exception):
    pass


class NoWorkingDraftError(Exception):
    pass


def run_full_scan(content: str) -> dict:
    """
    Score + (if below REFORMATION_THRESHOLD) reform + injection-scan.

    Returns:
        {
            "scan": dict | None,           # score_document() result
            "scan_error": str | None,
            "reformed_content": str | None,
            "injection": {"flagged": bool, "findings": [...]},
        }
    """
    result: dict = {"scan": None, "scan_error": None, "reformed_content": None, "injection": None}

    try:
        result["scan"] = score_document(content)
    except ScoringError as exc:
        result["scan_error"] = str(exc)
    except Exception as exc:  # network / rate-limit / etc — must not raise
        result["scan_error"] = f"{type(exc).__name__}: {exc}"

    if result["scan"] is not None and result["scan"]["overall_score"] < REFORMATION_THRESHOLD:
        try:
            result["reformed_content"] = reform_document(content, result["scan"])
        except ReformationError as exc:
            reform_err = f"reform failed: {exc}"
            result["scan_error"] = (
                f"{result['scan_error']}; {reform_err}" if result["scan_error"] else reform_err
            )

    result["injection"] = scan_for_injection(content)
    return result


def scan_passed(scan_outcome: dict) -> bool:
    """A version may become `indexed` only if it scored at/above threshold AND was not flagged."""
    return (
        scan_outcome["scan"] is not None
        and scan_outcome["scan"]["overall_score"] >= REFORMATION_THRESHOLD
        and not scan_outcome["injection"]["flagged"]
    )


def _record_scan(db: Session, *, version_id: uuid.UUID, scan_outcome: dict) -> None:
    """Persists a DocumentScan row for this version, if a score exists (a total
    scoring failure — e.g. Groq unreachable — leaves nothing to record)."""
    if scan_outcome["scan"] is None:
        return
    passed = scan_passed(scan_outcome)
    db.add(DocumentScan(
        version_id=version_id,
        overall_score=scan_outcome["scan"]["overall_score"],
        criteria=scan_outcome["scan"]["criteria"],
        reform_triggered=scan_outcome["reformed_content"] is not None,
        reformed_content=scan_outcome["reformed_content"],
        review_status=ScanReviewStatus.not_required if passed else ScanReviewStatus.pending,
        injection_flagged=scan_outcome["injection"]["flagged"],
        injection_findings=scan_outcome["injection"]["findings"],
    ))


def finalize_document_revision(db: Session, *, document_id: uuid.UUID, user_id: uuid.UUID, session_id: str) -> dict:
    """
    Reads the review session's current working-draft content (the
    drafting_agent's revisions, same draft_workspace machinery as Phase 4),
    runs the full scan, and writes it as a new DocumentVersion on
    `document_id`. Deletes the working file afterward either way.

    Returns:
        {
            "version_id": str, "version_number": int, "status": str,
            "scan": dict | None, "scan_error": str | None,
            "reformed_content": str | None,
            "injection_flagged": bool, "injection_findings": list,
        }

    Raises:
        DocumentNotFoundError, NoWorkingDraftError
    """
    document = db.get(Document, document_id)
    if document is None:
        raise DocumentNotFoundError(f"Unknown document: {document_id}")

    content = draft_workspace.read_working_draft(session_id)
    if content is None:
        raise NoWorkingDraftError("No working draft to finalize")

    scan_outcome = run_full_scan(content)
    passed = scan_passed(scan_outcome)

    max_version = db.execute(
        select(func.max(DocumentVersion.version_number)).where(
            DocumentVersion.document_id == document_id
        )
    ).scalar_one()
    new_version_number = (max_version or 0) + 1

    version_id = uuid.uuid4()
    content_bytes = content.encode("utf-8")
    version = DocumentVersion(
        version_id=version_id,
        document_id=document_id,
        version_number=new_version_number,
        file_data=content_bytes,
        file_size_bytes=len(content_bytes),
        uploaded_by=user_id,
        status=DocumentStatus.indexed if passed else DocumentStatus.pending_review,
    )
    db.add(version)
    db.flush()
    document.current_version_id = version_id

    _record_scan(db, version_id=version_id, scan_outcome=scan_outcome)

    record_audit(
        db, actor_id=user_id, action="FINALIZE_DOCUMENT_REVISION", resource_type="document",
        resource_id=document_id,
        details={
            "version_id": str(version_id), "version_number": new_version_number,
            "status": version.status.value,
            "overall_score": scan_outcome["scan"]["overall_score"] if scan_outcome["scan"] else None,
            "injection_flagged": scan_outcome["injection"]["flagged"],
        },
    )
    db.commit()

    draft_workspace.delete_working_draft(session_id)

    return {
        "version_id": str(version_id),
        "version_number": new_version_number,
        "status": version.status.value,
        "scan": scan_outcome["scan"],
        "scan_error": scan_outcome["scan_error"],
        "reformed_content": scan_outcome["reformed_content"],
        "injection_flagged": scan_outcome["injection"]["flagged"],
        "injection_findings": scan_outcome["injection"]["findings"],
    }
