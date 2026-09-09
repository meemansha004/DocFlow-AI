"""add team_stage_access table

Revision ID: 8729e420600d
Revises: a8505190b956
Create Date: 2026-09-10 03:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8729e420600d'
down_revision: Union[str, None] = 'a8505190b956'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Grants a team access to a stage. No row for a (team, stage) pair means
    # that team has no access — enforced in the API (create_document upload
    # gate + stage-visibility filtering), org_admin/project_admin bypass.
    bind = op.get_bind()
    if "team_stage_access" in sa.inspect(bind).get_table_names():
        return
    op.create_table(
        'team_stage_access',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('team_id', sa.UUID(), nullable=False),
        sa.Column('stage_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('now()')),
        sa.ForeignKeyConstraint(['team_id'], ['teams.team_id']),
        sa.ForeignKeyConstraint(['stage_id'], ['stages.stage_id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('team_id', 'stage_id', name='uq_team_stage_access'),
    )
    op.create_index('ix_team_stage_access_stage_id', 'team_stage_access', ['stage_id'])
    op.create_index('ix_team_stage_access_team_id', 'team_stage_access', ['team_id'])


def downgrade() -> None:
    bind = op.get_bind()
    if "team_stage_access" in sa.inspect(bind).get_table_names():
        op.drop_table('team_stage_access')
