"""add document_scans table

Revision ID: 522f44ffb7e4
Revises: 975958034b69
Create Date: 2026-08-24 17:38:13.019036

"""
from typing import Sequence, Union
from sqlalchemy.dialects import postgresql

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '522f44ffb7e4'
down_revision: Union[str, None] = '975958034b69'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('document_scans',
    sa.Column('scan_id', sa.UUID(), nullable=False),
    sa.Column('version_id', sa.UUID(), nullable=False),
    sa.Column('overall_score', sa.Integer(), nullable=False),
    sa.Column('criteria', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('reform_triggered', sa.Boolean(), nullable=False),
    sa.Column('reformed_content', sa.Text(), nullable=True),
    sa.Column('review_status', sa.Enum('not_required', 'pending', 'accepted', 'edited_accepted', 'rejected', name='scan_review_status'), nullable=False),
    sa.Column('edited_content', sa.Text(), nullable=True),
    sa.Column('reviewed_by', sa.UUID(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['reviewed_by'], ['users.user_id'], ),
    sa.ForeignKeyConstraint(['version_id'], ['document_versions.version_id'], ),
    sa.PrimaryKeyConstraint('scan_id')
    )


def downgrade() -> None:
    op.drop_table('document_scans')
    op.execute('DROP TYPE IF EXISTS scan_review_status')
