"""
Project Activity — the finalized design (rebuild of the non-functional stub).

Shape:
  project cards (only projects the caller is part of)
    -> pick one team (single-select)
      -> flat, newest-first list of that team's document activity, each entry
         carrying its stage and a workflow outcome/status.

Visibility (point 6): everyone EXCEPT a viewer-only user. A project card only
appears if the caller has at least one selectable team in it (contributor or
team_lead), or administers the project (project_admin / org_admin).

Role scoping (point 8):
  - team_lead / contributor  -> their OWN team's activity only
  - project_admin (for a project they administer) / org_admin -> any team, full depth
  - a project_admin who is only a regular member of some other project is
    limited there exactly like anyone else.

Sensitivity filtering (point 9): every entry is tied to a specific document and
must still pass can_view_document() for the caller — team membership alone does
NOT bypass confidential clearance. This is the same rule GET /documents uses.

This adapts the teammate's project-scoped audit query (database.py::
get_project_activity: audit_log JOIN documents ON resource_id) and extends it
with team-level filtering, the role gate above, and the per-row
can_view_document() check.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.document import Document
from app.models.project import Project
from app.models.stage import Stage
from app.models.team import Team, TeamRole
from app.models.user import User
from app.models.workflow import WorkflowState
from app.services.access_control import can_view_document


class ActivityAccessError(Exception):
    """Caller may not view this team's activity (viewer-only / not their team) -> 403."""


class ActivityNotFound(Exception):
    """Project or team does not exist in the caller's tenant -> 404."""


# Document lifecycle actions that make up "team activity".
_DOC_ACTIONS = (
    "UPLOAD_DOCUMENT",
    "SUBMIT_DOCUMENT",
    "APPROVE_DOCUMENT",
    "REJECT_DOCUMENT",
)

_SELECTABLE_ROLES = (TeamRole.contributor, TeamRole.team_lead)


def _admin_of(identity, project_id: uuid.UUID) -> bool:
    return identity.is_org_admin or project_id in identity.project_admin_project_ids


def list_activity_projects(db: Session, identity) -> list[dict]:
    """Project cards the caller can open, each with the teams they may select."""
    candidate_ids: set[uuid.UUID] = set(identity.project_admin_project_ids)
    candidate_ids.update(m.project_id for m in identity.team_memberships)
    if identity.is_org_admin:
        candidate_ids.update(db.execute(
            select(Project.project_id).where(Project.tenant_id == identity.tenant_id)
        ).scalars())

    memberships_by_project: dict[uuid.UUID, dict[uuid.UUID, TeamRole]] = {}
    for m in identity.team_memberships:
        memberships_by_project.setdefault(m.project_id, {})[m.team_id] = m.role

    out: list[dict] = []
    for pid in candidate_ids:
        project = db.get(Project, pid)
        if project is None or project.tenant_id != identity.tenant_id:
            continue
        admin_here = _admin_of(identity, pid)
        all_teams = db.execute(
            select(Team).where(Team.project_id == pid).order_by(Team.name)
        ).scalars().all()

        if admin_here:
            selectable = all_teams
        else:
            own = memberships_by_project.get(pid, {})
            selectable = [t for t in all_teams if own.get(t.team_id) in _SELECTABLE_ROLES]

        if not selectable:
            continue  # viewer-only (or no) access here -> no card

        out.append({
            "project_id": str(pid),
            "project_name": project.name,
            "admin_here": admin_here,
            "teams": [{"team_id": str(t.team_id), "name": t.name} for t in selectable],
        })

    out.sort(key=lambda p: p["project_name"].lower())
    return out


def list_team_activity(
    db: Session,
    identity,
    project_id: uuid.UUID,
    team_id: uuid.UUID,
    limit: int = 200,
) -> list[dict]:
    """Flat, sensitivity-filtered activity feed for one team."""
    project = db.get(Project, project_id)
    if project is None or project.tenant_id != identity.tenant_id:
        raise ActivityNotFound("Project not found.")
    team = db.get(Team, team_id)
    if team is None or team.project_id != project_id:
        raise ActivityNotFound("Team not found.")

    # --- role gate ---------------------------------------------------------
    if not _admin_of(identity, project_id):
        role = next(
            (m.role for m in identity.team_memberships
             if m.team_id == team_id and m.project_id == project_id),
            None,
        )
        if role not in _SELECTABLE_ROLES:
            raise ActivityAccessError(
                "You can only view Project Activity for a team you contribute to or lead."
            )

    # --- query: audit rows JOIN the document they refer to ----------------
    rows = db.execute(
        select(AuditLog, User.email, User.full_name, Document, Stage.name)
        .join(User, User.user_id == AuditLog.user_id)
        .join(Document, Document.document_id == AuditLog.resource_id)
        .join(Stage, Stage.stage_id == Document.stage_id)
        .where(
            AuditLog.resource_type == "document",
            AuditLog.action.in_(_DOC_ACTIONS),
            Document.tenant_id == identity.tenant_id,
            Document.project_id == project_id,
            Document.uploaded_as_team_id == team_id,
        )
        .order_by(AuditLog.created_at.desc())
        .limit(max(limit * 3, limit))  # headroom: some rows drop on the sensitivity check
    ).all()

    doc_ids = {doc.document_id for _, _, _, doc, _ in rows}
    workflow_by_doc = {
        w.document_id: w for w in db.execute(
            select(WorkflowState).where(WorkflowState.document_id.in_(doc_ids))
        ).scalars()
    } if doc_ids else {}

    # can_view_document() is deterministic per (user, document) here — cache it.
    view_cache: dict[uuid.UUID, bool] = {}

    out: list[dict] = []
    for a, email, full_name, doc, stage_name in rows:
        allowed = view_cache.get(doc.document_id)
        if allowed is None:
            allowed = can_view_document(db, identity.user_id, doc)
            view_cache[doc.document_id] = allowed
        if not allowed:
            continue  # confidential doc the caller has no clearance for

        d = a.details or {}
        wf = workflow_by_doc.get(doc.document_id)
        # Per-entry outcome (point 10): the state this action produced, falling
        # back to the document's current workflow state.
        entry_status = d.get("state") or d.get("workflow_state")
        if entry_status in (None, "none"):
            entry_status = wf.state.value if wf is not None else None

        out.append({
            "log_id": str(a.log_id),
            "actor_email": email,
            "actor_name": full_name or email,
            "action": a.action,
            "document_id": str(doc.document_id),
            "filename": doc.original_filename,
            "stage": stage_name,
            "sensitivity_level": doc.sensitivity_level.name,
            "status": entry_status,
            "current_state": wf.state.value if wf is not None else None,
            "rejection_reason": (
                wf.rejection_reason if wf is not None and a.action == "REJECT_DOCUMENT" else None
            ),
            "details": _row_details_text(d),
            "created_at": a.created_at.isoformat(),
            "timestamp": a.created_at.isoformat(),
        })
        if len(out) >= limit:
            break
    return out


def _row_details_text(details: dict) -> str | None:
    if not details:
        return None
    # Drop the ids the UI already renders structurally; keep the human bits.
    hidden = {"team_id", "stage_id", "workflow_state", "state"}
    kept = {k: v for k, v in details.items() if k not in hidden}
    if not kept:
        return None
    return "; ".join(f"{k}={v}" for k, v in kept.items())
