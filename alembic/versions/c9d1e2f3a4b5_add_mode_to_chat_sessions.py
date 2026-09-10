"""add mode to chat_sessions

Revision ID: c9d1e2f3a4b5
Revises: b2c4e6f80a11
Create Date: 2026-09-10 00:00:00.000000

The Query Agent (Phase C follow-on) is the second consumer of
chat_sessions/chat_messages, alongside the RAG Agent. Both persist through
the same first-party tables, but the Search-tab history sidebar must only
show RAG conversations. chat_sessions gains a `mode` discriminator
('rag' | 'query'); existing rows are all RAG, so the column is added with a
server default of 'rag'.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9d1e2f3a4b5"
down_revision: Union[str, None] = "b2c4e6f80a11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_sessions",
        sa.Column(
            "mode",
            sa.String(length=20),
            nullable=False,
            server_default="rag",
        ),
    )


def downgrade() -> None:
    op.drop_column("chat_sessions", "mode")
