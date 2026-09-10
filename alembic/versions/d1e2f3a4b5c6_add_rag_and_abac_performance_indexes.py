"""add rag and abac performance indexes

Revision ID: d1e2f3a4b5c6
Revises: c9d1e2f3a4b5
Create Date: 2026-09-11 00:00:00.000000

Adds critical performance indexes for batch ABAC evaluation, candidate document
filtering, and stage requirements lookup in DocFlow AI.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, None] = "c9d1e2f3a4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. User team memberships: (user_id, project_id) and (team_id)
    op.create_index(
        "idx_utm_user_project",
        "user_team_memberships",
        ["user_id", "project_id"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_utm_team_id",
        "user_team_memberships",
        ["team_id"],
        if_not_exists=True,
    )

    # 2. Document team visibility: (document_id) and (team_id, document_id)
    op.create_index(
        "idx_dtv_doc_id",
        "document_team_visibility",
        ["document_id"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_dtv_team_doc",
        "document_team_visibility",
        ["team_id", "document_id"],
        if_not_exists=True,
    )

    # 3. Documents: (project_id, tenant_id) and (stage_id)
    op.create_index(
        "idx_docs_project_tenant",
        "documents",
        ["project_id", "tenant_id"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_docs_stage_id",
        "documents",
        ["stage_id"],
        if_not_exists=True,
    )

    # 4. Team stage access: (team_id, stage_id) and (stage_id)
    op.create_index(
        "idx_tsa_team_stage",
        "team_stage_access",
        ["team_id", "stage_id"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_tsa_stage_id",
        "team_stage_access",
        ["stage_id"],
        if_not_exists=True,
    )

    # 5. Required documents: (stage_id)
    op.create_index(
        "idx_req_docs_stage_id",
        "required_documents",
        ["stage_id"],
        if_not_exists=True,
    )

    # 6. Access requests: (user_id, team_id, status)
    op.create_index(
        "idx_access_req_user_team_status",
        "access_requests",
        ["user_id", "team_id", "status"],
        if_not_exists=True,
    )

    # 7. Workflow state: (document_id)
    op.create_index(
        "idx_wf_state_doc_id",
        "workflow_state",
        ["document_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("idx_wf_state_doc_id", table_name="workflow_state")
    op.drop_index("idx_access_req_user_team_status", table_name="access_requests")
    op.drop_index("idx_req_docs_stage_id", table_name="required_documents")
    op.drop_index("idx_tsa_stage_id", table_name="team_stage_access")
    op.drop_index("idx_tsa_team_stage", table_name="team_stage_access")
    op.drop_index("idx_docs_stage_id", table_name="documents")
    op.drop_index("idx_docs_project_tenant", table_name="documents")
    op.drop_index("idx_dtv_team_doc", table_name="document_team_visibility")
    op.drop_index("idx_dtv_doc_id", table_name="document_team_visibility")
    op.drop_index("idx_utm_team_id", table_name="user_team_memberships")
    op.drop_index("idx_utm_user_project", table_name="user_team_memberships")
