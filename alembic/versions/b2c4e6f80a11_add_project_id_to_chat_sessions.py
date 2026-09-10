"""add project_id to chat_sessions

Revision ID: b2c4e6f80a11
Revises: 1aaef4d7674b
Create Date: 2026-09-11 12:00:00.000000

chat_sessions/chat_messages existed since Phase 1 but were never written to.
The RAG Agent (Phase C) is the first real consumer. Conversation history must
be isolated per (user, project), so chat_sessions gains a required project_id.

Table is empty in every environment (never populated before now), so the
column is added NOT NULL directly with no backfill.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2c4e6f80a11"
down_revision: Union[str, None] = "1aaef4d7674b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_sessions",
        sa.Column("project_id", sa.UUID(), nullable=False),
    )
    op.create_foreign_key(
        "fk_chat_sessions_project_id",
        "chat_sessions",
        "projects",
        ["project_id"],
        ["project_id"],
    )
    op.create_index(
        "ix_chat_sessions_user_project",
        "chat_sessions",
        ["user_id", "project_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_sessions_user_project", table_name="chat_sessions")
    op.drop_constraint("fk_chat_sessions_project_id", "chat_sessions", type_="foreignkey")
    op.drop_column("chat_sessions", "project_id")
