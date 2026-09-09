"""add users.full_name

Revision ID: c7f1a9e34b21
Revises: 78b19a88d136
Create Date: 2026-09-08 08:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7f1a9e34b21'
down_revision: Union[str, None] = '78b19a88d136'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Human-readable name shown in the admin user directory. Nullable: users
    # created before this (and invite-created users who never supplied one)
    # simply have no name and the UI falls back to the email.
    bind = op.get_bind()
    existing = {col["name"] for col in sa.inspect(bind).get_columns("users")}
    if "full_name" not in existing:
        op.add_column('users', sa.Column('full_name', sa.String(length=255), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing = {col["name"] for col in sa.inspect(bind).get_columns("users")}
    if "full_name" in existing:
        op.drop_column('users', 'full_name')
