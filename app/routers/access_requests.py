"""
Phase 6: the confidential-access request workflow.

This is the request/approval side of the "contributor with an active access
grant" exception in app/services/access_control.py — a viewer/contributor who
is blocked from a confidential document asks the team's lead for clearance;
once approved they get a 90-day grant that can_view_document() honours.

  POST /access-requests                    request confidential access on a team
                                           (any member who is not already cleared)
  GET  /access-requests/mine               the caller's own requests + live status
  GET  /access-requests/pending            requests the caller may decide
  POST /access-requests/{id}/approve       -> approved, expires in 90 days
  POST /access-requests/{id}/deny          -> denied

Approve / deny and the /pending listing are gated by
has_permission(..., "approve_access_request", team_id, project_id) — team_lead
on that team, or project_admin / org_admin.
"""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.database import get_db
from app.models.project import Project
from app.models.team import (
    AccessRequest,
    AccessRequestStatus,
    Team,
    TeamRole,
    UserTeamMembership,
)
from app.models.user import User
from app.services.access_control import has_permission
from app.services.audit import record_audit
from app.services.auth import ResolvedIdentity

router = APIRouter(prefix="/access-requests", tags=["access-requests"])

_GRANT_TTL_DAYS = 90


class CreateAccessRequest(BaseModel):
    team_id: uuid.UUID


class AccessRequestOut(BaseModel):
    request_id: str
    user_id: str
    requester_email: str | None
    team_id: str
    team_name: str
    project_id: str
    project_name: str
    status: str
    requested_at: str | None
    decided_at: str | None
    expires_at: str | None
    active: bool  # approved and not expired — i.e. currently grants clearance


def _is_expired(r: AccessRequest) -> bool:
    return bool(r.expires_at and r.expires_at < datetime.now(timezone.utc))


def _serialize(db: Session, r: AccessRequest, *, team=None, project=None, requester=None) -> AccessRequestOut:
    team = team or db.get(Team, r.team_id)
    project = project or (db.get(Project, team.project_id) if team else None)
    requester = requester or db.get(User, r.user_id)
    return AccessRequestOut(
        request_id=str(r.request_id),
        user_id=str(r.user_id),
        requester_email=requester.email if requester else None,
        team_id=str(r.team_id),
        team_name=team.name if team else "(unknown)",
        project_id=str(team.project_id) if team else "",
        project_name=project.name if project else "(unknown)",
        status=r.status.value,
        requested_at=r.requested_at.isoformat() if r.requested_at else None,
        decided_at=r.decided_at.isoformat() if r.decided_at else None,
        expires_at=r.expires_at.isoformat() if r.expires_at else None,
        active=(r.status == AccessRequestStatus.approved and not _is_expired(r)),
    )


def _live_request(db: Session, user_id: uuid.UUID, team_id: uuid.UUID) -> AccessRequest | None:
    """The caller's current pending — or approved-and-still-valid — request for a team."""
    rows = db.execute(
        select(AccessRequest)
        .where(AccessRequest.user_id == user_id, AccessRequest.team_id == team_id)
        .order_by(AccessRequest.requested_at.desc())
    ).scalars().all()
    for r in rows:
        if r.status == AccessRequestStatus.pending:
            return r
        if r.status == AccessRequestStatus.approved and not _is_expired(r):
            return r
    return None


@router.post("", response_model=AccessRequestOut, status_code=201)
def create_access_request(
    body: CreateAccessRequest,
    identity: ResolvedIdentity = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    team = db.get(Team, body.team_id)
    project = db.get(Project, team.project_id) if team else None
    if team is None or project is None or project.tenant_id != identity.tenant_id:
        raise HTTPException(status_code=404, detail="Team not found")

    membership = db.execute(
        select(UserTeamMembership).where(
            UserTeamMembership.user_id == identity.user_id,
            UserTeamMembership.team_id == team.team_id,
        )
    ).scalar_one_or_none()
    is_project_admin = team.project_id in identity.project_admin_project_ids
    if membership is None and not is_project_admin and not identity.is_org_admin:
        raise HTTPException(status_code=403, detail="You are not a member of this team")

    # team_lead / project_admin / org_admin already see confidential automatically.
    if identity.is_org_admin or is_project_admin or (
        membership is not None and membership.role == TeamRole.team_lead
    ):
        raise HTTPException(
            status_code=409, detail="You already have confidential access on this team."
        )

    existing = _live_request(db, identity.user_id, team.team_id)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"You already have a {existing.status.value} confidential-access request for this team.",
        )

    req = AccessRequest(
        user_id=identity.user_id, team_id=team.team_id, status=AccessRequestStatus.pending
    )
    db.add(req)
    db.flush()
    record_audit(
        db, actor_id=identity.user_id, action="REQUEST_CONFIDENTIAL_ACCESS",
        resource_type="team", resource_id=team.team_id,
        details={"team": team.name, "project": project.name},
    )
    db.commit()
    db.refresh(req)
    return _serialize(db, req, team=team, project=project, requester=db.get(User, identity.user_id))


@router.get("/mine", response_model=list[AccessRequestOut])
def my_access_requests(
    identity: ResolvedIdentity = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(AccessRequest)
        .where(AccessRequest.user_id == identity.user_id)
        .order_by(AccessRequest.requested_at.desc())
    ).scalars().all()
    out: list[AccessRequestOut] = []
    for r in rows:
        team = db.get(Team, r.team_id)
        project = db.get(Project, team.project_id) if team else None
        if team is None or project is None or project.tenant_id != identity.tenant_id:
            continue
        out.append(_serialize(db, r, team=team, project=project))
    return out


@router.get("/pending", response_model=list[AccessRequestOut])
def pending_access_requests(
    identity: ResolvedIdentity = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(AccessRequest)
        .where(AccessRequest.status == AccessRequestStatus.pending)
        .order_by(AccessRequest.requested_at.desc())
    ).scalars().all()
    out: list[AccessRequestOut] = []
    for r in rows:
        team = db.get(Team, r.team_id)
        project = db.get(Project, team.project_id) if team else None
        if team is None or project is None or project.tenant_id != identity.tenant_id:
            continue
        if not has_permission(
            db, identity.user_id, "approve_access_request", team.team_id, team.project_id
        ):
            continue
        out.append(_serialize(db, r, team=team, project=project))
    return out


def _decide(db: Session, identity: ResolvedIdentity, request_id: uuid.UUID, *, approve: bool) -> AccessRequestOut:
    r = db.get(AccessRequest, request_id)
    team = db.get(Team, r.team_id) if r else None
    project = db.get(Project, team.project_id) if team else None
    if r is None or team is None or project is None or project.tenant_id != identity.tenant_id:
        raise HTTPException(status_code=404, detail="Request not found")
    if not has_permission(
        db, identity.user_id, "approve_access_request", team.team_id, team.project_id
    ):
        raise HTTPException(
            status_code=403, detail=f"You cannot review access requests for team '{team.name}'"
        )
    if r.status != AccessRequestStatus.pending:
        raise HTTPException(status_code=409, detail=f"This request has already been {r.status.value}.")

    now = datetime.now(timezone.utc)
    r.status = AccessRequestStatus.approved if approve else AccessRequestStatus.denied
    r.decided_by = identity.user_id
    r.decided_at = now
    r.expires_at = now + timedelta(days=_GRANT_TTL_DAYS) if approve else None
    record_audit(
        db, actor_id=identity.user_id,
        action="APPROVE_ACCESS_REQUEST" if approve else "DENY_ACCESS_REQUEST",
        resource_type="access_request", resource_id=r.request_id,
        details={"team": team.name, "requester_id": str(r.user_id)},
    )
    db.commit()
    db.refresh(r)
    return _serialize(db, r, team=team, project=project)


@router.post("/{request_id}/approve", response_model=AccessRequestOut)
def approve_access_request(
    request_id: uuid.UUID,
    identity: ResolvedIdentity = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _decide(db, identity, request_id, approve=True)


@router.post("/{request_id}/deny", response_model=AccessRequestOut)
def deny_access_request(
    request_id: uuid.UUID,
    identity: ResolvedIdentity = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _decide(db, identity, request_id, approve=False)
