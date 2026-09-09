"""
Audit logging (adapted from the teammate's database.py::audit_log +
get_audit_log). Phase 1 created the audit_log table but nothing wrote to it —
this closes that gap.

record_audit() adds a row to the CALLER'S session without committing, so the
audit entry lands in the same transaction as the action it describes (an
upload that rolls back leaves no audit row). details is stored as JSONB.

The read side (GET /admin/audit-log) is org_admin-only and tenant-wide; since
AuditLog has no tenant_id (Phase 1: derive via user), it joins users.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.user import User


def record_audit(
    db: Session,
    *,
    actor_id: uuid.UUID,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID | str | None = None,
    details: dict | None = None,
) -> None:
    """Stage an audit row on `db` (no commit — caller commits with its work)."""
    if isinstance(resource_id, str):
        try:
            resource_id = uuid.UUID(resource_id)
        except ValueError:
            resource_id = None
    db.add(AuditLog(
        user_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        details=details,
    ))


def _details_to_text(details: dict | None) -> str | None:
    if not details:
        return None
    return "; ".join(f"{k}={v}" for k, v in details.items())


def list_audit_for_tenant(db: Session, tenant_id: uuid.UUID, limit: int = 100) -> list[dict]:
    """Newest-first audit rows for every user in `tenant_id`."""
    rows = db.execute(
        select(AuditLog, User.email)
        .join(User, User.user_id == AuditLog.user_id)
        .where(User.tenant_id == tenant_id)
        .order_by(AuditLog.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "log_id": str(a.log_id),
            "user_id": str(a.user_id),
            "actor_email": email,
            "action": a.action,
            "resource_type": a.resource_type,
            "resource_id": str(a.resource_id) if a.resource_id else None,
            "details": _details_to_text(a.details),
            "created_at": a.created_at.isoformat(),
            "timestamp": a.created_at.isoformat(),  # his AdminPage reads `timestamp`
        }
        for a, email in rows
    ]
