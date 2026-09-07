"""add user local-auth columns

Revision ID: 78b19a88d136
Revises: 29c18500af5c
Create Date: 2026-09-07 09:26:24.910340

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '78b19a88d136'
down_revision: Union[str, None] = '29c18500af5c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # All nullable — Phase 2 fills these in; existing rows are untouched.
    #
    # `password_hash` already exists in some databases: it was added directly
    # (outside the migration chain) during the repo merge, as VARCHAR(255) NULL,
    # which matches the model. Add each column only if it isn't already present
    # so this migration is safe against that drift.
    bind = op.get_bind()
    existing = {col["name"] for col in sa.inspect(bind).get_columns("users")}

    if "password_hash" not in existing:
        op.add_column('users', sa.Column('password_hash', sa.String(length=255), nullable=True))
    if "reset_token" not in existing:
        op.add_column('users', sa.Column('reset_token', sa.String(length=255), nullable=True))
    if "reset_token_expires" not in existing:
        op.add_column('users', sa.Column('reset_token_expires', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    existing = {col["name"] for col in sa.inspect(bind).get_columns("users")}

    if "reset_token_expires" in existing:
        op.drop_column('users', 'reset_token_expires')
    if "reset_token" in existing:
        op.drop_column('users', 'reset_token')
    if "password_hash" in existing:
        op.drop_column('users', 'password_hash')
