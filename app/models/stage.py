import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Stage(Base):
    """
    A lifecycle stage within a project (e.g. Intake, Requirements, Design...).
    Configurable per-project, not a fixed global list — template-seeded at
    project creation, then freely editable (rename/reorder/add/delete).

    order_index is FUNCTIONAL (drives dashboard progress-stepper + future
    gap-detection sequencing), not just cosmetic.

    deleted_at implements soft-delete: when a stage is deleted, documents
    must first be reassigned to another stage, then this stage is
    soft-deleted (not physically removed) to preserve historical references.
    """
    __tablename__ = "stages"

    stage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.project_id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False)
    # Stage-gate for the document approval workflow (§3/4 crossover): documents
    # in a stage with this set must be explicitly submitted and approved before
    # they count as done. Default False keeps approval opt-in per stage.
    requires_approval: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class StageReference(Base):
    """
    A ONE-WAY structural link: stage `stage_id` references stage
    `references_stage_id`. RAG retrieval scoped to a stage can then also pull
    content from the stages it references (e.g. "Testing" -> "Requirements").

    Directional: A -> B does NOT imply B -> A. Both stages MUST belong to the
    same project (enforced in the API, not by FK). A stage cannot reference
    itself (DB CHECK + API guard). No duplicate (stage_id, references_stage_id)
    pairs (unique constraint).

    This REPLACES the never-used `document_stage_references` concept — that
    table stays in the schema, unused, nothing new is built against it.
    """
    __tablename__ = "stage_references"
    __table_args__ = (
        UniqueConstraint("stage_id", "references_stage_id", name="uq_stage_reference"),
        CheckConstraint(
            "stage_id <> references_stage_id", name="ck_stage_reference_not_self"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    stage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stages.stage_id"), nullable=False
    )
    references_stage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stages.stage_id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class TeamStageAccess(Base):
    """
    Grants a team access to a stage: uploading documents as this team to this
    stage, and seeing this stage at all, requires a row here. No row for a
    (team_id, stage_id) pair == that team has no access to that stage.

    org_admin / project_admin BYPASS this check entirely (same bypass pattern
    as has_permission() elsewhere) — see
    app.services.access_control.has_stage_access() and
    .get_accessible_stages_for_user(). An empty table for a project means no
    regular user can upload to or see any stage in it until a project_admin
    grants access.
    """
    __tablename__ = "team_stage_access"
    __table_args__ = (
        UniqueConstraint("team_id", "stage_id", name="uq_team_stage_access"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.team_id"), nullable=False
    )
    stage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stages.stage_id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
