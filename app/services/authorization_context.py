"""
Request-scoped authorization context for DocFlow AI.

Eliminates N+1 query loops across ABAC and RAG retrieval by pre-loading:
  - User identity, tenant_id, clearance, org-admin status
  - Project-admin status
  - All team memberships and roles for (user_id, project_id)
  - All accessible stage IDs
  - Active confidential access grants for the user's teams

Authoritative decisions remain in PostgreSQL. This context is strictly internal
and NEVER exposed as an argument to LLMs.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import SensitivityLevel
from app.models.project import Project
from app.models.stage import Stage, TeamStageAccess
from app.models.team import (
    AccessRequest,
    AccessRequestStatus,
    ProjectAdmin,
    TeamRole,
    UserTeamMembership,
)
from app.models.user import User


@dataclass
class AuthorizationContext:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    project_id: uuid.UUID

    is_org_admin: bool
    is_project_admin: bool

    team_ids: set[uuid.UUID] = field(default_factory=set)
    team_roles: dict[uuid.UUID, TeamRole] = field(default_factory=dict)

    accessible_stage_ids: set[uuid.UUID] = field(default_factory=set)
    clearance_level: SensitivityLevel | str | None = None
    active_confidential_grant_team_ids: set[uuid.UUID] = field(default_factory=set)

    @property
    def is_admin(self) -> bool:
        return self.is_org_admin or self.is_project_admin


def build_authorization_context(
    db: Session,
    user_id: uuid.UUID,
    project_id: uuid.UUID,
) -> AuthorizationContext:
    """
    Constructs a request-scoped AuthorizationContext for (user_id, project_id)
    in a small, bounded number of queries (maximum 4).
    """
    user = db.get(User, user_id)
    if not user:
        raise ValueError(f"User {user_id} does not exist.")

    tenant_id = user.tenant_id
    is_org_admin = bool(user.is_org_admin)
    clearance = getattr(user, "clearance_level", None)

    # 1. Project Admin check
    is_project_admin = db.execute(
        select(ProjectAdmin.id).where(
            ProjectAdmin.user_id == user_id,
            ProjectAdmin.project_id == project_id,
        ).limit(1)
    ).scalar_one_or_none() is not None

    # 2. Team memberships within this project
    memberships = db.execute(
        select(UserTeamMembership).where(
            UserTeamMembership.user_id == user_id,
            UserTeamMembership.project_id == project_id,
        )
    ).scalars().all()

    team_ids = {m.team_id for m in memberships}
    team_roles = {m.team_id: m.role for m in memberships}

    # 3. Accessible stages
    if is_org_admin or is_project_admin:
        stage_rows = db.execute(
            select(Stage.stage_id).where(
                Stage.project_id == project_id,
                Stage.deleted_at.is_(None),
            )
        ).scalars().all()
        accessible_stage_ids = set(stage_rows)
    elif team_ids:
        stage_rows = db.execute(
            select(TeamStageAccess.stage_id)
            .join(Stage, Stage.stage_id == TeamStageAccess.stage_id)
            .where(
                TeamStageAccess.team_id.in_(team_ids),
                Stage.deleted_at.is_(None),
            )
            .distinct()
        ).scalars().all()
        accessible_stage_ids = set(stage_rows)
    else:
        accessible_stage_ids = set()

    # 4. Active non-expired confidential grants for the user's teams
    active_confidential_grant_team_ids = set()
    if team_ids and not (is_org_admin or is_project_admin):
        grants = db.execute(
            select(AccessRequest).where(
                AccessRequest.user_id == user_id,
                AccessRequest.team_id.in_(team_ids),
                AccessRequest.status == AccessRequestStatus.approved,
            )
        ).scalars().all()

        now = datetime.now(timezone.utc)
        for g in grants:
            if g.expires_at is None:
                active_confidential_grant_team_ids.add(g.team_id)
            else:
                exp = g.expires_at
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if exp >= now:
                    active_confidential_grant_team_ids.add(g.team_id)

    return AuthorizationContext(
        user_id=user_id,
        tenant_id=tenant_id,
        project_id=project_id,
        is_org_admin=is_org_admin,
        is_project_admin=is_project_admin,
        team_ids=team_ids,
        team_roles=team_roles,
        accessible_stage_ids=accessible_stage_ids,
        clearance_level=clearance,
        active_confidential_grant_team_ids=active_confidential_grant_team_ids,
    )
