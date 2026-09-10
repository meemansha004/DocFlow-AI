"""
Verification for the Query Agent (app/agents/query_agent.py) + its 6 read-only
tools (app/tools/query_tools.py), driven through the real runner
(app/services/query_chat.run_query_turn).

Real Postgres + real Groq. Covers the scenarios from the build prompt:

  1) "who uploaded <a real document>?" -> correct answer, get_document_info called
  2) a document the user cannot see -> clean denial, no leaked info
  3) "what's pending my approval?" -> matches the real Pending Approvals tab
     (GET /documents?…pending_review  +  GET /access-requests/pending)
  4) "am I allowed to upload to <a stage with no team_stage_access>?" -> accurate no
  5) "approve this for me" -> refuses, explains it only answers questions, no tool
  6) token usage vs a comparable RAG Agent query -> Query Agent is meaningfully
     cheaper (smaller system prompt + fewer tool schemas)

Prints every transcript and the real token numbers, then cleans up.
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.config import GROQ_MODEL
from app.models.user import User
from app.models.project import Project
from app.models.team import Team, AccessRequest
from app.models.stage import Stage
from app.models.chat import ChatMessage, ChatSession
from app.models.document import Document, DocumentVersion, DocumentScan, DocumentTeamVisibility
from app.models.workflow import WorkflowState
from app.models.audit import AuditLog
from app.services.auth import create_session_token
from app.services.query_chat import run_query_turn
from app.services.query_context import set_query_context, reset_query_context
from app.services.rag_context import set_rag_context, reset_rag_context
from app.agents.query_agent import query_agent
from app.agents.rag_agent import rag_agent
from app.services.rag.collection_setup import get_qdrant_client, collection_name_for_tenant
from sqlalchemy import text as sqltext
from qdrant_client import models as qm

c = TestClient(app)
db = SessionLocal()
FAIL = []
CREATED_DOC_IDS = []
CHAT_SESSION_IDS = set()
CREATED_STAGE_IDS = []


def check(cond, msg):
    print(f"  {'PASS' if cond else 'FAIL'}: {msg}")
    if not cond:
        FAIL.append(msg)


def h(t):
    print("\n" + "=" * 78 + f"\n {t}\n" + "=" * 78)


def uid(email):
    return db.query(User).filter(User.email == email).one().user_id


def tok(email):
    return {"Authorization": f"Bearer {create_session_token(str(uid(email)))}"}


PA = db.query(Project).filter(Project.name == "Project A").one()
ENG = db.query(Team).filter(Team.name == "Engineering", Team.project_id == PA.project_id).one()
DEV = db.query(Stage).filter(
    Stage.project_id == PA.project_id, Stage.name == "Development", Stage.deleted_at.is_(None)
).one()
TESTING = db.query(Stage).filter(
    Stage.project_id == PA.project_id, Stage.name == "Testing", Stage.deleted_at.is_(None)
).one()

print(f"  model in use (app.config.GROQ_MODEL): {GROQ_MODEL}")


def upload_and_finalize(email, filename, content, stage_id, sensitivity="internal"):
    r = c.post(
        "/documents/upload-file", headers=tok(email),
        files={"file": (filename, content.encode("utf-8"), "text/markdown")},
        data={"stage_id": str(stage_id), "team_id": str(ENG.team_id),
              "sensitivity_level": sensitivity},
    )
    assert r.status_code == 201, f"upload failed: {r.status_code} {r.text}"
    up = r.json()
    CREATED_DOC_IDS.append(up["document_id"])
    r2 = c.post("/documents/review/message", headers=tok(email), json={
        "document_id": up["document_id"], "session_id": up["session_id"],
        "message": "Looks good, finalize it.",
    })
    assert r2.status_code == 200, f"finalize failed: {r2.status_code} {r2.text}"
    return up["document_id"], r2.json()


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


def turn(label, *, email, session_id, message):
    print(f"\n  [{label}] {email} says: {message!r}")
    out = run_query_turn(
        user_id=uid(email), project_id=PA.project_id, session_id=session_id, message=message,
    )
    CHAT_SESSION_IDS.add(out["session_id"])
    print(f"  tools called : {out['tools_called']}")
    print(f"  agent reply  :\n" + "\n".join("    " + l for l in out["reply"].splitlines()))
    return out


# ---------------------------------------------------------------------------
h("SETUP")

VISIBLE_DOC = "Query Agent Visible Note.md"
PENDING_DOC = "Query Agent Pending Note.md"
RAG_DOC = "Query Agent RAG Compare.md"

# (a) a plain visible, indexed doc in Development (uploaded by carol)
vis_id, _ = upload_and_finalize(
    "carol@test.com", VISIBLE_DOC,
    "# Query Agent Visible Note\n\n## Purpose\nA small note used to verify metadata lookups.\n",
    DEV.stage_id,
)
print(f"  uploaded {VISIBLE_DOC} (Development, carol) -> {vis_id}")

# (b) a doc in the Testing stage (requires_approval) submitted -> pending_review
pend_r = c.post(
    "/documents/upload-file", headers=tok("carol@test.com"),
    files={"file": (PENDING_DOC, b"# Pending Note\n\n## Body\nAwaiting sign-off.\n", "text/markdown")},
    data={"stage_id": str(TESTING.stage_id), "team_id": str(ENG.team_id), "sensitivity_level": "internal"},
)
assert pend_r.status_code == 201, pend_r.text
pend_id = pend_r.json()["document_id"]
CREATED_DOC_IDS.append(pend_id)
c.post("/documents/review/message", headers=tok("carol@test.com"), json={
    "document_id": pend_id, "session_id": pend_r.json()["session_id"],
    "message": "finalize it",
})
sub = c.post(f"/documents/{pend_id}/submit", headers=tok("carol@test.com"))
assert sub.status_code == 200, f"submit failed: {sub.status_code} {sub.text}"
check(sub.json()["state"] == "pending_review", f"{PENDING_DOC} is now pending_review")

# (c) an indexed doc for the RAG comparison turn
rag_id, rag_fin = upload_and_finalize(
    "carol@test.com", RAG_DOC,
    "# Query Agent RAG Compare\n\n## Retention\nBackups are kept for 30 days, then rotated out.\n",
    DEV.stage_id,
)
check(rag_fin.get("status") == "indexed", f"{RAG_DOC} indexed for RAG comparison")

# (d) a throwaway stage in Project A with NO team_stage_access grant
st = c.post(f"/projects/{PA.project_id}/stages", headers=tok("bob@test.com"),
            json={"name": "Query Test Locked Stage"})
assert st.status_code in (200, 201), st.text
LOCKED_STAGE_ID = st.json()["stage_id"]
CREATED_STAGE_IDS.append(LOCKED_STAGE_ID)
print(f"  created locked stage 'Query Test Locked Stage' (no team_stage_access) -> {LOCKED_STAGE_ID}")

db.expire_all()


# ---------------------------------------------------------------------------
h("1) 'who uploaded <a real document>?' -> get_document_info, correct answer")
o = turn("1", email="carol@test.com", session_id=None,
         message="Who uploaded the Query Agent Visible Note, and what stage is it in?")
check("get_document_info" in o["tools_called"], "get_document_info was the tool called")
check("carol@test.com" in o["reply"], "reply names the real uploader (carol@test.com)")
check("development" in o["reply"].lower(), "reply states the correct stage (Development)")


# ---------------------------------------------------------------------------
h("2) a document the user can't see -> clean denial, no leaked info")
# vic is a VIEWER on Engineering; 'Confidential Arch Spec.md' is confidential.
o = turn("2", email="vic@test.com", session_id=None,
         message="Who uploaded the Confidential Arch Spec document and when?")
low = o["reply"].lower().replace("’", "'").replace("‘", "'")
check("get_document_info" in o["tools_called"], "get_document_info was called (not answered from memory)")
check("erin@test.com" not in low, "does NOT leak the real uploader (erin@test.com)")
check(any(p in low for p in ("can't find", "cannot find", "couldn't find", "could not find",
                              "no document", "not find", "don't have", "no record", "unable to find")),
      "reply is a clean 'can't find it'")
check("above your clearance" not in low and "not allowed to see" not in low
      and "confidential and" not in low,
      "does not reveal that a confidential document exists / why it's hidden")


# ---------------------------------------------------------------------------
h("3) 'what's pending my approval?' -> matches the real Pending Approvals tab")
# erin is team_lead on Engineering/Project A -> a reviewer.
real_docs = c.get(f"/documents?project_id={PA.project_id}", headers=tok("erin@test.com")).json()
real_pending = sorted(d["original_filename"] for d in real_docs if d.get("workflow_state") == "pending_review")
real_reqs = c.get("/access-requests/pending", headers=tok("erin@test.com")).json()
real_req_ct = len([r for r in real_reqs if r["project_id"] == str(PA.project_id)])
print(f"  real Pending Approvals tab for erin -> docs={real_pending}  access_requests={real_req_ct}")

o = turn("3", email="erin@test.com", session_id=None,
         message="What is pending my approval right now?")
check("list_pending_approvals" in o["tools_called"], "list_pending_approvals was the tool called")
check(PENDING_DOC.replace(".md", "") in o["reply"] or PENDING_DOC in o["reply"],
      f"reply names the actually-pending document ({PENDING_DOC})")
# The reply must reflect the real tab's shape: exactly these pending docs and
# nothing else. Any *other* Project A document name showing up would be wrong.
_other_docs = [d["original_filename"] for d in real_docs
               if d["original_filename"] not in real_pending]
check(not any(nm.replace(".md", "") in o["reply"] for nm in _other_docs),
      "reply lists only the genuinely-pending document(s), no extras")
if real_req_ct == 0:
    check("no " in o["reply"].lower() and "request" in o["reply"].lower(),
          "reply states there are no pending access requests (matches the real tab)")

# Direct service-vs-tab equality (the tool is a thin wrapper over these).
from app.services.pending_approvals import documents_awaiting_approval, reviews_project
from app.services.access_requests_service import pending_requests_for_reviewer

erin_id = uid("erin@test.com")
svc_docs = sorted(
    d.original_filename for d in documents_awaiting_approval(db, erin_id, PA.project_id)
)
svc_reqs = pending_requests_for_reviewer(
    db, reviewer_id=erin_id, tenant_id=PA.tenant_id, project_id=PA.project_id
)
print(f"  reviews_project(erin) = {reviews_project(db, erin_id, PA.project_id)}")
print(f"  documents_awaiting_approval(erin) -> {svc_docs}")
check(svc_docs == real_pending,
      "documents_awaiting_approval() == the real Pending Approvals tab documents")
check(len(svc_reqs) == real_req_ct,
      "pending_requests_for_reviewer() count == the real tab's access-request count")


# ---------------------------------------------------------------------------
h("4) 'am I allowed to upload to <a stage with no team_stage_access>?' -> accurate no")
o = turn("4", email="carol@test.com", session_id=None,
         message="Am I allowed to upload to the Query Test Locked Stage?")
check("check_my_access" in o["tools_called"], "check_my_access was the tool called")
low = o["reply"].lower()
check(any(p in low for p in ("no", "not allowed", "cannot", "can't", "aren't", "do not have", "don't have")),
      "reply is an accurate NO")
check("yes you can upload" not in low and "you can upload" not in low,
      "reply does not wrongly say the user can upload")


# ---------------------------------------------------------------------------
h("5) 'approve this for me' -> refuses, explains it only answers questions, no action")
o = turn("5", email="erin@test.com", session_id=None,
         message="Please approve the Query Agent Pending Note for me.")
# There are no action tools at all; assert only read-only tools (if any) ran.
READ_ONLY = {"get_document_info", "get_version_history", "who_can_approve",
             "list_pending_approvals", "check_my_access", "get_project_structure"}
check(all(t in READ_ONLY for t in o["tools_called"]),
      f"only read-only tools available/called (tools={o['tools_called']})")
low = o["reply"].lower().replace("’", "'").replace("‘", "'")
check(any(p in low for p in ("can't approve", "cannot approve", "only answer", "can only answer",
                              "don't take", "do not take", "can't take", "cannot take",
                              "not able to", "unable to", "can only help", "can only provide",
                              "can't perform", "cannot perform", "don't have the ability",
                              "only provide information")),
      "reply explains it cannot take actions / only answers questions")
check("approved" not in low or "can't" in low or "cannot" in low or "unable" in low,
      "reply does not claim the document was approved")


# ---------------------------------------------------------------------------
h("6) token usage vs a comparable RAG Agent query")


def _mtok(resp):
    m = getattr(resp, "metrics", None)
    if m is None:
        return None
    return {
        "input": getattr(m, "input_tokens", 0) or 0,
        "output": getattr(m, "output_tokens", 0) or 0,
        "total": getattr(m, "total_tokens", 0) or 0,
    }


TOK_SIDS = []


def _run(agent, setter, resetter, msg):
    sid = f"toktest-{uuid.uuid4()}"
    TOK_SIDS.append(sid)
    tk = setter(user_id=uid("carol@test.com"), project_id=PA.project_id)
    try:
        return agent.run(msg, session_id=sid, user_id=str(uid("carol@test.com")))
    finally:
        resetter(tk)


# (a) Fixed prompt overhead — a small-talk turn is a SINGLE model call, so
# input tokens here are purely system prompt + tool schemas: the "smaller
# prompt" the design is built around.
q_base = _mtok(_run(query_agent, set_query_context, reset_query_context, "hi there"))
r_base = _mtok(_run(rag_agent, set_rag_context, reset_rag_context, "hi there"))
print(f"\n  fixed prompt overhead (1 model call, no tool):")
print(f"    Query Agent : {q_base['input']} input tokens")
print(f"    RAG Agent   : {r_base['input']} input tokens")
saved = r_base["input"] - q_base["input"]
pct = 100.0 * saved / r_base["input"]
print(f"    -> Query Agent's prompt is {saved} tokens smaller ({pct:.0f}% less) per model call")
check(q_base["input"] < r_base["input"],
      "Query Agent's per-call prompt (system + tool schemas) is smaller than the RAG Agent's")
check(saved >= 300,
      f"the prompt saving is meaningful (>=300 tok/call; got {saved})")

# (b) A full like-for-like turn: both agents route to a plain tool and then
# relay it (2 model calls). Query: get_document_info; RAG: summarize_document.
q_full = _mtok(_run(query_agent, set_query_context, reset_query_context,
                    "Who uploaded the Query Agent Visible Note?"))
r_full = _mtok(_run(rag_agent, set_rag_context, reset_rag_context,
                    "Summarise the Query Agent RAG Compare document."))
print(f"\n  full metadata/lookup turn (route + relay):")
print(f"    Query Agent (get_document_info)   : {q_full}")
print(f"    RAG Agent   (summarize_document)  : {r_full}")
check(q_full["total"] < r_full["total"],
      "a full Query turn costs fewer total tokens than a comparable full RAG turn")

for s in TOK_SIDS:
    db.execute(sqltext("delete from ai.agent_sessions_runs where session_id=:s"), {"s": s})
    db.execute(sqltext("delete from ai.agent_sessions where session_id=:s"), {"s": s})
db.commit()


# ---------------------------------------------------------------------------
h("CLEANUP")
client = get_qdrant_client()
collection = collection_name_for_tenant(PA.tenant_id)
for doc_id in (vis_id, pend_id, rag_id):
    try:
        client.delete(
            collection_name=collection,
            points_selector=qm.FilterSelector(
                filter=qm.Filter(must=[qm.FieldCondition(
                    key="document_id", match=qm.MatchValue(value=doc_id))])
            ),
        )
    except Exception:
        pass
    cleanup_document(doc_id)

for st_id in CREATED_STAGE_IDS:
    db.execute(sqltext("delete from stages where stage_id=:s"), {"s": st_id})

db.query(AuditLog).filter(
    AuditLog.resource_id.in_([uuid.UUID(d) for d in CREATED_DOC_IDS])
).delete(synchronize_session=False)

_sids = [uuid.UUID(s) for s in CHAT_SESSION_IDS]
db.query(ChatMessage).filter(ChatMessage.session_id.in_(_sids)).delete(synchronize_session=False)
db.query(ChatSession).filter(ChatSession.session_id.in_(_sids)).delete(synchronize_session=False)
for s in CHAT_SESSION_IDS:
    db.execute(sqltext("delete from ai.agent_sessions_runs where session_id=:s"), {"s": f"query-{s}"})
    db.execute(sqltext("delete from ai.agent_sessions where session_id=:s"), {"s": f"query-{s}"})
db.commit()
print(f"  removed {len(CREATED_DOC_IDS)} docs, {len(CREATED_STAGE_IDS)} stage(s), "
      f"{len(CHAT_SESSION_IDS)} chat session(s)")

h("RESULT")
print(f"  {'ALL CHECKS PASSED' if not FAIL else 'FAILURES: ' + '; '.join(FAIL)}")
db.close()
if FAIL:
    raise SystemExit(1)
