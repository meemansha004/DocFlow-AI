import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Boolean, DateTime, ForeignKey, Enum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RequirementSource(str, enum.Enum):
    """Traceability only — template-origin items are just as editable as custom ones."""
    template = "template"
    custom = "custom"


class RequiredDocument(Base):
    """
    A required-document checklist item for a stage. Hybrid model: template
    seeds a starting checklist, but every item (template or custom) is fully
    editable. is_mandatory drives the coverage ratio shown on dashboards
    (e.g. "Requirements: 3/4 required docs present" counts only mandatory items).
    """
    __tablename__ = "required_documents"

    requirement_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    stage_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("stages.stage_id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    is_mandatory: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    source: Mapped[RequirementSource] = mapped_column(
        Enum(RequirementSource, name="requirement_source"),
        default=RequirementSource.custom,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
