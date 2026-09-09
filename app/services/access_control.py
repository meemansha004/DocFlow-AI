"""
Core ABAC/RBAC permission functions.

has_permission() — the RBAC-style rank comparison, cumulative hierarchy,
with org_admin/project_admin full bypass and the contributor confidential-
access-grant exception.

can_view_document() — combines has_permission with document-specific
sensitivity + team-visibility checks (the ABAC layer wrapping the RBAC core).

build_access_filter() — compound filter for listing/searching documents.
"""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.team import UserTeamMembership, ProjectAdmin, TeamRole, AccessRequest, AccessRequestStatus
from app.models.document import Document, DocumentTeamVisibility, SensitivityLevel
from app.models.stage import Stage, TeamStageAccess


# Rank order — higher index = more privileged. Used for cumulative comparison.
_TEAM_ROLE_RANK = {
    TeamRole.viewer: 0,
    TeamRole.contributor: 1,
    TeamRole.team_lead: 2,
}

# Minimum TeamRole required per action (actions gated at team level).
_ACTION_MIN_ROLE = {
    "view": TeamRole.viewer,
    "upload": TeamRole.contributor,
    "edit": TeamRole.contributor,
    # Document approval workflow (MERGE_DECISIONS §3/4): submit is the same bar
    # as upload; approve/reject require team_lead+ on that document's team.
    "submit": TeamRole.contributor,
    "approve": TeamRole.team_lead,
    "reject": TeamRole.team_lead,
    "manage_team_members": TeamRole.team_lead,
    "approve_access_request": TeamRole.team_lead,
}

def _is_org_admin(db: Session, user_id: UUID) -> bool:
    user = db.get(User, user_id)
    return bool(user and user.is_org_admin)


def _is_project_admin(db: Session, user_id: UUID, project_id: UUID) -> bool:
    stmt = select(ProjectAdmin).where(
        ProjectAdmin.user_id == user_id, ProjectAdmin.project_id == project_id
    )
    return db.execute(stmt).scalar_one_or_none() is not None


def _get_team_membership(db: Session, user_id: UUID, team_id: UUID) -> UserTeamMembership | None:
    stmt = select(UserTeamMembership).where(
        UserTeamMembership.user_id == user_id, UserTeamMembership.team_id == team_id
    )
    return db.execute(stmt).scalar_one_or_none()


def has_permission(db: Session, user_id: UUID, action: str, team_id: UUID, project_id: UUID) -> bool:
    """
    Core rank-comparison check, cumulative hierarchy, with full bypass
    for org_admin (tenant-wide) and project_admin (project-wide).
    """
    if _is_org_admin(db, user_id):
        return True
    if _is_project_admin(db, user_id, project_id):
        return True

    membership = _get_team_membership(db, user_id, team_id)
    if membership is None:
        return False  # no membership on this team = no access, full stop

    required_role = _ACTION_MIN_ROLE.get(action)
    if required_role is None:
        raise ValueError(f"Unknown action: {action}")

    return _TEAM_ROLE_RANK[membership.role] >= _TEAM_ROLE_RANK[required_role]


def has_stage_access(db: Session, user_id: UUID, team_id: UUID, stage_id: UUID, project_id: UUID) -> bool:
    """
    Phase A Part 3: does `team_id` have a team_stage_access grant for
    `stage_id`? org_admin/project_admin BYPASS entirely — same bypass
    pattern as has_permission(). No grant row == no access, full stop
    (there is no rank/hierarchy here; access is per (team, stage), not
    cumulative).

    Used as a THIRD gate in create_document(), alongside (not replacing)
    has_permission()'s role/team-project check.
    """
    if _is_org_admin(db, user_id):
        return True
    if _is_project_admin(db, user_id, project_id):
        return True

    stmt = select(TeamStageAccess).where(
        TeamStageAccess.team_id == team_id, TeamStageAccess.stage_id == stage_id
    )
    return db.execute(stmt).scalar_one_or_none() is not None


def get_accessible_stages_for_user(db: Session, user_id: UUID, project_id: UUID) -> list[UUID]:
    """
    The set of stage_ids `user_id` may see/upload to within `project_id`:
    the UNION of stages accessible via ANY of their team memberships in this
    project, via team_stage_access. org_admin/project_admin bypass — every
    active stage in the project, unfiltered.

    Used to filter stage visibility (Sources panel, stage dropdowns) for
    regular users, and is the resolver Phase C retrieval will consume to
    scope RAG to what a user is allowed to see.
    """
    if _is_org_admin(db, user_id) or _is_project_admin(db, user_id, project_id):
        return list(db.execute(
            select(Stage.stage_id).where(
                Stage.project_id == project_id, Stage.deleted_at.is_(None)
            )
        ).scalars())

    team_ids = list(db.execute(
        select(UserTeamMembership.team_id).where(
            UserTeamMembership.user_id == user_id,
            UserTeamMembership.project_id == project_id,
        )
    ).scalars())
    if not team_ids:
        return []

    return list(db.execute(
        select(TeamStageAccess.stage_id)
        .join(Stage, Stage.stage_id == TeamStageAccess.stage_id)
        .where(TeamStageAccess.team_id.in_(team_ids), Stage.deleted_at.is_(None))
        .distinct()
    ).scalars())


def _has_active_confidential_grant(db: Session, user_id: UUID, team_id: UUID) -> bool:
    stmt = select(AccessRequest).where(
        AccessRequest.user_id == user_id,
        AccessRequest.team_id == team_id,
        AccessRequest.status == AccessRequestStatus.approved,
    )
    grant = db.execute(stmt).scalar_one_or_none()
    if grant is None:
        return False
    if grant.expires_at and grant.expires_at < datetime.now(timezone.utc):
        return False  # expired — treated as no grant
    return True


def can_view_document(db: Session, user_id: UUID, document: Document) -> bool:
    """
    Full ABAC check for viewing a specific document: bypass, team
    visibility, and sensitivity clearance combined.
    """
    if _is_org_admin(db, user_id):
        return True
    if _is_project_admin(db, user_id, document.project_id):
        return True

    # Team visibility — document must be visible to a team the user belongs to
    visible_team_ids = {
        row.team_id for row in db.execute(
            select(DocumentTeamVisibility).where(DocumentTeamVisibility.document_id == document.document_id)
        ).scalars()
    }
    membership = None
    for team_id in visible_team_ids:
        m = _get_team_membership(db, user_id, team_id)
        if m is not None:
            membership = m
            break

    if membership is None:
        return False  # not on any team this document is visible to

    # Sensitivity clearance
    if document.sensitivity_level in (SensitivityLevel.public, SensitivityLevel.internal):
        return True  # viewer+ can always see these

    # confidential tier
    if _TEAM_ROLE_RANK[membership.role] >= _TEAM_ROLE_RANK[TeamRole.team_lead]:
        return True  # team_lead+ sees confidential automatically

    if membership.role == TeamRole.contributor:
        return _has_active_confidential_grant(db, user_id, membership.team_id)

    return False  # viewer, no grant path


def build_access_filter(db: Session, user_id: UUID, project_id: UUID):
    """
    Returns a SQLAlchemy filter condition for querying documents within a
    project — the COARSE narrowing only: tenant, project, and team-visibility.
    Sensitivity is deliberately NOT filtered here; that decision belongs
    entirely to can_view_document()'s per-row check (team_lead+ sees
    confidential automatically, contributor only with an active grant, viewer
    never). Callers must still run each returned row through can_view_document().

    For org_admin/project_admin, returns a filter scoped only to
    tenant/project (full visibility within that scope).
    """
    from sqlalchemy import and_

    user = db.get(User, user_id)

    if user.is_org_admin:
        return Document.tenant_id == user.tenant_id

    if _is_project_admin(db, user_id, project_id):
        return and_(Document.tenant_id == user.tenant_id, Document.project_id == project_id)

    # Regular user: must be on a team the document is visible to
    user_team_ids = [
        row.team_id for row in db.execute(
            select(UserTeamMembership).where(
                UserTeamMembership.user_id == user_id,
                UserTeamMembership.project_id == project_id,
            )
        ).scalars()
    ]
    if not user_team_ids:
        return Document.document_id == None  # no access — matches nothing

    visible_doc_ids_subquery = (
        select(DocumentTeamVisibility.document_id)
        .where(DocumentTeamVisibility.team_id.in_(user_team_ids))
    )

    return and_(
        Document.tenant_id == user.tenant_id,
        Document.project_id == project_id,
        Document.document_id.in_(visible_doc_ids_subquery),
        # NOTE: no sensitivity condition — every doc visible to the user's
        # teams (public, internal AND confidential) passes through here;
        # can_view_document() makes the final per-row call.
    )

def _coerce_sensitivity(value: "SensitivityLevel | int | str") -> SensitivityLevel:
    """
    Accept a SensitivityLevel, its int value (0/1/2), or its name
    (case-insensitive, e.g. "internal") — Phase 1 made SensitivityLevel an
    IntEnum, so callers may hand us any of these forms.
    """
    if isinstance(value, SensitivityLevel):
        return value
    if isinstance(value, bool):  # bool is a subclass of int — reject explicitly
        raise ValueError(f"Invalid sensitivity level: {value!r}")
    if isinstance(value, int):
        return SensitivityLevel(value)
    if isinstance(value, str):
        try:
            return SensitivityLevel[value.strip().lower()]
        except KeyError:
            raise ValueError(f"Unknown sensitivity level: {value!r}") from None
    raise ValueError(f"Unsupported sensitivity value: {value!r}")


def resolve_sensitivity(requested: "SensitivityLevel | int | str", role: str) -> SensitivityLevel:
    """
    Caps requested sensitivity by role. viewer/contributor capped at internal;
    team_lead+/org_admin/project_admin can set confidential directly. Because
    SensitivityLevel is an IntEnum, the cap is a plain comparison.
    """
    requested_level = _coerce_sensitivity(requested)
    role_name = role.value if hasattr(role, "value") else str(role)
    if role_name in ("team_lead", "org_admin", "project_admin"):
        return requested_level
    if requested_level > SensitivityLevel.internal:
        return SensitivityLevel.internal
    return requested_level