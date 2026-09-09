"""
Stage scope resolution for RAG retrieval (Phase A infrastructure).

resolve_stage_scope(db, stage_id) -> the set of stage IDs whose content is
in scope when retrieval is scoped to `stage_id`: the stage itself plus every
stage it directly references (see app/models/stage.py::StageReference).

ONE LEVEL ONLY — NOT transitive. If A references B and B references C, then
resolve_stage_scope(A) is {A, B}, NOT {A, B, C}. Transitive resolution is
deliberately not built; whether retrieval should follow reference chains is an
open question to confirm before implementing.

Nothing consumes this yet — Phase C retrieval will. Built and tested now.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.stage import Stage, StageReference


def resolve_stage_scope(db: Session, stage_id: uuid.UUID) -> list[uuid.UUID]:
    """
    Returns [stage_id] + [directly referenced stage_ids], deduplicated, with the
    stage itself first. Returns [] if `stage_id` is not a real stage.

    Referenced stages that were soft-deleted do not appear here: the stage
    router drops StageReference rows on either side of a deleted stage, so the
    join below never sees them. (If that invariant ever changes, add an explicit
    `Stage.deleted_at.is_(None)` filter on the referenced side.)
    """
    stage = db.get(Stage, stage_id)
    if stage is None:
        return []

    referenced = db.execute(
        select(StageReference.references_stage_id).where(
            StageReference.stage_id == stage_id
        )
    ).scalars().all()

    scope: list[uuid.UUID] = [stage_id]
    for ref_id in referenced:
        if ref_id not in scope:
            scope.append(ref_id)
    return scope
