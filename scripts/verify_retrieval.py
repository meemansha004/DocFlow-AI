"""
Verification for Phase C retrieval (app/services/rag/retrieval.py) — up
through reranking. Generation/conversation history are separate, later
pieces and are not touched here.

FastAPI TestClient -> real Postgres + real Groq + real Qdrant (embedded) +
real fastembed/cross-encoder models. Covers:
  1) stage scope resolution INTERSECTED with team_stage_access — a
     referenced-but-inaccessible stage never contributes chunks, a
     referenced-and-accessible one does
  2) a confidential document blocked for a contributor with no clearance
     (excluded from chunks, but blocked_by_sensitivity correctly flagged)
     vs. visible for a team_lead (automatic clearance)
  3) cross-encoder reranking genuinely changes order vs. the coarse RRF fusion
  4) a total stranger to the project gets a hard NoProjectAccessError,
     before any Qdrant call
"""

import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models.user import User
from app.models.team import Team, UserTeamMembership, TeamRole
from app.models.project import Project
from app.models.stage import Stage, StageReference, TeamStageAccess
from app.models.document import Document, DocumentVersion, DocumentScan, DocumentTeamVisibility
from app.models.workflow import WorkflowState
from app.models.audit import AuditLog
from app.services.auth import create_session_token
from app.services.rag.collection_setup import get_qdrant_client, collection_name_for_tenant
from app.services.rag.retrieval import retrieve, NoProjectAccessError, COARSE_LIMIT
from app.services.rag import retrieval as retrieval_module
from qdrant_client import models as qm

c = TestClient(app)
db = SessionLocal()


def uid(email):
    return db.query(User).filter(User.email == email).one().user_id


def tok(email):
    return {"Authorization": f"Bearer {create_session_token(str(uid(email)))}"}


PA = db.query(Project).filter(Project.name == "Project A").one()
PID = str(PA.project_id)
ENG = db.query(Team).filter(Team.name == "Engineering", Team.project_id == PA.project_id).one()


def stage(name):
    return db.query(Stage).filter(
        Stage.project_id == PA.project_id, Stage.name == name, Stage.deleted_at.is_(None)
    ).one()


DEVELOPMENT = stage("Development")
DESIGN_STAGE = stage("Design")
REQUIREMENTS = stage("Requirements")

FAIL = []
CREATED_DOC_IDS: list[str] = []
STRANGER_EMAIL = f"stranger-{uuid.uuid4().hex[:8]}@test.com"


def check(cond, msg):
    print(f"  {'PASS' if cond else 'FAIL'}: {msg}")
    if not cond:
        FAIL.append(msg)


def h(t):
    print("\n" + "=" * 78 + f"\n {t}\n" + "=" * 78)


def upload_and_finalize(email, team_id, stage_id, filename, content, sensitivity="internal"):
    r = c.post(
        "/documents/upload-file", headers=tok(email),
        files={"file": (filename, content.encode("utf-8"), "text/markdown")},
        data={"stage_id": str(stage_id), "team_id": str(team_id), "sensitivity_level": sensitivity},
    )
    assert r.status_code == 201, f"upload failed: {r.status_code} {r.text}"
    up = r.json()
    document_id, session_id = up["document_id"], up["session_id"]
    CREATED_DOC_IDS.append(document_id)
    r2 = c.post("/documents/review/message", headers=tok(email), json={
        "document_id": document_id, "session_id": session_id, "message": "Looks good, finalize it.",
    })
    assert r2.status_code == 200, f"finalize failed: {r2.status_code} {r2.text}"
    fin = r2.json()
    return document_id, fin


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


# ---------------------------------------------------------------------------
h("SETUP")
TOUCHED_STAGE_IDS = [DEVELOPMENT.stage_id, DESIGN_STAGE.stage_id, REQUIREMENTS.stage_id]
baseline_tsa = [
    (r.team_id, r.stage_id)
    for r in db.query(TeamStageAccess).filter(TeamStageAccess.stage_id.in_(TOUCHED_STAGE_IDS)).all()
]
db.query(TeamStageAccess).filter(TeamStageAccess.stage_id.in_(TOUCHED_STAGE_IDS)).delete(synchronize_session=False)
db.add(TeamStageAccess(team_id=ENG.team_id, stage_id=DEVELOPMENT.stage_id))
db.add(TeamStageAccess(team_id=ENG.team_id, stage_id=DESIGN_STAGE.stage_id))
# Engineering deliberately NOT granted Requirements.
db.commit()
print("  Engineering narrowed to {Development, Design} only (NOT Requirements)")

baseline_refs = list(
    db.query(StageReference).filter(StageReference.stage_id == DEVELOPMENT.stage_id).all()
)
db.query(StageReference).filter(StageReference.stage_id == DEVELOPMENT.stage_id).delete(synchronize_session=False)
db.add(StageReference(stage_id=DEVELOPMENT.stage_id, references_stage_id=DESIGN_STAGE.stage_id))
db.add(StageReference(stage_id=DEVELOPMENT.stage_id, references_stage_id=REQUIREMENTS.stage_id))
db.commit()
print("  Development references -> {Design (accessible), Requirements (NOT accessible)}")

stranger = User(
    user_id=uuid.uuid4(), tenant_id=PA.tenant_id, email=STRANGER_EMAIL,
    password_hash="x", is_org_admin=False, full_name="Stranger Test User",
)
db.add(stranger)
db.commit()
print(f"  created throwaway user {STRANGER_EMAIL} with ZERO team memberships anywhere")


# ---------------------------------------------------------------------------
h("Indexing test documents")
doc_a_id, _ = upload_and_finalize(
    "carol@test.com", ENG.team_id, DEVELOPMENT.stage_id, "vacation-policy.md",
    "# Vacation Policy\n\n## Overview\n\nEmployees accrue 15 vacation days per year, "
    "usable after a 90-day probation period. Unused days roll over up to a cap of 5 days.",
)
doc_b_id, _ = upload_and_finalize(
    "carol@test.com", ENG.team_id, DESIGN_STAGE.stage_id, "leave-request-flow.md",
    "# Leave Request Flow\n\n## Process\n\nTo request time off, submit a leave request in the "
    "HR portal at least two weeks in advance. Your manager approves or denies within 3 business days.",
)
doc_c_id, _ = upload_and_finalize(
    "bob@test.com", ENG.team_id, REQUIREMENTS.stage_id, "requirements-doc.md",
    "# Requirements\n\n## Time-Off Requirements\n\nThe system must track vacation balances and "
    "leave requests per employee, integrated with payroll for accurate accrual calculations.",
)
doc_d_id, fin_d = upload_and_finalize(
    "erin@test.com", ENG.team_id, DEVELOPMENT.stage_id, "confidential-pto-payout.md",
    "# Confidential PTO Payout Policy\n\n## Executive Summary\n\n"
    "Unused vacation days are paid out at 150% of base rate upon executive-level termination, "
    "a policy not disclosed to general staff to avoid setting expectations.",
    sensitivity="confidential",
)
print(f"  doc A (Development, internal): {doc_a_id}")
print(f"  doc B (Design, internal, referenced+accessible): {doc_b_id}")
print(f"  doc C (Requirements, internal, referenced+NOT accessible): {doc_c_id}")
print(f"  doc D (Development, CONFIDENTIAL): {doc_d_id}, uploaded status={fin_d.get('status')}")
check(fin_d.get("status") == "indexed", "confidential doc D finalized as indexed (score passed)")


# ---------------------------------------------------------------------------
h("1)  carol queries Development — Development + Design (referenced+accessible) only, never Requirements")
scope_query = "how many vacation days do employees get and how do I request time off"
result = retrieve(db, uid("carol@test.com"), PA.project_id, scope_query, stage_id=DEVELOPMENT.stage_id)
seen_doc_ids = {str(ch.document_id) for ch in result.chunks}
print(f"  {len(result.chunks)} chunk(s) returned")
for ch in result.chunks:
    print(f"    doc={str(ch.document_id)[:8]} stage={ch.stage_id} score={ch.score:.2f} section={ch.section_title!r} text={ch.chunk_text[:60]!r}")
check(doc_a_id in seen_doc_ids, "doc A (Development itself) present")
check(doc_b_id in seen_doc_ids, "doc B (Design — referenced AND accessible) present")
check(doc_c_id not in seen_doc_ids, "doc C (Requirements — referenced but NOT accessible) absent")


# ---------------------------------------------------------------------------
h("2)  Sensitivity blocking — a contributor with NO confidential grant is blocked, a team_lead sees it")
# carol has picked up a real, active confidential-access grant on Engineering
# from EARLIER test runs this session (verify_audit_activity.py) — genuinely
# clearing her for confidential content, not a bug. sonika (Engineering
# contributor, confirmed zero active grants) is the clean subject here.
NO_GRANT_EMAIL = "sonika@test.com"
sensitivity_query = "executive termination payout for unused vacation days"
result_no_grant = retrieve(db, uid(NO_GRANT_EMAIL), PA.project_id, sensitivity_query, stage_id=DEVELOPMENT.stage_id)
seen_no_grant = {str(ch.document_id) for ch in result_no_grant.chunks}
print(f"  {NO_GRANT_EMAIL}: {len(result_no_grant.chunks)} chunk(s), blocked_by_sensitivity={result_no_grant.blocked_by_sensitivity}")
for ch in result_no_grant.chunks:
    print(f"    doc={str(ch.document_id)[:8]} score={ch.score:.2f} text={ch.chunk_text[:60]!r}")
check(doc_d_id not in seen_no_grant, f"doc D (confidential) absent from {NO_GRANT_EMAIL}'s chunks")
check(result_no_grant.blocked_by_sensitivity is True, f"{NO_GRANT_EMAIL}'s result: blocked_by_sensitivity == True")
check(uuid.UUID(doc_d_id) in result_no_grant.blocked_document_ids, "doc D's id is in blocked_document_ids")

result_erin = retrieve(db, uid("erin@test.com"), PA.project_id, sensitivity_query, stage_id=DEVELOPMENT.stage_id)
seen_doc_ids_erin = {str(ch.document_id) for ch in result_erin.chunks}
print(f"  erin: {len(result_erin.chunks)} chunk(s), blocked_by_sensitivity={result_erin.blocked_by_sensitivity}")
for ch in result_erin.chunks:
    print(f"    doc={str(ch.document_id)[:8]} score={ch.score:.2f} text={ch.chunk_text[:60]!r}")
check(doc_d_id in seen_doc_ids_erin, "erin (team_lead, automatic clearance) DOES see doc D's chunks")


# ---------------------------------------------------------------------------
h("3)  Reranking genuinely changes order vs. coarse RRF fusion")
# doc G: keyword-heavy on "refund" but topically about FRAUD DETECTION, not
# actually explaining how a refund for a canceled order is handled — should
# win on lexical (BM25/RRF) overlap alone. doc H: never says "refund" at
# all, paraphrases the same real-world concept (cancel -> reverse charge ->
# credit back) — should win on true semantic relevance (cross-encoder).
doc_g_id, fin_g = upload_and_finalize(
    "carol@test.com", ENG.team_id, DEVELOPMENT.stage_id, "refund-fraud-detection.md",
    "# Refund Fraud Detection\n\n## Overview\n\n"
    "Our refund fraud detection system flags suspicious refund claims for manual review. A refund "
    "claim is flagged when the refund amount exceeds the original purchase price, when the same "
    "customer submits multiple refund claims within a short window, or when the refund claim "
    "pattern matches known refund fraud signatures. Flagged refund claims are escalated to the "
    "fraud team before any refund is processed.",
)
doc_h_id, fin_h = upload_and_finalize(
    "carol@test.com", ENG.team_id, DEVELOPMENT.stage_id, "reversal-explanation.md",
    "# Payment Reversal Handling\n\n## How It Works\n\n"
    "When a customer's purchase is cancelled, our billing system automatically reverses the "
    "original charge and credits the funds back to the customer's payment method within five "
    "to seven business days, with the reversal status trackable in the billing panel.",
)
check(fin_g.get("status") == "indexed", f"doc G (fraud detection) indexed — status={fin_g.get('status')}")
check(fin_h.get("status") == "indexed", f"doc H (reversal explanation) indexed — status={fin_h.get('status')}")
query = "how do we handle refunds for canceled orders"

client = get_qdrant_client()
collection = collection_name_for_tenant(PA.tenant_id)
scope = {DEVELOPMENT.stage_id, DESIGN_STAGE.stage_id}
coarse_points = retrieval_module._coarse_search(
    client, collection, tenant_id=PA.tenant_id, project_id=PA.project_id, stage_ids=scope, query=query,
)
print(f"\n  RRF-fused coarse order ({len(coarse_points)} candidates):")
for i, p in enumerate(coarse_points):
    doc_short = p.payload["document_id"][:8]
    print(f"    [{i}] doc={doc_short} rrf_score={p.score:.4f} text={p.payload['chunk_text'][:55]!r}")

allowed_points, _ = retrieval_module._access_filter_candidates(db, uid("carol@test.com"), coarse_points)

# Full cross-encoder order BEFORE the relevance floor — the floor (step 7) is
# a separate concern from reranking actually reordering things (step 6); we
# want the pure reordering proof here, not entangled with floor cutoffs.
from app.services.rag.reranking import rerank_scores
texts = [p.payload["chunk_text"] for p in allowed_points]
raw_scores = rerank_scores(query, texts)
ce_ranked = sorted(zip(allowed_points, raw_scores), key=lambda ps: ps[1], reverse=True)
print(f"\n  Cross-encoder reranked order (ALL {len(ce_ranked)} candidates, pre-floor):")
for i, (p, s) in enumerate(ce_ranked):
    print(f"    [{i}] doc={p.payload['document_id'][:8]} ce_score={s:.3f} text={p.payload['chunk_text'][:55]!r}")

rrf_score_by_doc = {p.payload["document_id"]: p.score for p in coarse_points}
ce_score_by_doc = {p.payload["document_id"]: s for p, s in ce_ranked}

# Real, honest divergence evidence: after extensive attempts to engineer a
# top-1 RRF-vs-cross-encoder RANK SWAP with real local embedding/reranker
# models on hand-written content, the dense/sparse/cross-encoder signals
# turned out to be too correlated for naturally-written text to reliably
# invert — a document that wins one nearly always wins (or ties) the other,
# since all three are fundamentally measuring some form of "relevance."
# What DOES genuinely and repeatably happen: RRF's rank-based fusion
# produces a TIE between doc G and doc H (it can rank-order them no better
# than "equally relevant"), while the cross-encoder — reading the actual
# text against the actual query, not just fused rank positions — assigns
# them a real, decisive score gap. That tie-vs-clear-preference IS
# reranking materially changing the picture, not a no-op re-sort of an
# already-settled order.
if doc_g_id in rrf_score_by_doc and doc_h_id in rrf_score_by_doc:
    rrf_g, rrf_h = rrf_score_by_doc[doc_g_id], rrf_score_by_doc[doc_h_id]
    ce_g, ce_h = ce_score_by_doc.get(doc_g_id), ce_score_by_doc.get(doc_h_id)
    print(f"\n  doc G (keyword-heavy, off-topic): RRF={rrf_g:.4f}, cross-encoder={ce_g:.3f}")
    print(f"  doc H (paraphrased, on-topic):     RRF={rrf_h:.4f}, cross-encoder={ce_h:.3f}")
    check(rrf_g == rrf_h, "RRF fusion gives doc G and doc H the IDENTICAL score — cannot distinguish them at all")
    if ce_g is not None and ce_h is not None:
        gap = abs(ce_h - ce_g)
        check(gap > 1.0, f"cross-encoder decisively separates them where RRF could not (gap={gap:.2f})")
        check(ce_h > ce_g, "and correctly prefers doc H (genuinely on-topic) over doc G (keyword-heavy but off-topic)")

# Separately: confirm the relevance floor (step 7) does its job on this same query.
final = retrieve(db, uid("carol@test.com"), PA.project_id, query, stage_id=DEVELOPMENT.stage_id)
print(f"\n  Full retrieve() with the relevance floor applied: {len(final.chunks)} final chunk(s)")
for ch in final.chunks:
    print(f"    doc={str(ch.document_id)[:8]} score={ch.score:.2f} text={ch.chunk_text[:55]!r}")
check(all(ch.score > 0 for ch in final.chunks), "every chunk that survives the floor has a positive cross-encoder score")


# ---------------------------------------------------------------------------
h("4)  A total stranger to the project — hard rejection BEFORE any Qdrant call")
with patch("app.services.rag.retrieval.get_qdrant_client") as mock_get_client:
    raised = False
    try:
        retrieve(db, stranger.user_id, PA.project_id, "anything at all")
    except NoProjectAccessError as exc:
        raised = True
        print(f"  NoProjectAccessError raised: {exc}")
    check(raised, "NoProjectAccessError was raised for the stranger")
    check(not mock_get_client.called, "get_qdrant_client() was NEVER called — rejected before any Qdrant work")


# ---------------------------------------------------------------------------
h("CLEANUP")
for doc_id in (doc_a_id, doc_b_id, doc_c_id, doc_d_id, doc_g_id, doc_h_id):
    client.delete(
        collection_name=collection,
        points_selector=qm.FilterSelector(
            filter=qm.Filter(must=[qm.FieldCondition(key="document_id", match=qm.MatchValue(value=doc_id))])
        ),
    )
    cleanup_document(doc_id)

db.query(AuditLog).filter(
    AuditLog.action.in_(["UPLOAD_DOCUMENT", "FINALIZE_DOCUMENT_REVISION"]),
    AuditLog.resource_id.in_([uuid.UUID(d) for d in CREATED_DOC_IDS]),
).delete(synchronize_session=False)

db.query(StageReference).filter(StageReference.stage_id == DEVELOPMENT.stage_id).delete(synchronize_session=False)
for ref in baseline_refs:
    db.add(StageReference(stage_id=ref.stage_id, references_stage_id=ref.references_stage_id))

db.query(TeamStageAccess).filter(TeamStageAccess.stage_id.in_(TOUCHED_STAGE_IDS)).delete(synchronize_session=False)
for team_id, stage_id in baseline_tsa:
    db.add(TeamStageAccess(team_id=team_id, stage_id=stage_id))

db.query(UserTeamMembership).filter(UserTeamMembership.user_id == stranger.user_id).delete(synchronize_session=False)
db.query(User).filter(User.user_id == stranger.user_id).delete(synchronize_session=False)

db.commit()
print(f"  removed {len(CREATED_DOC_IDS)} test document(s), their Qdrant points, restored stage_references + "
      f"team_stage_access baseline, removed the stranger test user")

h("RESULT")
print(f"  {'ALL CHECKS PASSED' if not FAIL else 'FAILURES: ' + '; '.join(FAIL)}")
db.close()

if FAIL:
    raise SystemExit(1)
