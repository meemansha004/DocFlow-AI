"""convert sensitivity_level to integer

Revision ID: 58ded334f2a0
Revises: 3223baac9ac9
Create Date: 2026-09-07 09:26:22.967258

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '58ded334f2a0'
down_revision: Union[str, None] = '3223baac9ac9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # documents.sensitivity_level: Postgres enum 'sensitivity_level' -> plain integer
    # (public=0, internal=1, confidential=2), per merge decision §1.
    op.execute("ALTER TABLE documents ALTER COLUMN sensitivity_level DROP DEFAULT")
    op.execute(
        """
        ALTER TABLE documents
        ALTER COLUMN sensitivity_level TYPE integer
        USING (
            CASE sensitivity_level::text
                WHEN 'public' THEN 0
                WHEN 'internal' THEN 1
                WHEN 'confidential' THEN 2
            END
        )
        """
    )
    # No server_default in the model (Python-side default only) — leave the
    # column with no DB default, matching the rest of the schema.
    op.execute("DROP TYPE sensitivity_level")


def downgrade() -> None:
    op.execute(
        "CREATE TYPE sensitivity_level AS ENUM ('public', 'internal', 'confidential')"
    )
    op.execute(
        """
        ALTER TABLE documents
        ALTER COLUMN sensitivity_level TYPE sensitivity_level
        USING (
            CASE sensitivity_level
                WHEN 0 THEN 'public'
                WHEN 1 THEN 'internal'
                WHEN 2 THEN 'confidential'
            END
        )::sensitivity_level
        """
    )
