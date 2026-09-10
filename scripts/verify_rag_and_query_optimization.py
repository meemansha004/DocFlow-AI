"""
Comprehensive verification test suite for PostgreSQL + Qdrant RAG Optimization,
Request-Scoped Authorization Context, Batch ABAC, and Structured Query Tools.

Covers all test criteria from DocFlow AI — PostgreSQL + Qdrant RAG Implementation Plan.md:
  1. AuthorizationContext construction & accessible stages
  2. Batch ABAC (Admin, Viewer, Contributor, Team Lead, Multi-team, Stranger)
  3. Confidential access grants (Active vs Expired)
  4. Query Count Boundedness (Proof of NO N+1 queries)
  5. Structured Stage Requirements (get_stage_requirements)
  6. Stage Document Status & Coverage Calculation (get_stage_document_status)
  7. Qdrant Hybrid Retrieval with Batch Authorization & Cross-Encoder Reranker
  8. Malformed Qdrant Payload Fault-Tolerance
"""

from datetime import datetime, timedelta, timezone
import os
import sys
import time
import uuid

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from sqlalchemy import event, select
from app.database import Base, SessionLocal, engine
from app.models.document import Document, DocumentStatus, DocumentTeamVisibility, DocumentVersion, SensitivityLevel
from app.models.project import Project
from app.models.required_document import RequiredDocument, RequirementSource
from app.models.stage import Stage, StageReference, TeamStageAccess
from app.models.team import AccessRequest, AccessRequestStatus, ProjectAdmin, Team, TeamRole, UserTeamMembership
from app.models.tenant import Tenant
from app.models.user import User
from app.models.workflow import WorkflowState, WorkflowStatus
from app.services.access_control import (
    DocumentVisibility,
    can_view_document,
    classify_document_visibility,
    classify_documents_visibility,
    has_any_project_access,
)
from app.services.authorization_context import (
    AuthorizationContext,
    build_authorization_context,
)
from app.services.indexing import index_document, should_index
from app.services.query_context import reset_query_context, set_query_context
from app.services.rag.collection_setup import (
    collection_name_for_tenant,
    ensure_tenant_collection,
    get_qdrant_client,
)
from app.services.rag.retrieval import (
    NoProjectAccessError,
    _validate_point_payload,
    retrieve,
)
from app.tools.query_tools import (
    get_stage_document_status,
    get_stage_requirements,
)

print("=" * 80)
print("DOCFLOW AI: POSTGRESQL + QDRANT RAG & QUERY AGENT VERIFICATION SUITE")
print("=" * 80)

db = SessionLocal()
Base.metadata.create_all(bind=engine)

# Setup Test Fixtures
tenant_id = uuid.uuid4()
tenant = Tenant(tenant_id=tenant_id, name="Acme Corp")
foreign_tenant_id = uuid.uuid4()
foreign_tenant = Tenant(tenant_id=foreign_tenant_id, name="Foreign Corp")
db.add_all([tenant, foreign_tenant])
db.flush()

project_id = uuid.uuid4()
project = Project(project_id=project_id, tenant_id=tenant_id, name="DocFlow Proj")
foreign_project_id = uuid.uuid4()
foreign_project = Project(project_id=foreign_project_id, tenant_id=foreign_tenant_id, name="Foreign Proj")
db.add_all([project, foreign_project])
db.flush()

# Stages
stage_req = Stage(stage_id=uuid.uuid4(), project_id=project_id, name="Requirements", order_index=1, requires_approval=True)
stage_des = Stage(stage_id=uuid.uuid4(), project_id=project_id, name="Design", order_index=2, requires_approval=True)
stage_dev = Stage(stage_id=uuid.uuid4(), project_id=project_id, name="Development", order_index=3, requires_approval=False)
db.add_all([stage_req, stage_des, stage_dev])
db.flush()

# Directional Stage Reference: Development references Requirements and Design
ref1 = StageReference(stage_id=stage_dev.stage_id, references_stage_id=stage_req.stage_id)
ref2 = StageReference(stage_id=stage_dev.stage_id, references_stage_id=stage_des.stage_id)
db.add_all([ref1, ref2])

# Teams
team_eng = Team(team_id=uuid.uuid4(), project_id=project_id, name="Engineering")
team_sec = Team(team_id=uuid.uuid4(), project_id=project_id, name="Security")
team_prd = Team(team_id=uuid.uuid4(), project_id=project_id, name="Product")
db.add_all([team_eng, team_sec, team_prd])
db.flush()

# Team stage access grants
db.add(TeamStageAccess(id=uuid.uuid4(), team_id=team_eng.team_id, stage_id=stage_req.stage_id))
db.add(TeamStageAccess(id=uuid.uuid4(), team_id=team_eng.team_id, stage_id=stage_des.stage_id))
db.add(TeamStageAccess(id=uuid.uuid4(), team_id=team_eng.team_id, stage_id=stage_dev.stage_id))
db.add(TeamStageAccess(id=uuid.uuid4(), team_id=team_sec.team_id, stage_id=stage_dev.stage_id))
db.add(TeamStageAccess(id=uuid.uuid4(), team_id=team_prd.team_id, stage_id=stage_req.stage_id))
db.flush()

# Users
run_id = uuid.uuid4().hex[:6]
user_admin = User(user_id=uuid.uuid4(), tenant_id=tenant_id, email=f"admin_{run_id}@acme.com", is_org_admin=True)
user_proj_admin = User(user_id=uuid.uuid4(), tenant_id=tenant_id, email=f"pm_{run_id}@acme.com", is_org_admin=False)
user_alice = User(user_id=uuid.uuid4(), tenant_id=tenant_id, email=f"alice_{run_id}@acme.com", is_org_admin=False) # Team Lead
user_bob = User(user_id=uuid.uuid4(), tenant_id=tenant_id, email=f"bob_{run_id}@acme.com", is_org_admin=False)     # Contributor
user_charlie = User(user_id=uuid.uuid4(), tenant_id=tenant_id, email=f"charlie_{run_id}@acme.com", is_org_admin=False) # Viewer
user_dave = User(user_id=uuid.uuid4(), tenant_id=tenant_id, email=f"dave_{run_id}@acme.com", is_org_admin=False)   # Security
user_erin = User(user_id=uuid.uuid4(), tenant_id=tenant_id, email=f"erin_{run_id}@acme.com", is_org_admin=False)   # Multi-team
user_stranger = User(user_id=uuid.uuid4(), tenant_id=foreign_tenant_id, email=f"stranger_{run_id}@foreign.com", is_org_admin=False)

db.add_all([user_admin, user_proj_admin, user_alice, user_bob, user_charlie, user_dave, user_erin, user_stranger])
db.flush()
db.add(ProjectAdmin(id=uuid.uuid4(), project_id=project_id, user_id=user_proj_admin.user_id))

# Memberships
db.add(UserTeamMembership(id=uuid.uuid4(), user_id=user_alice.user_id, team_id=team_eng.team_id, project_id=project_id, role=TeamRole.team_lead))
db.add(UserTeamMembership(id=uuid.uuid4(), user_id=user_bob.user_id, team_id=team_eng.team_id, project_id=project_id, role=TeamRole.contributor))
db.add(UserTeamMembership(id=uuid.uuid4(), user_id=user_charlie.user_id, team_id=team_eng.team_id, project_id=project_id, role=TeamRole.viewer))
db.add(UserTeamMembership(id=uuid.uuid4(), user_id=user_dave.user_id, team_id=team_sec.team_id, project_id=project_id, role=TeamRole.contributor))
# Erin: viewer on Eng, team_lead on Product
db.add(UserTeamMembership(id=uuid.uuid4(), user_id=user_erin.user_id, team_id=team_eng.team_id, project_id=project_id, role=TeamRole.viewer))
db.add(UserTeamMembership(id=uuid.uuid4(), user_id=user_erin.user_id, team_id=team_prd.team_id, project_id=project_id, role=TeamRole.team_lead))

db.commit()

# --- 1. Test Request-Scoped Authorization Context ---
print("\n[TEST 1] Request-Scoped Authorization Context Construction")
ctx_alice = build_authorization_context(db, user_alice.user_id, project_id)
assert ctx_alice.is_org_admin is False
assert ctx_alice.is_project_admin is False
assert team_eng.team_id in ctx_alice.team_ids
assert ctx_alice.team_roles[team_eng.team_id] == TeamRole.team_lead
assert stage_req.stage_id in ctx_alice.accessible_stage_ids
assert stage_dev.stage_id in ctx_alice.accessible_stage_ids
assert len(ctx_alice.active_confidential_grant_team_ids) == 0

ctx_admin = build_authorization_context(db, user_admin.user_id, project_id)
assert ctx_admin.is_admin is True
assert len(ctx_admin.accessible_stage_ids) == 3

print("✓ AuthorizationContext correctly batch-loaded for regular user and admin.")

# --- 2. Test Documents & Batch ABAC Classification ---
print("\n[TEST 2] Batch ABAC Document Visibility Evaluation")

def create_test_doc(name: str, sensitivity: SensitivityLevel, stage: Stage, teams: list[Team]) -> Document:
    doc_id = uuid.uuid4()
    ver_id = uuid.uuid4()
    doc = Document(
        document_id=doc_id,
        project_id=stage.project_id,
        tenant_id=tenant_id,
        stage_id=stage.stage_id,
        uploaded_by=user_alice.user_id,
        uploaded_as_team_id=teams[0].team_id,
        sensitivity_level=sensitivity,
        original_filename=name,
        mime_type="text/markdown",
        current_version_id=None,
    )
    db.add(doc)
    db.flush()
    ver = DocumentVersion(
        version_id=ver_id,
        document_id=doc_id,
        version_number=1,
        uploaded_by=user_alice.user_id,
        file_size_bytes=1024,
        file_data=f"# {name}\n\nThis is test content for {name}.".encode("utf-8"),
        status=DocumentStatus.indexed,
    )
    db.add(ver)
    db.flush()
    doc.current_version_id = ver_id
    for t in teams:
        db.add(DocumentTeamVisibility(id=uuid.uuid4(), document_id=doc_id, team_id=t.team_id))
    db.commit()
    return doc

doc_public = create_test_doc("System Overview.md", SensitivityLevel.public, stage_req, [team_eng])
doc_internal = create_test_doc("Internal Architecture.md", SensitivityLevel.internal, stage_des, [team_eng])
doc_confidential = create_test_doc("Crypto Keys Spec.md", SensitivityLevel.confidential, stage_dev, [team_eng])
doc_sec_confidential = create_test_doc("PenTest Vulnerability Report.md", SensitivityLevel.confidential, stage_dev, [team_sec])
doc_prd_confidential = create_test_doc("Q4 Roadmap Confidential.md", SensitivityLevel.confidential, stage_req, [team_prd])

all_docs = [doc_public, doc_internal, doc_confidential, doc_sec_confidential, doc_prd_confidential]
all_doc_ids = [d.document_id for d in all_docs]

# Test Admin
vis_admin = classify_documents_visibility(db, ctx_admin, all_doc_ids)
assert all(v == DocumentVisibility.fully_allowed for v in vis_admin.values())

# Test Team Lead (Alice on Eng): sees Eng public, internal, and confidential; cannot see Sec or Prd
vis_alice = classify_documents_visibility(db, ctx_alice, all_doc_ids)
assert vis_alice[doc_public.document_id] == DocumentVisibility.fully_allowed
assert vis_alice[doc_internal.document_id] == DocumentVisibility.fully_allowed
assert vis_alice[doc_confidential.document_id] == DocumentVisibility.fully_allowed
assert vis_alice[doc_sec_confidential.document_id] == DocumentVisibility.not_visible
assert vis_alice[doc_prd_confidential.document_id] == DocumentVisibility.not_visible

# Test Contributor without grant (Bob): sees public and internal; confidential is blocked
ctx_bob = build_authorization_context(db, user_bob.user_id, project_id)
vis_bob = classify_documents_visibility(db, ctx_bob, all_doc_ids)
assert vis_bob[doc_public.document_id] == DocumentVisibility.fully_allowed
assert vis_bob[doc_internal.document_id] == DocumentVisibility.fully_allowed
assert vis_bob[doc_confidential.document_id] == DocumentVisibility.blocked_by_sensitivity
assert vis_bob[doc_sec_confidential.document_id] == DocumentVisibility.not_visible

# Test Viewer (Charlie): confidential is blocked
ctx_charlie = build_authorization_context(db, user_charlie.user_id, project_id)
vis_charlie = classify_documents_visibility(db, ctx_charlie, all_doc_ids)
assert vis_charlie[doc_confidential.document_id] == DocumentVisibility.blocked_by_sensitivity

# Test Multi-team (Erin): viewer on Eng, team_lead on Product
ctx_erin = build_authorization_context(db, user_erin.user_id, project_id)
vis_erin = classify_documents_visibility(db, ctx_erin, all_doc_ids)
assert vis_erin[doc_public.document_id] == DocumentVisibility.fully_allowed
assert vis_erin[doc_confidential.document_id] == DocumentVisibility.blocked_by_sensitivity  # viewer on Eng
assert vis_erin[doc_prd_confidential.document_id] == DocumentVisibility.fully_allowed      # team_lead on Product

# Test Stranger
ctx_stranger = build_authorization_context(db, user_stranger.user_id, project_id)
vis_stranger = classify_documents_visibility(db, ctx_stranger, all_doc_ids)
assert all(v == DocumentVisibility.not_visible for v in vis_stranger.values())

print("✓ Batch ABAC evaluation verified across Admin, Team Lead, Contributor, Viewer, Multi-team, and Stranger.")

# --- 3. Test Confidential Grant Lifecycle (Active vs Expired) ---
print("\n[TEST 3] Active vs Expired Confidential Grants")
now = datetime.now(timezone.utc)
grant_active = AccessRequest(
    request_id=uuid.uuid4(),
    user_id=user_bob.user_id,
    team_id=team_eng.team_id,
    status=AccessRequestStatus.approved,
    decided_by=user_alice.user_id,
    decided_at=now,
    expires_at=now + timedelta(days=90),
)
db.add(grant_active)
db.commit()

ctx_bob_granted = build_authorization_context(db, user_bob.user_id, project_id)
assert team_eng.team_id in ctx_bob_granted.active_confidential_grant_team_ids
vis_bob_granted = classify_documents_visibility(db, ctx_bob_granted, [doc_confidential.document_id])
assert vis_bob_granted[doc_confidential.document_id] == DocumentVisibility.fully_allowed
print("✓ Active 90-day grant allows contributor to access confidential tier.")

# Expire the grant
grant_active.expires_at = now - timedelta(hours=1)
db.commit()

ctx_bob_expired = build_authorization_context(db, user_bob.user_id, project_id)
assert team_eng.team_id not in ctx_bob_expired.active_confidential_grant_team_ids
vis_bob_expired = classify_documents_visibility(db, ctx_bob_expired, [doc_confidential.document_id])
assert vis_bob_expired[doc_confidential.document_id] == DocumentVisibility.blocked_by_sensitivity
print("✓ Expired grant correctly reverts contributor to blocked_by_sensitivity.")

# --- 4. Test Query Count Boundedness (Proof of NO N+1) ---
print("\n[TEST 4] Query Count Boundedness Check (No N+1)")
# Create 15 more candidate documents
extra_doc_ids = []
for i in range(15):
    d = create_test_doc(f"Candidate Doc {i}.md", SensitivityLevel.internal, stage_dev, [team_eng])
    extra_doc_ids.append(d.document_id)

candidate_sample = all_doc_ids + extra_doc_ids  # 20 documents total

query_count = 0
def count_queries(conn, cursor, statement, parameters, context, executemany):
    global query_count
    query_count += 1

event.listen(engine, "before_cursor_execute", count_queries)

query_count = 0
# Execute batch ABAC for 20 documents
vis_result = classify_documents_visibility(db, ctx_alice, candidate_sample)

event.remove(engine, "before_cursor_execute", count_queries)

print(f"-> Queries executed for {len(candidate_sample)} candidate documents: {query_count}")
# Boundedness assertion: Must NOT scale with 20 documents (max 2 queries: Document + DocumentTeamVisibility)
assert query_count <= 2, f"Expected <= 2 queries for batch ABAC, got {query_count}!"
assert len(vis_result) == len(candidate_sample)
print("✓ Verified: Batch authorization does NOT suffer from N+1 query explosion.")

# --- 5. Test Structured Stage Requirements & Coverage Status ---
print("\n[TEST 5] Structured Stage Requirements & Document Status Tools")

req_srs = RequiredDocument(
    requirement_id=uuid.uuid4(),
    stage_id=stage_req.stage_id,
    name="Software Requirements Specification",
    description="Full SRS IEEE 830",
    is_mandatory=True,
    source=RequirementSource.template,
)
req_uc = RequiredDocument(
    requirement_id=uuid.uuid4(),
    stage_id=stage_req.stage_id,
    name="Use Case Catalog",
    description="Actor user stories and scenarios",
    is_mandatory=False,
    source=RequirementSource.custom,
)
db.add_all([req_srs, req_uc])
db.commit()

# Helper to call agno Function object or standard callable
def call_tool(fn, *args, **kwargs):
    if hasattr(fn, "entrypoint"):
        return fn.entrypoint(*args, **kwargs)
    return fn(*args, **kwargs)

# Set Query Context for Alice
q_token = set_query_context(user_id=user_alice.user_id, project_id=project_id)
try:
    # 1. get_stage_requirements
    req_out = call_tool(get_stage_requirements, "Requirements")
    assert req_out["status"] == "ok"
    assert req_out["stage"] == "Requirements"
    assert req_out["total_count"] == 2
    assert req_out["mandatory_count"] == 1
    assert any(r["name"] == "Software Requirements Specification" and r["mandatory"] is True for r in req_out["requirements"])
    print("✓ get_stage_requirements correctly queried relational requirements directly from SQL.")

    # 2. get_stage_document_status before upload
    status_before = call_tool(get_stage_document_status, "Requirements")
    assert status_before["status"] == "ok"
    assert status_before["mandatory_requirements_count"] == 1
    assert status_before["satisfied_mandatory_count"] == 0
    assert status_before["coverage_percentage"] == 0.0
    assert "Software Requirements Specification" in status_before["missing_mandatory"]
    print("✓ get_stage_document_status correctly reports 0% coverage and missing mandatory items.")

    # False-positive test 1: Notes document should NOT satisfy deliverable requirement
    notes_doc = create_test_doc("Software Requirements Specification Notes.md", SensitivityLevel.public, stage_req, [team_eng])
    status_notes = call_tool(get_stage_document_status, "Requirements")
    assert "Software Requirements Specification" not in status_notes["satisfied_documents"]
    print("✓ False-positive guard verified: 'Software Requirements Specification Notes.md' does NOT satisfy SRS requirement.")

    # False-positive test 2: Generic substring doc should NOT satisfy specific requirement
    sub_doc = create_test_doc("Specification.md", SensitivityLevel.public, stage_req, [team_eng])
    status_sub = call_tool(get_stage_document_status, "Requirements")
    assert "Software Requirements Specification" not in status_sub["satisfied_documents"]
    print("✓ False-positive guard verified: 'Specification.md' does NOT satisfy SRS requirement.")

    # Upload matching SRS document
    srs_doc = create_test_doc("Software Requirements Specification (SRS) v1.0.md", SensitivityLevel.public, stage_req, [team_eng])

    # 3. get_stage_document_status after upload
    status_after = call_tool(get_stage_document_status, "Requirements")
    assert status_after["status"] == "ok"
    assert status_after["satisfied_mandatory_count"] == 1
    assert status_after["coverage_percentage"] == 100.0
    assert "Software Requirements Specification" in status_after["satisfied_documents"]
    assert "Use Case Catalog" in status_after["missing_documents"]
    assert any(d["requirement"] == "Software Requirements Specification" and d["status"] == "satisfied" for d in status_after["requirements_details"])
    print(f"✓ get_stage_document_status after upload reports {status_after['coverage_percentage']}% coverage with SRS satisfied.")
finally:
    reset_query_context(q_token)

# --- 6. Test Qdrant Hybrid Retrieval & Cross-Encoder Reranker ---
print("\n[TEST 6] Qdrant Hybrid Retrieval with Batch ABAC & Directional Stage Scope")

# Setup Qdrant collection
q_client = get_qdrant_client()
collection = ensure_tenant_collection(q_client, tenant_id)

# Create approved document in Requirements stage
workflow_srs = WorkflowState(document_id=srs_doc.document_id, state=WorkflowStatus.approved)
db.add(workflow_srs)
db.commit()

assert should_index(db, srs_doc.document_id) is True
index_res = index_document(db, srs_doc.document_id)
assert index_res["chunks_indexed"] > 0
print(f"✓ Document indexed into Qdrant tenant collection '{collection}' ({index_res['chunks_indexed']} chunks).")

# Retrieve as Alice querying Development stage (which directionally references Requirements)
rag_res = retrieve(
    db=db,
    user_id=user_alice.user_id,
    project_id=project_id,
    query="What is in the Software Requirements Specification?",
    stage_id=stage_dev.stage_id,  # Scoped to Development, should include referenced Requirements
)
assert len(rag_res.chunks) > 0
assert rag_res.chunks[0].document_id == srs_doc.document_id
assert rag_res.chunks[0].score is not None
print(f"✓ RAG retrieval successfully returned reranked chunk from referenced stage (score={rag_res.chunks[0].score:.2f}).")

# Stranger cannot retrieve
try:
    retrieve(db=db, user_id=user_stranger.user_id, project_id=project_id, query="Software Requirements")
    assert False, "Stranger retrieval should raise NoProjectAccessError!"
except NoProjectAccessError:
    print("✓ Stranger query rejected before Qdrant call.")

# --- 7. Test Malformed Qdrant Payload Fault Tolerance ---
print("\n[TEST 7] Malformed Qdrant Payload Fault Tolerance")
class FakePoint:
    def __init__(self, payload):
        self.payload = payload

good_point = FakePoint({
    "document_id": str(srs_doc.document_id),
    "project_id": str(project_id),
    "stage_id": str(stage_req.stage_id),
    "chunk_text": "Sample text",
    "section_title": "Section 1",
})
bad_point_missing = FakePoint({"chunk_text": "No IDs"})
bad_point_invalid_uuid = FakePoint({
    "document_id": "not-a-uuid",
    "project_id": str(project_id),
    "stage_id": str(stage_req.stage_id),
    "chunk_text": "Sample",
    "section_title": "Section",
})

assert _validate_point_payload(good_point) is True
assert _validate_point_payload(bad_point_missing) is False
assert _validate_point_payload(bad_point_invalid_uuid) is False
print("✓ Malformed Qdrant points are rejected safely before database authorization.")

print("\n" + "=" * 80)
print("🎉 ALL RAG OPTIMIZATIONS & STRUCTURED QUERY AGENT TESTS PASSED!")
print("=" * 80)
db.close()
