import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Integer, DateTime, ForeignKey, Boolean
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
