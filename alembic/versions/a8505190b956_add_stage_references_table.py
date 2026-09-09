"""add stage_references table

Revision ID: a8505190b956
Revises: c7f1a9e34b21
Create Date: 2026-09-09 13:36:30.361297

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a8505190b956'
down_revision: Union[str, None] = 'c7f1a9e34b21'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # One-way structural links between stages (A references B). Same-project
    # validity and the no-self-reference rule are enforced in the API; the CHECK
    # constraint is defense-in-depth, the unique constraint blocks duplicates.
    bind = op.get_bind()
    if "stage_references" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        'stage_references',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('stage_id', sa.UUID(), nullable=False),
        sa.Column('references_stage_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()')),
        sa.CheckConstraint('stage_id <> references_stage_id', name='ck_stage_reference_not_self'),
        sa.ForeignKeyConstraint(['references_stage_id'], ['stages.stage_id']),
        sa.ForeignKeyConstraint(['stage_id'], ['stages.stage_id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('stage_id', 'references_stage_id', name='uq_stage_reference'),
    )
    op.create_index('ix_stage_references_stage_id', 'stage_references', ['stage_id'])


def downgrade() -> None:
    bind = op.get_bind()
    if "stage_references" in sa.inspect(bind).get_table_names():
        op.drop_table('stage_references')
