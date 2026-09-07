"""add workflow_state table and stage requires_approval

Revision ID: aa21bf6ddacd
Revises: 58ded334f2a0
Create Date: 2026-09-07 09:26:23.385618

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'aa21bf6ddacd'
down_revision: Union[str, None] = '58ded334f2a0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'workflow_state',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('document_id', sa.UUID(), nullable=False),
        sa.Column(
            'state',
            sa.Enum('draft', 'pending_review', 'approved', 'rejected', name='workflow_status'),
            nullable=False,
        ),
        sa.Column('approved_by', sa.UUID(), nullable=True),
        sa.Column('approval_timestamp', sa.DateTime(timezone=True), nullable=True),
        sa.Column('rejection_reason', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['document_id'], ['documents.document_id'], ),
        sa.ForeignKeyConstraint(['approved_by'], ['users.user_id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('document_id', name='uq_workflow_state_document'),
    )

    # Stage-gate flag for the approval workflow. Add with a server_default so
    # existing rows get False, then drop the default to match the model
    # (which has no server_default).
    op.add_column(
        'stages',
        sa.Column('requires_approval', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column('stages', 'requires_approval', server_default=None)


def downgrade() -> None:
    op.drop_column('stages', 'requires_approval')
    op.drop_table('workflow_state')
    op.execute('DROP TYPE IF EXISTS workflow_status')
