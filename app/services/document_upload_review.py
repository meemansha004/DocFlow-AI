"""
Upload entry point for the "upload + scan + revise-in-chat + index" flow.

upload_and_scan() ties together:
  - document_persistence.create_document_from_file() — real file bytes ->
    Document + DocumentVersion (v1, pending_review by default), parsed to
    Markdown (Docling, called exactly once — every downstream step below
    reuses this same parsed_content, never re-parses)
  - a content-hash marker check (draft_workspace.check_scan_marker) — if this
    exact file was previously finalized by chat-drafting (Phase 4) and is
    unchanged, SKIP the paid structural rescan and reuse its embedded score
  - the Structure Scanner (score + reform-if-low, via document_finalize's
    shared run_full_scan) otherwise
  - the Injection Scanner — ALWAYS runs regardless of the marker, since it's
    a cheap deterministic security gate, not the expensive check being
    skipped. A flagged upload gets its v1 status corrected from the default
    pending_review to needs_attention (the row is still created either way)
  - persisting a DocumentScan row for v1
  - seeding the review chat session's on-disk working file (same
    draft_workspace machinery as Phase 4) with the parsed content, so the
    drafting_agent can revise it from there
"""

import uuid

from sqlalchemy.orm import Session

from app.models.document import DocumentScan, DocumentStatus, DocumentVersion, ScanReviewStatus
from app.services import draft_workspace
from app.services.document_finalize import run_full_scan, scan_passed
from app.services.document_persistence import CreatedDocumentFromFile, create_document_from_file
from app.services.injection_scan import scan_for_injection


def upload_and_scan(
    db: Session,
    *,
    user_id: uuid.UUID,
    team_id: uuid.UUID,
    project_id: uuid.UUID,
    role: str,
    stage_id: uuid.UUID,
    original_filename: str,
    mime_type: str,
    file_data: bytes,
    session_id: str,
    sensitivity: str = "internal",
) -> dict:
    """
    Raises the same exceptions as create_document_from_file (PermissionDeniedError,
    StageNotFoundError, UnsupportedDocumentTypeError, DocumentParseError).

    Returns:
        {
            "created": CreatedDocumentFromFile,
            "scan": dict | None, "scan_error": str | None,
            "reformed_content": str | None,
            "injection_flagged": bool, "injection_findings": list,
            "scan_skipped": bool,   # True when the content-hash marker matched
        }
    """
    created: CreatedDocumentFromFile = create_document_from_file(
        db,
        user_id=user_id, team_id=team_id, project_id=project_id, role=role,
        stage_id=stage_id, original_filename=original_filename, mime_type=mime_type,
        file_data=file_data, sensitivity=sensitivity,
    )

    marker = draft_workspace.check_scan_marker(created.parsed_content)

    if marker is not None:
        # Exact same content as a previously chat-finalized file — skip the
        # (LLM-backed) structural rescan, reuse the embedded score. The
        # injection scan still runs: it's a new check that never ran when
        # this file was chat-finalized, and it's cheap/local either way.
        seed_content = marker["content_without_marker"]
        scan_outcome = {
            "scan": {
                "overall_score": marker["score"],
                "criteria": [],
                "summary": "Reused from this file's earlier chat-drafting scan (content unchanged).",
            },
            "scan_error": None,
            "reformed_content": None,
            "injection": scan_for_injection(seed_content),
        }
        scan_skipped = True
    else:
        seed_content = created.parsed_content
        scan_outcome = run_full_scan(seed_content)
        scan_skipped = False

    passed = scan_passed(scan_outcome)

    # The row was just created with the model's default status
    # (pending_review — v1 always starts there, awaiting the human's first
    # look via the review chat, regardless of structural score). Injection
    # is a SECURITY concern, not a quality one, so it overrides that default
    # immediately, at upload time — don't block persistence, but don't let a
    # flagged upload sit in the ordinary "awaiting review" bucket either.
    if scan_outcome["injection"]["flagged"]:
        db.query(DocumentVersion).filter(
            DocumentVersion.version_id == created.version_id
        ).update({"status": DocumentStatus.needs_attention}, synchronize_session=False)

    if scan_outcome["scan"] is not None:
        db.add(DocumentScan(
            version_id=created.version_id,
            overall_score=scan_outcome["scan"]["overall_score"],
            criteria=scan_outcome["scan"]["criteria"],
            reform_triggered=scan_outcome["reformed_content"] is not None,
            reformed_content=scan_outcome["reformed_content"],
            # Upload-time scans are never "not_required" even on a clean pass
            # — the document still needs an explicit human finalize (chat)
            # before it can be indexed, per the upload+review flow.
            review_status=ScanReviewStatus.pending,
            injection_flagged=scan_outcome["injection"]["flagged"],
            injection_findings=scan_outcome["injection"]["findings"],
        ))

    # Unconditional (not nested under the DocumentScan branch above): the
    # needs_attention status update must be committed even on a total
    # scoring failure (scan_outcome["scan"] is None) if injection was flagged.
    db.commit()

    # Seed the review session's working file — same draft_workspace machinery
    # Phase 4 uses, just seeded with this upload's content instead of blank.
    draft_workspace.write_working_draft(session_id, seed_content)

    return {
        "created": created,
        "scan": scan_outcome["scan"],
        "scan_error": scan_outcome["scan_error"],
        "reformed_content": scan_outcome["reformed_content"],
        "injection_flagged": scan_outcome["injection"]["flagged"],
        "injection_findings": scan_outcome["injection"]["findings"],
        "scan_skipped": scan_skipped,
        "scan_passed": passed,
    }
