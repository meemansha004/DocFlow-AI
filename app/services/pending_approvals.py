"""
"What is awaiting my review right now" — the query behind the Admin
→ Pending Approvals tab, made reusable so the Query Agent's
list_pending_approvals tool reports exactly what that tab shows.

Two independent kinds of pending work, same as the tab:
  * documents in a requires_approval stage sitting at `pending_review`
    (submit -> approve/reject), scoped to what the caller can see — this
    mirrors the frontend's projectsApi.pendingApprovals(), which filters the
    caller's visible `GET /documents` list to workflow_state == 'pending_review'.
  * confidential-access requests the caller may decide — delegated to
    access_requests_service.pending_requests_for_reviewer(), the same query
    behind GET /access-requests/pending.

The tab itself is only offered to users with a review role (org_admin,
project_admin, or team_lead somewhere in the project); reviews_project()
reproduces that gate.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.team import ProjectAdmin, TeamRole, UserTeamMembership
from app.models.user import User
from app.models.workflow import WorkflowState, WorkflowStatus
from app.services.access_control import build_access_filter, can_view_document


def reviews_project(db: Session, user_id: uuid.UUID, project_id: uuid.UUID) -> bool:
    """
    Does this user have a review role in this project — the gate the Admin
    → Pending Approvals tab uses to decide whether to show itself at all
    (org_admin / project_admin / team_lead on any team in the project)?
    """
    user = db.get(User, user_id)
    if user is not None and user.is_org_admin:
        return True
    if db.execute(
        select(ProjectAdmin.id).where(
            ProjectAdmin.user_id == user_id, ProjectAdmin.project_id == project_id
        )
    ).first() is not None:
        return True
    return db.execute(
        select(UserTeamMembership.id).where(
            UserTeamMembership.user_id == user_id,
            UserTeamMembership.project_id == project_id,
            UserTeamMembership.role == TeamRole.team_lead,
        ).limit(1)
    ).first() is not None


def documents_awaiting_approval(
    db: Session, user_id: uuid.UUID, project_id: uuid.UUID
) -> list[Document]:
    """
    Documents in `project_id` at workflow state `pending_review` that
    `user_id` can see — the same coarse access filter + per-row
    can_view_document() refine that GET /documents applies, then narrowed to
    the pending-review WorkflowState rows (the client-side filter the
    Pending Approvals tab does today).
    """
    access_filter = build_access_filter(db, user_id, project_id)
    rows = db.execute(
        select(Document).where(access_filter, Document.project_id == project_id)
    ).scalars().all()
    visible = [d for d in rows if can_view_document(db, user_id, d)]
    if not visible:
        return []

    pending_ids = {
        w.document_id
        for w in db.execute(
            select(WorkflowState).where(
                WorkflowState.document_id.in_([d.document_id for d in visible]),
                WorkflowState.state == WorkflowStatus.pending_review,
            )
        ).scalars()
    }
    return [d for d in visible if d.document_id in pending_ids]
