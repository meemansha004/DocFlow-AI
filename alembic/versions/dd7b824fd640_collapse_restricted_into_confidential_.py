"""collapse restricted into confidential sensitivity level

Revision ID: dd7b824fd640
Revises: 522f44ffb7e4
Create Date: 2026-09-04 01:45:08.588789

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'dd7b824fd640'
down_revision: Union[str, None] = '522f44ffb7e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None    

def upgrade() -> None:
    # Postgres can't drop enum values directly — recreate the type instead.
    op.execute("ALTER TYPE sensitivity_level RENAME TO sensitivity_level_old")
    op.execute("CREATE TYPE sensitivity_level AS ENUM ('public', 'internal', 'confidential')")

    # Migrate the column, mapping any existing 'restricted' rows to 'confidential'
    op.execute("""
        ALTER TABLE documents
        ALTER COLUMN sensitivity_level TYPE sensitivity_level
        USING (
            CASE sensitivity_level::text
                WHEN 'restricted' THEN 'confidential'
                ELSE sensitivity_level::text
            END
        )::sensitivity_level
    """)

    op.execute("DROP TYPE sensitivity_level_old")


def downgrade() -> None:
    # NOTE: irreversible data loss — any row that was 'restricted' before
    # upgrade() is now 'confidential' and cannot be distinguished from a
    # genuinely-confidential row. This only restores the enum OPTION, not
    # the original classification of any specific document.
    op.execute("ALTER TYPE sensitivity_level RENAME TO sensitivity_level_new")
    op.execute("CREATE TYPE sensitivity_level AS ENUM ('public', 'internal', 'confidential', 'restricted')")
    op.execute("""
        ALTER TABLE documents
        ALTER COLUMN sensitivity_level TYPE sensitivity_level
        USING sensitivity_level::text::sensitivity_level
    """)
    op.execute("DROP TYPE sensitivity_level_new")
    