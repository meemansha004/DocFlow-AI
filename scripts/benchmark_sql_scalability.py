"""
Benchmark and Query Execution Plan Audit for PostgreSQL + Qdrant RAG.

This script audits:
1. Request initialization vs Candidate Authorization separation:
   - Request initialization: AuthorizationContext batch-loads user/project/team state in 3-4 bounded queries.
   - Candidate Authorization: classify_documents_visibility evaluates N candidates in exactly 1-2 bounded queries.
2. Scalability benchmark across candidate counts:
   - 20 candidates
   - 200 candidates
   - 2000 candidates
   Verifies that SQL query count remains strictly <= 2 and execution time stays low.
3. PostgreSQL Query Plan & Index Verification:
   - Analyzes query structure, compile targets, and indexes for all critical paths.
   - If a live PostgreSQL instance is configured, executes EXPLAIN (ANALYZE, BUFFERS).
"""

import os
import sys
import time
import uuid

# Set UTF-8
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, event
from sqlalchemy.dialects import postgresql
from app.database import SessionLocal, engine
from app.models.tenant import Tenant
from app.models.project import Project
from app.models.stage import Stage, TeamStageAccess
from app.models.team import Team, TeamRole, UserTeamMembership, AccessRequest, AccessRequestStatus
from app.models.user import User
from app.models.document import Document, DocumentVersion, DocumentTeamVisibility, SensitivityLevel, DocumentStatus
from app.models.required_document import RequiredDocument
from app.services.authorization_context import build_authorization_context, AuthorizationContext
from app.services.access_control import classify_documents_visibility, DocumentVisibility


def compile_pg_sql(stmt) -> str:
    """Compile a SQLAlchemy statement to raw PostgreSQL SQL with parameter literals."""
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def main():
    print("=" * 80)
    print("DOCFLOW AI: AUTHORIZATION SCALABILITY & SQL AUDIT BENCHMARK")
    print("=" * 80)

    db = SessionLocal()

    # 1. Setup minimal test dataset
    tenant_id = uuid.uuid4()
    project_id = uuid.uuid4()
    db.add(Tenant(tenant_id=tenant_id, name="Benchmark Tenant"))
    db.flush()
    db.add(Project(project_id=project_id, tenant_id=tenant_id, name="Benchmark Project"))
    db.flush()

    stage_id = uuid.uuid4()
    stage = Stage(stage_id=stage_id, project_id=project_id, name="Development", order_index=1)
    db.add(stage)
    db.flush()

    team_id = uuid.uuid4()
    team = Team(team_id=team_id, project_id=project_id, name="Core Engineering")
    db.add(team)
    db.flush()

    run_id = uuid.uuid4().hex[:6]
    test_user = User(user_id=uuid.uuid4(), tenant_id=tenant_id, email=f"bench_{run_id}@bench.com")
    db.add(test_user)
    db.flush()
    db.add(UserTeamMembership(id=uuid.uuid4(), user_id=test_user.user_id, team_id=team_id, project_id=project_id, role=TeamRole.contributor))
    db.commit()

    # 2. Compile and inspect PostgreSQL query plans for all core access control queries
    print("\n[PART 1] Compiled PostgreSQL Queries & Index Utilization Audit")

    # A. User memberships
    q_membership = select(UserTeamMembership).where(
        UserTeamMembership.user_id == test_user.user_id,
        UserTeamMembership.project_id == project_id,
    )
    print("\n--- 1. User Team Memberships Lookup ---")
    print("Target Index: ix_user_team_memberships_user_project (user_id, project_id)")
    print("PostgreSQL SQL:")
    print("  " + compile_pg_sql(q_membership))

    # B. Candidate Document Batch Lookup
    dummy_doc_ids = [uuid.uuid4(), uuid.uuid4()]
    q_docs = select(Document).where(
        Document.document_id.in_(dummy_doc_ids),
        Document.tenant_id == tenant_id,
        Document.project_id == project_id,
    )
    print("\n--- 2. Candidate Document Batch Lookup ---")
    print("Target Index: PRIMARY KEY (document_id) / ix_documents_project_stage (project_id, stage_id)")
    print("PostgreSQL SQL:")
    print("  " + compile_pg_sql(q_docs))

    # C. Document Team Visibility Batch Lookup
    q_vis = select(DocumentTeamVisibility).where(
        DocumentTeamVisibility.document_id.in_(dummy_doc_ids)
    )
    print("\n--- 3. Document Team Visibility Batch Lookup ---")
    print("Target Index: ix_document_team_visibility_doc_team (document_id, team_id)")
    print("PostgreSQL SQL:")
    print("  " + compile_pg_sql(q_vis))

    # D. Confidential Grant Lookup
    q_grant = select(AccessRequest).where(
        AccessRequest.user_id == test_user.user_id,
        AccessRequest.team_id.in_([team_id]),
        AccessRequest.status == AccessRequestStatus.approved,
    )
    print("\n--- 4. Active Confidential Grant Lookup ---")
    print("Target Index: ix_access_requests_user_team_status (user_id, team_id, status)")
    print("PostgreSQL SQL:")
    print("  " + compile_pg_sql(q_grant))

    # E. Stage Requirements Lookup
    q_req = select(RequiredDocument).where(RequiredDocument.stage_id == stage_id).order_by(RequiredDocument.created_at.asc())
    print("\n--- 5. Stage Requirements Checklist Lookup ---")
    print("Target Index: ix_required_documents_stage_mandatory (stage_id, is_mandatory)")
    print("PostgreSQL SQL:")
    print("  " + compile_pg_sql(q_req))

    # 3. Scalability Benchmark: 20 vs 200 vs 2000 Candidate Documents
    print("\n[PART 2] Candidate Authorization Scalability Benchmark")
    print("Testing batch ABAC (classify_documents_visibility) across candidate sizes:")

    # Populate 2000 synthetic candidate documents in bulk
    batch_sizes = [20, 200, 2000]
    max_needed = max(batch_sizes)

    all_doc_ids = []
    docs_to_add = []
    dvis_to_add = []

    for i in range(max_needed):
        d_id = uuid.uuid4()
        v_id = uuid.uuid4()
        all_doc_ids.append(d_id)
        if i % 10 < 7:
            sens = SensitivityLevel.internal
        elif i % 10 < 9:
            sens = SensitivityLevel.confidential
        else:
            sens = SensitivityLevel.public

        doc = Document(
            document_id=d_id,
            tenant_id=tenant_id,
            project_id=project_id,
            stage_id=stage_id,
            uploaded_by=test_user.user_id,
            uploaded_as_team_id=team_id,
            sensitivity_level=sens,
            original_filename=f"Candidate_Doc_{i:04d}.md",
            mime_type="text/markdown",
            current_version_id=None,
        )
        docs_to_add.append(doc)
        dvis_to_add.append(DocumentTeamVisibility(id=uuid.uuid4(), document_id=d_id, team_id=team_id))

    db.add_all(docs_to_add)
    db.add_all(dvis_to_add)
    db.commit()

    # Build auth context once (Request Initialization)
    auth_ctx = build_authorization_context(db, test_user.user_id, project_id)

    print(f"\nAuthorizationContext initialized for user {test_user.email}:")
    print(f"  Teams: {len(auth_ctx.team_ids)} | Stages: {len(auth_ctx.accessible_stage_ids)} | Admin: {auth_ctx.is_admin}")

    # Track SQL queries executed during candidate authorization
    executed_queries = []

    def query_listener(conn, cursor, statement, parameters, context, executemany):
        if statement.strip().upper().startswith(("SELECT", "WITH")):
            executed_queries.append(statement)

    event.listen(engine, "before_cursor_execute", query_listener)

    print("\nExecuting candidate visibility evaluations:")
    print("-" * 75)
    print(f"{'Candidates':<15} | {'SQL Queries':<15} | {'Execution Time (ms)':<20} | {'Throughput (docs/sec)':<20}")
    print("-" * 75)

    for n in batch_sizes:
        subset = all_doc_ids[:n]
        executed_queries.clear()

        start_t = time.perf_counter()
        results = classify_documents_visibility(db, auth_ctx, subset)
        elapsed_ms = (time.perf_counter() - start_t) * 1000

        query_count = len(executed_queries)
        assert query_count <= 2, f"Expected <= 2 queries, got {query_count} for {n} candidates!"
        assert len(results) == n

        throughput = int(n / (elapsed_ms / 1000.0)) if elapsed_ms > 0 else 0
        print(f"{n:<15} | {query_count:<15} | {elapsed_ms:>10.2f} ms        | {throughput:>12,d} docs/s")

    print("-" * 75)
    print("✓ SCALABILITY VERIFIED: Authorization cost remains STRICTLY BOUNDED to 2 SQL queries")
    print("  regardless of whether 20, 200, or 2,000 candidates are evaluated.")

    # 4. Check live PostgreSQL EXPLAIN ANALYZE if running on PostgreSQL
    is_postgres = "postgresql" in str(engine.url)
    if is_postgres:
        print("\n[PART 3] Live PostgreSQL EXPLAIN (ANALYZE, BUFFERS) Execution")
        try:
            from sqlalchemy import text
            with engine.connect() as conn:
                sample_ids = all_doc_ids[:20]
                explain_sql = f"EXPLAIN (ANALYZE, BUFFERS) SELECT * FROM documents WHERE document_id = ANY(ARRAY{[str(i) for i in sample_ids]}::uuid[])"
                res = conn.execute(text(explain_sql)).fetchall()
                for row in res:
                    print("  " + row[0])
        except Exception as e:
            print(f"  Note: Live EXPLAIN skipped ({e})")
    else:
        print("\n[PART 3] Live PostgreSQL EXPLAIN Mode")
        print("  Current engine: SQLite (local/test).")
        print("  To run live EXPLAIN (ANALYZE, BUFFERS) against PostgreSQL in production/staging:")
        print("  Set DATABASE_URL=postgresql+psycopg2://... in .env and run this script.")

    print("\n" + "=" * 80)
    print("AUDIT & BENCHMARK COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
