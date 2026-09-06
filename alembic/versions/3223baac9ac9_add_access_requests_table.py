"""add access_requests table

Revision ID: 3223baac9ac9
Revises: dd7b824fd640
Create Date: 2026-09-04 11:55:46.664370

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '3223baac9ac9'
down_revision: Union[str, None] = 'dd7b824fd640'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('access_requests',
    sa.Column('request_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('team_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.Enum('pending', 'approved', 'denied', name='access_request_status'), nullable=False),
    sa.Column('requested_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('decided_by', sa.UUID(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.user_id'], ),
    sa.ForeignKeyConstraint(['team_id'], ['teams.team_id'], ),
    sa.ForeignKeyConstraint(['decided_by'], ['users.user_id'], ),
    sa.PrimaryKeyConstraint('request_id')
    )


def downgrade() -> None:
    op.drop_table('access_requests')
    op.execute('DROP TYPE IF EXISTS access_request_status')