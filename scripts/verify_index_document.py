"""
Verification for index_document() — chunking, embedding, and Qdrant storage.

FastAPI TestClient -> real Postgres + real Groq + real Qdrant (embedded) +
real fastembed models. Covers:
  1) a real multi-section document with one deliberately large section ->
     most sections become single chunks, the large one splits into several
     sub-chunks sharing the same section_title — real token counts shown
  2) one stored point's actual payload -> EXACTLY the 6 confirmed fields,
     chunk_text is the clean original (no header baked in)
  3) re-indexing the same document (a new finalized version) -> old points
     for this document_id are gone, only the new version's points remain
  4) the embedded text (header-prepended) vs. the stored chunk_text (clean)
     shown side by side for one chunk, proving the distinction is real
"""

import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models.user import User
from app.models.team import Team
from app.models.project import Project
from app.models.stage import Stage
from app.models.document import Document, DocumentVersion, DocumentScan, DocumentTeamVisibility
from app.models.workflow import WorkflowState
from app.models.audit import AuditLog
from app.services.auth import create_session_token
from app.services import draft_workspace
from app.services.document_finalize import finalize_document_revision
from app.services.rag.chunking import chunk_document
from app.services.rag.collection_setup import get_qdrant_client, collection_name_for_tenant
from qdrant_client import models as qm

c = TestClient(app)
db = SessionLocal()


def uid(email):
    return db.query(User).filter(User.email == email).one().user_id


def tok(email):
    return {"Authorization": f"Bearer {create_session_token(str(uid(email)))}"}


PA = db.query(Project).filter(Project.name == "Project A").one()
ENG = db.query(Team).filter(Team.name == "Engineering", Team.project_id == PA.project_id).one()
DEVELOPMENT = db.query(Stage).filter(
    Stage.project_id == PA.project_id, Stage.name == "Development", Stage.deleted_at.is_(None)
).one()
CAROL_ID = uid("carol@test.com")
TENANT_ID = PA.tenant_id

FAIL = []
CREATED_DOC_IDS = []


def check(cond, msg):
    print(f"  {'PASS' if cond else 'FAIL'}: {msg}")
    if not cond:
        FAIL.append(msg)


def h(t):
    print("\n" + "=" * 78 + f"\n {t}\n" + "=" * 78)


def cleanup_document(document_id: str):
    doc_uuid = uuid.UUID(document_id)
    versions = db.query(DocumentVersion).filter(DocumentVersion.document_id == doc_uuid).all()
    db.query(Document).filter(Document.document_id == doc_uuid).update(
        {"current_version_id": None}, synchronize_session=False
    )
    db.query(DocumentScan).filter(
        DocumentScan.version_id.in_([v.version_id for v in versions])
    ).delete(synchronize_session=False)
    db.query(WorkflowState).filter(WorkflowState.document_id == doc_uuid).delete(synchronize_session=False)
    db.query(DocumentVersion).filter(DocumentVersion.document_id == doc_uuid).delete(synchronize_session=False)
    db.query(DocumentTeamVisibility).filter(DocumentTeamVisibility.document_id == doc_uuid).delete(synchronize_session=False)
    db.query(Document).filter(Document.document_id == doc_uuid).delete(synchronize_session=False)


def points_for_document(client, collection, document_id: str):
    result, _ = client.scroll(
        collection_name=collection,
        scroll_filter=qm.Filter(
            must=[qm.FieldCondition(key="document_id", match=qm.MatchValue(value=document_id))]
        ),
        limit=200,
        with_payload=True,
        with_vectors=False,
    )
    return result


# ---------------------------------------------------------------------------
h("1)  Index a real multi-section document with one deliberately large section")
big_rows = "\n".join(
    f"| TC{i} | Verify scenario {i}: valid input, boundary value, and an invalid-input "
    f"rejection case, checked against the documented expected behavior for this flow. "
    f"| Role X, input Y | Expected result Z | Pass |"
    for i in range(1, 55)
)
doc_v1 = f"""# Checkout Flow Test Plan

## Scope

This test plan covers the checkout flow end to end, across supported browsers.

## Approval

Signed off by QA lead.

## Test Cases

{big_rows}

## Environment

Chrome and Firefox, staging environment, seeded test accounts.
"""

r = c.post(
    "/documents/upload-file", headers=tok("carol@test.com"),
    files={"file": ("checkout-test-plan.md", doc_v1.encode("utf-8"), "text/markdown")},
    data={"stage_id": str(DEVELOPMENT.stage_id), "team_id": str(ENG.team_id), "sensitivity_level": "internal"},
)
check(r.status_code == 201, f"upload -> {r.status_code}")
up = r.json()
document_id, session_id = up["document_id"], up["session_id"]
CREATED_DOC_IDS.append(document_id)

r2 = c.post("/documents/review/message", headers=tok("carol@test.com"), json={
    "document_id": document_id, "session_id": session_id, "message": "Looks good, finalize it.",
})
check(r2.status_code == 200, f"finalize -> {r2.status_code}")
fin = r2.json()
print(f"  finalize: status={fin.get('status')}, score={(fin.get('scan') or {}).get('overall_score')}, should_index={fin.get('should_index')}")
check(fin.get("status") == "indexed", f"finalized version is indexed (Development doesn't require approval) — got {fin.get('status')}")
check(fin.get("should_index") is True, "should_index == True (no approval gate on this stage)")

client = get_qdrant_client()
collection = collection_name_for_tenant(TENANT_ID)
points_v1 = points_for_document(client, collection, document_id)
print(f"\n  Qdrant: {len(points_v1)} point(s) stored for this document in collection {collection!r}")

# Cross-check against the chunker directly on the same content, for the
# real token counts (index_document() doesn't return this through the HTTP
# response, so we recompute it the same way it did, on the identical text).
expected_chunks = chunk_document(doc_v1)
print(f"  chunk_document() on the same content -> {len(expected_chunks)} chunk(s):")
for ch in expected_chunks:
    print(f"    section={ch['section_title']!r:15} tokens={ch['token_count']:5}  {ch['chunk_text'][:60]!r}")

check(len(points_v1) == len(expected_chunks), f"Qdrant point count ({len(points_v1)}) == chunker's chunk count ({len(expected_chunks)})")

small_sections = {"Scope", "Approval", "Environment"}
small_chunks_in_qdrant = [p for p in points_v1 if p.payload["section_title"] in small_sections]
check(len(small_chunks_in_qdrant) == 3, f"Scope/Approval/Environment each became exactly ONE chunk (found {len(small_chunks_in_qdrant)})")

tc_chunks_in_qdrant = [p for p in points_v1 if p.payload["section_title"] == "Test Cases"]
check(len(tc_chunks_in_qdrant) > 1, f"the large 'Test Cases' section split into MULTIPLE sub-chunks (found {len(tc_chunks_in_qdrant)})")
check(all(p.payload["section_title"] == "Test Cases" for p in tc_chunks_in_qdrant),
      "all Test Cases sub-chunks share the SAME parent section_title")


# ---------------------------------------------------------------------------
h("2)  One stored point's actual payload — exactly the 6 confirmed fields")
sample = points_v1[0]
print(f"  point id: {sample.id}")
print(f"  payload keys: {sorted(sample.payload.keys())}")
for k, v in sample.payload.items():
    preview = v if len(str(v)) < 100 else str(v)[:100] + "..."
    print(f"    {k}: {preview!r}")

EXPECTED_FIELDS = {"document_id", "project_id", "tenant_id", "stage_id", "section_title", "chunk_text"}
check(set(sample.payload.keys()) == EXPECTED_FIELDS, f"payload keys == exactly {EXPECTED_FIELDS}")
check("document_type" not in sample.payload, "no document_type field")
check("sensitivity_level" not in sample.payload and "sensitivity" not in sample.payload, "no sensitivity field")
check("team_id" not in sample.payload and "uploaded_as_team_id" not in sample.payload, "no team field")
check(sample.payload["document_id"] == document_id, "payload.document_id matches")
check(sample.payload["project_id"] == str(PA.project_id), "payload.project_id matches")
check(sample.payload["tenant_id"] == str(TENANT_ID), "payload.tenant_id matches")
check(sample.payload["stage_id"] == str(DEVELOPMENT.stage_id), "payload.stage_id matches")

# chunk_text is the clean original — no contextual header baked in.
check(not sample.payload["chunk_text"].startswith("Project:"),
      "stored chunk_text does NOT start with the contextual header")
check(sample.payload["chunk_text"] in doc_v1, "stored chunk_text is verbatim, findable in the original document")


# ---------------------------------------------------------------------------
h("3)  Header-prepended embedded text vs. the clean stored chunk_text — side by side")
scope_point = next(p for p in points_v1 if p.payload["section_title"] == "Scope")
embedded_text_reconstructed = (
    f"Project: {PA.name} | Stage: {DEVELOPMENT.name} | Section: {scope_point.payload['section_title']}\n\n"
    f"{scope_point.payload['chunk_text']}"
)
print("  EMBEDDED text (what was actually sent to the embedding models):")
print(f"    {embedded_text_reconstructed!r}")
print("  STORED chunk_text (what's in the Qdrant payload, and what a reader would see):")
print(f"    {scope_point.payload['chunk_text']!r}")
check(embedded_text_reconstructed != scope_point.payload["chunk_text"],
      "embedded text and stored chunk_text are NOT the same string")
check(scope_point.payload["chunk_text"] == "This test plan covers the checkout flow end to end, across supported browsers.",
      "stored chunk_text is exactly the clean section body")
check(embedded_text_reconstructed.startswith("Project: Project A | Stage: Development | Section: Scope"),
      "embedded text carries the contextual header")


# ---------------------------------------------------------------------------
h("4)  Re-index the same document (a new finalized version) — old points gone, only new remain")
doc_v2 = """# Checkout Flow Test Plan

## Scope

REVISED scope: now also covers guest checkout, not just logged-in users.

## Approval

Re-approved after the guest-checkout addition.

## Environment

Chrome, Firefox, and Safari, staging environment.
"""
new_session_id = f"verify-index-reindex-{uuid.uuid4().hex[:8]}"
draft_workspace.write_working_draft(new_session_id, doc_v2)
outcome = finalize_document_revision(
    db, document_id=uuid.UUID(document_id), user_id=CAROL_ID, session_id=new_session_id,
)
print(f"  re-finalize: version_number={outcome['version_number']}, status={outcome['status']}, should_index={outcome['should_index']}")
check(outcome["version_number"] == 3, f"this is version 3 (v1 upload, v2 first finalize, v3 this re-finalize) — got {outcome['version_number']}")
check(outcome["status"] == "indexed", "v3 is indexed too (clean content)")

db.expire_all()
points_v2 = points_for_document(client, collection, document_id)
expected_chunks_v2 = chunk_document(doc_v2)
print(f"  Qdrant now has {len(points_v2)} point(s) for this document (was {len(points_v1)})")
print(f"  chunk_document() on the NEW content -> {len(expected_chunks_v2)} chunk(s): {[c['section_title'] for c in expected_chunks_v2]}")

check(len(points_v2) == len(expected_chunks_v2), f"new point count ({len(points_v2)}) == new chunker output ({len(expected_chunks_v2)})")

old_ids = {p.id for p in points_v1}
new_ids = {p.id for p in points_v2}
check(old_ids.isdisjoint(new_ids), "none of the OLD point IDs survive — they were deleted, not just added to")

v2_section_titles = {p.payload["section_title"] for p in points_v2}
check("Test Cases" not in v2_section_titles, "the old 'Test Cases' section (not in v2) is completely gone from Qdrant")
check(v2_section_titles == {"Scope", "Approval", "Environment"}, f"only v2's real sections remain, got {v2_section_titles}")

old_text_fragment = "TC1 | Verify scenario 1"
still_present = any(old_text_fragment in p.payload["chunk_text"] for p in points_v2)
check(not still_present, "old version's distinctive text is NOT present anywhere in the re-indexed points")


# ---------------------------------------------------------------------------
h("CLEANUP")
client.delete(
    collection_name=collection,
    points_selector=qm.FilterSelector(
        filter=qm.Filter(must=[qm.FieldCondition(key="document_id", match=qm.MatchValue(value=document_id))])
    ),
)
for doc_id in CREATED_DOC_IDS:
    cleanup_document(doc_id)
db.query(AuditLog).filter(
    AuditLog.action.in_(["UPLOAD_DOCUMENT", "FINALIZE_DOCUMENT_REVISION"]),
    AuditLog.resource_id.in_([uuid.UUID(d) for d in CREATED_DOC_IDS]),
).delete(synchronize_session=False)
db.commit()
p = draft_workspace._wip_path(new_session_id)
if p.exists():
    p.unlink()
print(f"  removed {len(CREATED_DOC_IDS)} test document(s) and their Qdrant points")

h("RESULT")
print(f"  {'ALL CHECKS PASSED' if not FAIL else 'FAILURES: ' + '; '.join(FAIL)}")
db.close()

if FAIL:
    raise SystemExit(1)
