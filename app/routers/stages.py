"""
Stage management (create / edit / soft-delete), per the Stage model's design
intent — until now stages only ever existed via raw SQL.

  GET    /projects/{project_id}/stages              list active stages (+ doc counts)
  POST   /projects/{project_id}/stages              create a stage           (project_admin+)
  PATCH  /projects/{project_id}/stages/{stage_id}   rename / reorder / toggle requires_approval
                                                                             (project_admin+)
  DELETE /projects/{project_id}/stages/{stage_id}   soft-delete (deleted_at)  (project_admin+)
                                                    blocked while the stage still
                                                    owns documents unless
                                                    ?reassign_to=<stage_id> is given

"project_admin+" = org_admin (tenant-wide) or a ProjectAdmin scope on this
project. Team leads do NOT manage stages (stages are project-wide, not
team-scoped).

order_index is kept dense (0..n-1) after every mutation.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user
from app.database import get_db
from app.models.document import Document
from app.models.project import Project
from app.models.stage import Stage, StageReference
from app.services.audit import record_audit
from app.services.auth import ResolvedIdentity

router = APIRouter(prefix="/projects", tags=["stages"])


# --- request / response models ----------------------------------------------

class CreateStageRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    # Where to insert it. Omitted / out of range -> appended to the end.
    order_index: int | None = Field(default=None, ge=0)


class UpdateStageRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    order_index: int | None = Field(default=None, ge=0)
    requires_approval: bool | None = None


class SetStageReferencesRequest(BaseModel):
    # The FULL desired set of stages this stage references (replace, not append).
    references: list[uuid.UUID] = Field(default_factory=list)


class StageOut(BaseModel):
    stage_id: str
    project_id: str
    name: str
    order_index: int
    requires_approval: bool
    document_count: int
    # Stage IDs this stage references (one-way, same project). Drives RAG scope.
    references: list[str] = Field(default_factory=list)


# --- helpers ---------------------------------------------------------------

def _load_project(db: Session, identity: ResolvedIdentity, project_id: uuid.UUID) -> Project:
    project = db.get(Project, project_id)
    if project is None or project.tenant_id != identity.tenant_id:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _can_see_project(db: Session, identity: ResolvedIdentity, project_id: uuid.UUID) -> bool:
    if identity.is_org_admin or project_id in identity.project_admin_project_ids:
        return True
    return any(m.project_id == project_id for m in identity.team_memberships)


def _require_project_admin(identity: ResolvedIdentity, project_id: uuid.UUID) -> None:
    if identity.is_org_admin or project_id in identity.project_admin_project_ids:
        return
    raise HTTPException(
        status_code=403,
        detail="Only project admins (or organization admins) can manage stages.",
    )


def _active_stages(db: Session, project_id: uuid.UUID) -> list[Stage]:
    return db.execute(
        select(Stage)
        .where(Stage.project_id == project_id, Stage.deleted_at.is_(None))
        .order_by(Stage.order_index, Stage.created_at)
    ).scalars().all()


def _renumber(stages: list[Stage]) -> None:
    """Assign dense 0..n-1 order_index in list order."""
    for i, stage in enumerate(stages):
        if stage.order_index != i:
            stage.order_index = i


def _doc_counts(db: Session, project_id: uuid.UUID) -> dict[uuid.UUID, int]:
    rows = db.execute(
        select(Document.stage_id, func.count())
        .where(Document.project_id == project_id)
        .group_by(Document.stage_id)
    ).all()
    return {sid: n for sid, n in rows}


def _refs_by_stage(db: Session, stage_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[uuid.UUID]]:
    """{stage_id: [referenced stage_id, ...]} for the given stages."""
    out: dict[uuid.UUID, list[uuid.UUID]] = {sid: [] for sid in stage_ids}
    if not stage_ids:
        return out
    for r in db.execute(
        select(StageReference).where(StageReference.stage_id.in_(stage_ids))
    ).scalars():
        out.setdefault(r.stage_id, []).append(r.references_stage_id)
    return out


def _serialize(stage: Stage, doc_count: int, refs: list[uuid.UUID] | None = None) -> StageOut:
    return StageOut(
        stage_id=str(stage.stage_id),
        project_id=str(stage.project_id),
        name=stage.name,
        order_index=stage.order_index,
        requires_approval=stage.requires_approval,
        document_count=doc_count,
        references=[str(x) for x in (refs or [])],
    )


# --- endpoints -----------------------------------------------------------

@router.get("/{project_id}/stages", response_model=list[StageOut])
def list_stages(
    project_id: uuid.UUID,
    identity: ResolvedIdentity = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _load_project(db, identity, project_id)
    if not _can_see_project(db, identity, project_id):
        raise HTTPException(status_code=403, detail="You do not have access to this project")
    counts = _doc_counts(db, project_id)
    stages = _active_stages(db, project_id)
    refs = _refs_by_stage(db, [s.stage_id for s in stages])
    return [
        _serialize(s, counts.get(s.stage_id, 0), refs.get(s.stage_id, []))
        for s in stages
    ]


@router.post("/{project_id}/stages", response_model=StageOut, status_code=201)
def create_stage(
    project_id: uuid.UUID,
    body: CreateStageRequest,
    identity: ResolvedIdentity = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _load_project(db, identity, project_id)
    _require_project_admin(identity, project_id)

    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Stage name cannot be empty")

    stages = _active_stages(db, project_id)
    if any(s.name.lower() == name.lower() for s in stages):
        raise HTTPException(status_code=409, detail=f"A stage named '{name}' already exists")

    pos = body.order_index if body.order_index is not None else len(stages)
    pos = max(0, min(pos, len(stages)))

    stage = Stage(project_id=project_id, name=name, order_index=pos,
                  requires_approval=False)
    db.add(stage)
    stages.insert(pos, stage)
    _renumber(stages)
    db.flush()

    record_audit(
        db, actor_id=identity.user_id, action="CREATE_STAGE", resource_type="stage",
        resource_id=stage.stage_id,
        details={"project_id": str(project_id), "name": name, "order_index": stage.order_index},
    )
    db.commit()
    db.refresh(stage)
    return _serialize(stage, 0, [])


@router.patch("/{project_id}/stages/{stage_id}", response_model=StageOut)
def update_stage(
    project_id: uuid.UUID,
    stage_id: uuid.UUID,
    body: UpdateStageRequest,
    identity: ResolvedIdentity = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _load_project(db, identity, project_id)
    _require_project_admin(identity, project_id)

    stages = _active_stages(db, project_id)
    stage = next((s for s in stages if s.stage_id == stage_id), None)
    if stage is None:
        raise HTTPException(status_code=404, detail="Stage not found")

    changed: dict = {}

    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Stage name cannot be empty")
        if any(s.stage_id != stage_id and s.name.lower() == name.lower() for s in stages):
            raise HTTPException(status_code=409, detail=f"A stage named '{name}' already exists")
        if name != stage.name:
            changed["name"] = name
            stage.name = name

    if body.requires_approval is not None and body.requires_approval != stage.requires_approval:
        stage.requires_approval = body.requires_approval
        changed["requires_approval"] = body.requires_approval

    if body.order_index is not None:
        target = max(0, min(body.order_index, len(stages) - 1))
        current = stages.index(stage)
        if target != current:
            stages.pop(current)
            stages.insert(target, stage)
            _renumber(stages)
            changed["order_index"] = stage.order_index

    if not changed:
        counts = _doc_counts(db, project_id)
        return _serialize(stage, counts.get(stage_id, 0),
                          _refs_by_stage(db, [stage_id])[stage_id])

    record_audit(
        db, actor_id=identity.user_id, action="UPDATE_STAGE", resource_type="stage",
        resource_id=stage.stage_id, details={"project_id": str(project_id), **{
            k: str(v) for k, v in changed.items()
        }},
    )
    db.commit()
    db.refresh(stage)
    counts = _doc_counts(db, project_id)
    return _serialize(stage, counts.get(stage_id, 0),
                      _refs_by_stage(db, [stage_id])[stage_id])


@router.delete("/{project_id}/stages/{stage_id}")
def delete_stage(
    project_id: uuid.UUID,
    stage_id: uuid.UUID,
    reassign_to: uuid.UUID | None = Query(
        default=None,
        description="Move this stage's documents to this stage before deleting.",
    ),
    identity: ResolvedIdentity = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _load_project(db, identity, project_id)
    _require_project_admin(identity, project_id)

    stages = _active_stages(db, project_id)
    stage = next((s for s in stages if s.stage_id == stage_id), None)
    if stage is None:
        raise HTTPException(status_code=404, detail="Stage not found")
    if len(stages) <= 1:
        raise HTTPException(status_code=409, detail="A project must keep at least one stage.")

    doc_count = db.execute(
        select(func.count()).select_from(Document).where(Document.stage_id == stage_id)
    ).scalar_one()

    reassigned = 0
    if doc_count > 0:
        if reassign_to is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"'{stage.name}' still has {doc_count} document(s). Reassign them "
                    "to another stage before deleting it."
                ),
            )
        if reassign_to == stage_id:
            raise HTTPException(status_code=422, detail="Cannot reassign a stage's documents to itself")
        target = next((s for s in stages if s.stage_id == reassign_to), None)
        if target is None:
            raise HTTPException(status_code=404, detail="Reassignment target stage not found")
        db.execute(
            Document.__table__.update()
            .where(Document.stage_id == stage_id)
            .values(stage_id=reassign_to)
        )
        reassigned = doc_count

    stage.deleted_at = datetime.now(timezone.utc)
    remaining = [s for s in stages if s.stage_id != stage_id]
    _renumber(remaining)

    # Drop any stage-reference links this stage was on either side of — a
    # deleted stage should not linger in another stage's RAG scope, and its
    # own outbound references are meaningless now.
    db.query(StageReference).filter(
        (StageReference.stage_id == stage_id)
        | (StageReference.references_stage_id == stage_id)
    ).delete(synchronize_session=False)

    record_audit(
        db, actor_id=identity.user_id, action="DELETE_STAGE", resource_type="stage",
        resource_id=stage.stage_id,
        details={
            "project_id": str(project_id), "name": stage.name,
            "reassigned_documents": reassigned,
            "reassigned_to": str(reassign_to) if reassigned else None,
        },
    )
    db.commit()
    return {"status": "deleted", "stage_id": str(stage_id), "reassigned_documents": reassigned}


@router.put("/{project_id}/stages/{stage_id}/references", response_model=StageOut)
def set_stage_references(
    project_id: uuid.UUID,
    stage_id: uuid.UUID,
    body: SetStageReferencesRequest,
    identity: ResolvedIdentity = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Replace this stage's full set of outbound references. One-way (A->B does not
    imply B->A). Every referenced stage must be another ACTIVE stage in the SAME
    project; a stage cannot reference itself.
    """
    _load_project(db, identity, project_id)
    _require_project_admin(identity, project_id)

    stages = _active_stages(db, project_id)
    stage = next((s for s in stages if s.stage_id == stage_id), None)
    if stage is None:
        raise HTTPException(status_code=404, detail="Stage not found")

    active_ids = {s.stage_id for s in stages}
    wanted: list[uuid.UUID] = []
    for ref_id in body.references:
        if ref_id in wanted:
            continue  # dedupe silently
        if ref_id == stage_id:
            raise HTTPException(status_code=422, detail="A stage cannot reference itself")
        if ref_id not in active_ids:
            # missing, soft-deleted, or in another project — all rejected the same
            raise HTTPException(
                status_code=422,
                detail="A referenced stage must be another active stage in this project",
            )
        wanted.append(ref_id)

    current = set(_refs_by_stage(db, [stage_id])[stage_id])
    if current != set(wanted):
        db.query(StageReference).filter(StageReference.stage_id == stage_id).delete(
            synchronize_session=False
        )
        for ref_id in wanted:
            db.add(StageReference(stage_id=stage_id, references_stage_id=ref_id))
        record_audit(
            db, actor_id=identity.user_id, action="UPDATE_STAGE_REFERENCES",
            resource_type="stage", resource_id=stage_id,
            details={"project_id": str(project_id),
                     "references": ",".join(str(x) for x in wanted) or "(none)"},
        )
        db.commit()

    counts = _doc_counts(db, project_id)
    return _serialize(stage, counts.get(stage_id, 0), wanted)
