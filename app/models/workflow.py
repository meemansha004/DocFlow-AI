import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class WorkflowStatus(str, enum.Enum):
    """
    A document's position in the approval lifecycle:
      draft -> pending_review -> approved | rejected
    Only documents in a stage with requires_approval=True are meant to move
    past 'draft'; the policy that decides that lives in the persistence /
    action layer (Phase 2), not on this model.
    """
    draft = "draft"
    pending_review = "pending_review"
    approved = "approved"
    rejected = "rejected"


class WorkflowState(Base):
    """
    Approval state for a single document — at most one row per document
    (enforced by the unique constraint on document_id). Adopted from the
    teammate's `workflow_state` table, rebuilt to our conventions (surrogate
    UUID PK like the other auxiliary tables, timezone-aware timestamps).
    """
    __tablename__ = "workflow_state"
    __table_args__ = (
        UniqueConstraint("document_id", name="uq_workflow_state_document"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.document_id"), nullable=False
    )
    state: Mapped[WorkflowStatus] = mapped_column(
        Enum(WorkflowStatus, name="workflow_status"),
        default=WorkflowStatus.draft,
        nullable=False,
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=True
    )
    approval_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
