"""
Document persistence — real DB writes, gated by has_permission() AND
has_stage_access() (Phase A Part 3: a team also needs a team_stage_access
grant for the target stage, org_admin/project_admin bypass both).

create_document() takes the acting identity (user_id / team_id / project_id /
role) as EXPLICIT parameters. It no longer reaches into session_context —
callers decide where "who is doing this" comes from:
  - the CLI draft flow passes session_context values (app/tools/draft_tools.py)
  - the HTTP upload endpoint passes the authenticated ResolvedIdentity
    (app/routers/documents.py)

The caller owns the SQLAlchemy Session's lifecycle; create_document() commits
its unit of work but never closes the session.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.document import (
    Document,
    DocumentVersion,
    DocumentTeamVisibility,
    DocumentStatus,
    SensitivityLevel,
)
from app.models.stage import Stage
from app.models.user import User
from app.models.workflow import WorkflowState, WorkflowStatus
from app.services.access_control import has_permission, has_stage_access, resolve_sensitivity
from app.services.audit import record_audit


class PermissionDeniedError(Exception):
    pass


class StageNotFoundError(Exception):
    pass


@dataclass
class CreatedDocument:
    document_id: uuid.UUID
    version_id: uuid.UUID
    stage_id: uuid.UUID
    stage_name: str
    sensitivity_level: SensitivityLevel
    # "draft" when the stage requires approval (a WorkflowState row was created),
    # None when it does not (no row — approval is not applicable).
    workflow_state: str | None


def create_document(
    db: Session,
    *,
    user_id: uuid.UUID,
    team_id: uuid.UUID,
    project_id: uuid.UUID,
    role: str,
    document_type: str,
    stage_id: uuid.UUID,
    content: str,
    sensitivity: "SensitivityLevel | int | str" = SensitivityLevel.internal,
) -> CreatedDocument:
    """
    Persist a document as real Document + DocumentVersion rows, gated by
    has_permission(user_id, "upload", team_id, project_id). Seeds
    document_team_visibility for the acting team, per the existing design.

    Args:
        db: caller-owned session (committed here, not closed here)
        user_id / team_id / project_id: the acting context
        role: the acting role on that team ("viewer" / "contributor" /
            "team_lead" / "org_admin" / "project_admin") — only used to cap
            requested sensitivity via resolve_sensitivity()
        document_type: used as the original_filename base
        stage_id: a real Stage UUID; must belong to project_id and not be
            soft-deleted
        content: document body (stored as UTF-8 bytes, mime text/markdown)
        sensitivity: requested level (enum / int / name); capped by role

    Raises:
        PermissionDeniedError: acting context lacks upload rights
        StageNotFoundError: stage_id missing / in another project / deleted
    """
    if not has_permission(db, user_id, "upload", team_id, project_id):
        raise PermissionDeniedError(
            "You do not have permission to upload documents as this team."
        )

    stage = db.get(Stage, stage_id)
    if stage is None or stage.project_id != project_id or stage.deleted_at is not None:
        raise StageNotFoundError(
            f"Stage {stage_id} does not exist in this project (or has been deleted)."
        )

    # THIRD check (Phase A Part 3), alongside the role/team-project check
    # above: does the acting team have a team_stage_access grant for this
    # stage? org_admin/project_admin bypass entirely (has_stage_access()
    # applies the same bypass has_permission() does above).
    if not has_stage_access(db, user_id, team_id, stage_id, project_id):
        raise PermissionDeniedError(
            f"Team does not have access to upload to the '{stage.name}' stage."
        )

    final_sensitivity = resolve_sensitivity(sensitivity, role)

    user = db.get(User, user_id)
    if user is None:
        raise ValueError(f"Unknown user: {user_id}")

    document_id = uuid.uuid4()
    version_id = uuid.uuid4()
    content_bytes = content.encode("utf-8")

    doc = Document(
        document_id=document_id,
        tenant_id=user.tenant_id,  # denormalized onto Document per design
        project_id=project_id,
        stage_id=stage.stage_id,
        uploaded_by=user_id,
        uploaded_as_team_id=team_id,
        sensitivity_level=final_sensitivity,
        original_filename=f"{document_type}.md",
        mime_type="text/markdown",
    )
    version = DocumentVersion(
        version_id=version_id,
        document_id=document_id,
        version_number=1,
        file_data=content_bytes,
        file_size_bytes=len(content_bytes),
        uploaded_by=user_id,
        status=DocumentStatus.indexed,
    )
    db.add_all([doc, version])
    db.flush()  # so document_id / version_id are usable before commit
    doc.current_version_id = version_id

    db.add(DocumentTeamVisibility(document_id=document_id, team_id=team_id))

    # Approval workflow (MERGE_DECISIONS §3/4): a WorkflowState row is created
    # ONLY if this document's stage requires sign-off. No row == not applicable.
    workflow_state: str | None = None
    if stage.requires_approval:
        db.add(WorkflowState(document_id=document_id, state=WorkflowStatus.draft))
        workflow_state = WorkflowStatus.draft.value

    record_audit(
        db, actor_id=user_id, action="UPLOAD_DOCUMENT", resource_type="document",
        resource_id=document_id,
        details={
            "stage_id": str(stage.stage_id),
            "team_id": str(team_id),
            "sensitivity": final_sensitivity.name,
            "workflow_state": workflow_state or "none",
        },
    )
    db.commit()

    return CreatedDocument(
        document_id=document_id,
        version_id=version_id,
        stage_id=stage.stage_id,
        stage_name=stage.name,
        sensitivity_level=final_sensitivity,
        workflow_state=workflow_state,
    )
