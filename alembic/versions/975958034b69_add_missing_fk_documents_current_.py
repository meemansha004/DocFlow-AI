"""add missing fk_documents_current_version_id

Revision ID: 975958034b69
Revises: 22248ea163cd
Create Date: 2026-08-24 17:34:04.899587

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '975958034b69'
down_revision: Union[str, None] = '22248ea163cd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
     op.create_foreign_key(
        'fk_documents_current_version_id',
        'documents', 'document_versions',
        ['current_version_id'], ['version_id'],
        use_alter=True
    )
    


def downgrade() -> None:
    op.drop_constraint('fk_documents_current_version_id', 'documents', type_='foreignkey')
