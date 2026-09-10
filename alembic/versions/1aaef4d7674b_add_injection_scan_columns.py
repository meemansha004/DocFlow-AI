"""add injection scan columns to document_scans

Revision ID: 1aaef4d7674b
Revises: 8729e420600d
Create Date: 2026-09-11 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '1aaef4d7674b'
down_revision: Union[str, None] = '8729e420600d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Injection Scanner (third Scanner check, alongside score/reform) —
    # separate axis from structural quality, gates `indexed` status.
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("document_scans")}
    if "injection_flagged" not in columns:
        op.add_column(
            "document_scans",
            sa.Column("injection_flagged", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if "injection_findings" not in columns:
        op.add_column(
            "document_scans",
            sa.Column("injection_findings", postgresql.JSONB(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("document_scans")}
    if "injection_findings" in columns:
        op.drop_column("document_scans", "injection_findings")
    if "injection_flagged" in columns:
        op.drop_column("document_scans", "injection_flagged")
