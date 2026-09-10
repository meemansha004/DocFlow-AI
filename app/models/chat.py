import uuid
from datetime import datetime, timezone

from sqlalchemy import String, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ChatSession(Base):
    """
    One chat conversation, owned by a user AND scoped to a single project.
    Persisted so a conversation can be resumed later and queried through our
    own schema (the agent layer also keeps its own in-context history in
    Agno's `ai.*` tables — this is the canonical, first-party record).

    (user_id, project_id) is the isolation boundary for conversation history:
    two users in the same project, or the same user in two projects, get
    entirely separate ChatSessions — enforced in
    app/services/chat_history.resolve_chat_session(), which refuses to hand a
    session to a caller it doesn't belong to.

    Adopted from the teammate's `chat_sessions` table, rebuilt to our
    conventions; `project_id` is our addition.
    """
    __tablename__ = "chat_sessions"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.project_id"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class ChatMessage(Base):
    """
    A single turn within a ChatSession. `role` is 'user' or 'assistant', kept
    as a plain string (matching the teammate's shape) rather than an enum,
    since the agent layer may introduce further roles later.
    """
    __tablename__ = "chat_messages"

    message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_sessions.session_id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
