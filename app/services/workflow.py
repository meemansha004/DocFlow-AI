"""
Document approval workflow (MERGE_DECISIONS §3/4 crossover).

A document has a WorkflowState row ONLY if its stage has
requires_approval=True — created by create_document(). Absence of a row means
"no human sign-off needed for this document"; every function here raises
WorkflowError in that case.

Lifecycle:
    draft     --submit-->  pending_review  --approve-->  approved
    rejected  --submit-->  pending_review  --reject -->  rejected

A rejected document can be resubmitted (its stale rejection_reason is cleared);
an approved document is terminal.

This is purely human sign-off and has nothing to do with the Structure
Scanner (which only runs in the decoupled chat-drafting flow, not on real
document persistence).

Gating: has_permission(db, user_id, <action>, team_id, project_id), where
team_id / project_id are the DOCUMENT's team and project. Per §3/4:
  - "submit"  -> contributor+ (same bar as upload)
  - "approve" -> team_lead+ on that specific team
  - "reject"  -> team_lead+ on that specific team
org_admin / project_admin bypass, as everywhere else.

The `role` parameter is accepted for call-site symmetry with the rest of the
service layer; has_permission() is the actual authority and does not need it.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.workflow import WorkflowState, WorkflowStatus
from app.services.access_control import has_permission
from app.services.audit import record_audit


class WorkflowError(Exception):
    """No workflow row for this document, or it is in the wrong state."""


class WorkflowPermissionError(Exception):
    """Caller lacks the required role for this workflow action."""


def get_workflow_state(db: Session, document_id: uuid.UUID) -> WorkflowState | None:
    """The document's WorkflowState row, or None if its stage needs no approval."""
    return db.execute(
        select(WorkflowState).where(WorkflowState.document_id == document_id)
    ).scalar_one_or_none()


def _require_state(db: Session, document_id: uuid.UUID) -> WorkflowState:
    state = get_workflow_state(db, document_id)
    if state is None:
        raise WorkflowError(
            f"Document {document_id} has no approval workflow "
            "(its stage does not require approval)."
        )
    return state


def submit_for_review(
    db: Session,
    document_id: uuid.UUID,
    user_id: uuid.UUID,
    team_id: uuid.UUID,
    project_id: uuid.UUID,
    role: str,
) -> WorkflowState:
    """
    draft -> pending_review, or rejected -> pending_review (resubmission after
    addressing the feedback). Requires 'submit' (contributor+). On resubmission
    from 'rejected', the stale rejection_reason is cleared.
    """
    state = _require_state(db, document_id)
    if not has_permission(db, user_id, "submit", team_id, project_id):
        raise WorkflowPermissionError(
            "You do not have permission to submit documents for review on this team."
        )
    if state.state not in (WorkflowStatus.draft, WorkflowStatus.rejected):
        raise WorkflowError(
            f"Document is '{state.state.value}' — only a 'draft' or a "
            "'rejected' document can be submitted for review."
        )
    if state.state == WorkflowStatus.rejected:
        state.rejection_reason = None  # the previous rejection no longer applies
    state.state = WorkflowStatus.pending_review
    record_audit(
        db, actor_id=user_id, action="SUBMIT_DOCUMENT", resource_type="document",
        resource_id=document_id, details={"state": "pending_review"},
    )
    db.commit()
    return state


def approve_document(
    db: Session,
    document_id: uuid.UUID,
    approver_id: uuid.UUID,
    team_id: uuid.UUID,
    project_id: uuid.UUID,
    role: str,
) -> WorkflowState:
    """pending_review -> approved. Requires 'approve' (team_lead+ on this team)."""
    state = _require_state(db, document_id)
    if not has_permission(db, approver_id, "approve", team_id, project_id):
        raise WorkflowPermissionError(
            "You do not have permission to approve documents on this team."
        )
    if state.state != WorkflowStatus.pending_review:
        raise WorkflowError(
            f"Document is '{state.state.value}', not 'pending_review' — cannot approve."
        )
    state.state = WorkflowStatus.approved
    state.approved_by = approver_id
    state.approval_timestamp = datetime.now(timezone.utc)
    state.rejection_reason = None
    record_audit(
        db, actor_id=approver_id, action="APPROVE_DOCUMENT", resource_type="document",
        resource_id=document_id, details={"state": "approved"},
    )
    db.commit()
    return state


def reject_document(
    db: Session,
    document_id: uuid.UUID,
    approver_id: uuid.UUID,
    team_id: uuid.UUID,
    project_id: uuid.UUID,
    role: str,
    reason: str,
) -> WorkflowState:
    """
    pending_review -> rejected. Requires 'reject' (team_lead+ on this team) and
    a non-empty reason (raises ValueError otherwise).
    """
    if not reason or not reason.strip():
        raise ValueError("A rejection reason is required.")
    state = _require_state(db, document_id)
    if not has_permission(db, approver_id, "reject", team_id, project_id):
        raise WorkflowPermissionError(
            "You do not have permission to reject documents on this team."
        )
    if state.state != WorkflowStatus.pending_review:
        raise WorkflowError(
            f"Document is '{state.state.value}', not 'pending_review' — cannot reject."
        )
    state.state = WorkflowStatus.rejected
    state.rejection_reason = reason.strip()
    record_audit(
        db, actor_id=approver_id, action="REJECT_DOCUMENT", resource_type="document",
        resource_id=document_id, details={"state": "rejected", "reason": reason.strip()},
    )
    db.commit()
    return state
