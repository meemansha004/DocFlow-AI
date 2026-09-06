"""
Document persistence — Phase 5 + ABAC.

Real DB writes now, gated by has_permission(). Uses get_current_session()
internally to know WHO is uploading and AS WHICH team — this is never
supplied by the LLM/chat input, only by the CLI's startup session selection.
"""

import uuid
from datetime import datetime, timezone

from app.database import SessionLocal
from app.models.document import Document, DocumentVersion, DocumentTeamVisibility, DocumentStatus
from app.models.stage import Stage
from app.services.session_context import get_current_session
from app.services.access_control import has_permission, resolve_sensitivity


class PermissionDeniedError(Exception):
    pass


class StageNotFoundError(Exception):
    pass


def create_document(document_type: str, stage_name: str, content: str, sensitivity: str = "internal") -> str:
    """
    Persists a document as real Document + DocumentVersion rows, gated by
    has_permission(). Also seeds document_team_visibility for the
    uploader's team, per the existing design.

    Args:
        document_type: used as original_filename base
        stage_name: must match a real Stage in the current session's project
        content: the document's Markdown content
        sensitivity: requested sensitivity level, capped by resolve_sensitivity()

    Returns:
        A confirmation string with the new document_id.

    Raises:
        PermissionDeniedError: if the current session's user lacks upload rights
        StageNotFoundError: if stage_name doesn't match a real stage in this project
    """
    session = get_current_session()
    db = SessionLocal()

    try:
        if not has_permission(db, session["user_id"], "upload", session["team_id"], session["project_id"]):
            raise PermissionDeniedError(
                f"You do not have permission to upload documents as this team."
            )

        stage = (
            db.query(Stage)
            .filter(Stage.project_id == session["project_id"], Stage.name.ilike(stage_name.strip()))
            .first()
        )
        if stage is None:
            raise StageNotFoundError(
                f"No stage named '{stage_name}' exists in this project. "
                f"Please use an existing stage name."
            )

        final_sensitivity = resolve_sensitivity(sensitivity, session["role"])

        # Get tenant_id via the user (denormalized onto Document per design)
        from app.models.user import User
        user = db.get(User, session["user_id"])

        document_id = uuid.uuid4()
        version_id = uuid.uuid4()
        content_bytes = content.encode("utf-8")

        doc = Document(
            document_id=document_id,
            tenant_id=user.tenant_id,
            project_id=session["project_id"],
            stage_id=stage.stage_id,
            uploaded_by=session["user_id"],
            uploaded_as_team_id=session["team_id"],
            sensitivity_level=final_sensitivity,
            original_filename=f"{document_type}.md",
            mime_type="text/markdown",
        )
        db.add(doc)

        version = DocumentVersion(
            version_id=version_id,
            document_id=document_id,
            version_number=1,
            file_data=content_bytes,
            file_size_bytes=len(content_bytes),
            uploaded_by=session["user_id"],
            status=DocumentStatus.indexed,  # already scanned clean before this is called
        )
        db.add(version)

        db.flush()  # so document_id/version_id are usable before commit
        doc.current_version_id = version_id

        visibility = DocumentTeamVisibility(
            document_id=document_id,
            team_id=session["team_id"],
        )
        db.add(visibility)

        db.commit()

        return (
            f"Document saved: '{document_type}' in stage '{stage.name}' "
            f"(sensitivity: {final_sensitivity.value}). Document ID: {document_id}"
        )

    except (PermissionDeniedError, StageNotFoundError):
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()