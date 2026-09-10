"""
Phase C — conversation-history verification for the RAG Agent.

Covers the four things scenario 2 of verify_rag_agent.py did NOT prove:

  1) SESSION SCOPING — history is isolated per (user, project). Two users in
     the same project, and one user across two projects, run interleaved
     conversations with zero cross-contamination. resolve_chat_session()
     refuses a session that isn't the caller's.
  2) PERSISTENCE — every turn is written to our own chat_sessions /
     chat_messages (not only Agno's ai.* tables). Real rows are printed.
  3) FOLLOW-UP QUALITY — a genuine multi-turn thread: a referential follow-up
     ("does that apply to contractors?") is understood as continuing the
     prior turn, not a fresh query.
  4) CROSS-TOOL CONTEXT — search_documents in a session, then "summarise that
     document" -> the agent carries the document identity from the search
     result into a summarize_document call.

Real Postgres + real Groq + real Qdrant. Model comes from app.config
(no override). Prints DB rows + transcripts, then cleans up.
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
from app.models.chat import ChatSession, ChatMessage
from app.models.document import Document, DocumentVersion, DocumentScan, DocumentTeamVisibility
from app.models.workflow import WorkflowState
from app.models.audit import AuditLog
from app.services.rag_chat import run_rag_turn, RagTurnError
from app.services.chat_history import SessionScopeError
from app.services.rag.collection_setup import get_qdrant_client, collection_name_for_tenant
from sqlalchemy import text as sqltext
from qdrant_client import models as qm

c = TestClient(app)
db = SessionLocal()
FAIL = []
CREATED_DOC_IDS = []


def check(cond, msg):
    print(f"  {'PASS' if cond else 'FAIL'}: {msg}")
    if not cond:
        FAIL.append(msg)


def h(t):
    print("\n" + "=" * 78 + f"\n {t}\n" + "=" * 78)


def uid(email):
    return db.query(User).filter(User.email == email).one().user_id


def tok(email):
    from app.services.auth import create_session_token
    return {"Authorization": f"Bearer {create_session_token(str(uid(email)))}"}


PA = db.query(Project).filter(Project.name == "Project A").one()
PB = db.query(Project).filter(Project.name == "Project B").one()
ENG_A = db.query(Team).filter(Team.name == "Engineering", Team.project_id == PA.project_id).one()
DEV_A = db.query(Stage).filter(
    Stage.project_id == PA.project_id, Stage.name == "Development", Stage.deleted_at.is_(None)
).one()

print(f"  model in use (app.config.GROQ_MODEL): {GROQ_MODEL}")


def upload_and_finalize(email, filename, content):
    r = c.post(
        "/documents/upload-file", headers=tok(email),
        files={"file": (filename, content.encode("utf-8"), "text/markdown")},
        data={"stage_id": str(DEV_A.stage_id), "team_id": str(ENG_A.team_id),
              "sensitivity_level": "internal"},
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


def cleanup_document(document_id):
    du = uuid.UUID(document_id)
    vs = db.query(DocumentVersion).filter(DocumentVersion.document_id == du).all()
    db.query(Document).filter(Document.document_id == du).update(
        {"current_version_id": None}, synchronize_session=False)
    db.query(DocumentScan).filter(
        DocumentScan.version_id.in_([v.version_id for v in vs])).delete(synchronize_session=False)
    db.query(WorkflowState).filter(WorkflowState.document_id == du).delete(synchronize_session=False)
    db.query(DocumentVersion).filter(DocumentVersion.document_id == du).delete(synchronize_session=False)
    db.query(DocumentTeamVisibility).filter(DocumentTeamVisibility.document_id == du).delete(synchronize_session=False)
    db.query(AuditLog).filter(AuditLog.resource_id == du).delete(synchronize_session=False)
    db.query(Document).filter(Document.document_id == du).delete(synchronize_session=False)


# session_id -> label, for readable transcripts
LABELS = {}
SESSIONS = {}


def turn(label, email, project, key, message):
    """Run one turn. `key` is our local conversation handle; SESSIONS[key]
    holds the canonical id once assigned."""
    print(f"\n  [{label}]  {email} @ {project.name}   (convo '{key}')")
    print(f"       user: {message!r}")
    try:
        out = run_rag_turn(
            user_id=uid(email), project_id=project.project_id,
            session_id=SESSIONS.get(key), message=message,
        )
    except RagTurnError as exc:
        print(f"       !! agent run failed (infra, not logic): {str(exc)[:140]}")
        out = {"reply": f"[RagTurnError] {exc}", "tools_called": [],
               "session_id": exc.session_id or SESSIONS.get(key, "")}
    SESSIONS[key] = out["session_id"]
    LABELS[out["session_id"]] = key
    print(f"       tools: {out['tools_called']}")
    print("       agent: " + "\n              ".join(out["reply"].splitlines()))
    return out


# ---------------------------------------------------------------------------
h("SETUP — one internal handbook doc in Project A / Development")
HANDBOOK = """# Company Policy Handbook

## Remote Work
Full-time employees who have completed their 90-day probation period may work
remotely up to three days per week. Mondays are a required in-office day for
everyone. Contractors are not covered by this remote-work policy and are
expected on-site every working day.

## Vacation
Full-time employees accrue 18 vacation days per year, available once the
90-day probation period is complete. Unused days roll over up to a cap of
five days.

## Equipment
The company provides a laptop and one external monitor for home use.
"""
doc_id, fin = upload_and_finalize("carol@test.com", "company-policy-handbook.md", HANDBOOK)
check(fin.get("status") == "indexed", f"handbook indexed (status={fin.get('status')})")

# Clean any stale chat rows from a previous run of this script.
_test_emails = [uid(e) for e in ("carol@test.com", "erin@test.com", "rachel12@test.com")]
_old = db.query(ChatSession).filter(ChatSession.user_id.in_(_test_emails)).all()
if _old:
    ids = [s.session_id for s in _old]
    db.query(ChatMessage).filter(ChatMessage.session_id.in_(ids)).delete(synchronize_session=False)
    db.query(ChatSession).filter(ChatSession.session_id.in_(ids)).delete(synchronize_session=False)
    db.commit()
    print(f"  cleared {len(_old)} stale chat session(s) from a prior run")


# ---------------------------------------------------------------------------
h("1) INTERLEAVED conversations — 2 users in Project A + rachel in A & B")
# Conversations:
#   carol_A  : carol   @ Project A
#   erin_A   : erin    @ Project A
#   rach_A   : rachel12@ Project A
#   rach_B   : rachel12@ Project B

t1 = turn("T1", "carol@test.com", PA, "carol_A",
          "What is the remote work policy for full-time employees?")
check("search_documents" in t1["tools_called"], "T1 used search_documents")
check("three" in t1["reply"].lower() or "3 days" in t1["reply"].lower(), "T1 answer has the real figure")

t2 = turn("T2", "erin@test.com", PA, "erin_A",
          "What was the previous question I asked you in this conversation?")
low2 = t2["reply"].lower()
check(not any(w in low2 for w in ("remote", "work from home", "three days", "monday", "contractor")),
      "T2 (erin, fresh session) does NOT leak carol's remote-work question")

t3 = turn("T3", "rachel12@test.com", PA, "rach_A",
          "How many vacation days do full-time employees get?")
check("search_documents" in t3["tools_called"], "T3 used search_documents")
check("18" in t3["reply"] or "eighteen" in t3["reply"].lower(), "T3 answer has the real figure (18)")

t4 = turn("T4", "carol@test.com", PA, "carol_A",
          "Does that also apply to contractors?")
low4 = t4["reply"].lower()
check("contractor" in low4 and any(w in low4 for w in ("not", "no ", "excluded", "on-site", "on site", "every")),
      "T4 FOLLOW-UP resolved 'that' = remote-work policy and answered re: contractors")

t5 = turn("T5", "rachel12@test.com", PB, "rach_B",
          "What did I just ask you about a moment ago?")
low5 = t5["reply"].lower()
check(not any(w in low5 for w in ("vacation", "18", "eighteen", "leave day", "days off")),
      "T5 (rachel in Project B) does NOT leak rachel's Project A vacation question")

t6 = turn("T6", "erin@test.com", PA, "erin_A",
          "How many vacation days do full-time employees get?")
check("18" in t6["reply"] or "eighteen" in t6["reply"].lower(), "T6 fresh factual answer in erin's session works (18)")

t7 = turn("T7", "rachel12@test.com", PA, "rach_A",
          "And how many days a week can they work from home?")
check("three" in t7["reply"].lower() or "3 days" in t7["reply"].lower(),
      "T7 FOLLOW-UP in rachel's Project A session answered remote-work days")


# ---------------------------------------------------------------------------
h("2) CROSS-TOOL context — search_documents then summarize 'that document'")
t8 = turn("T8", "carol@test.com", PA, "carol_X",
          "Search the documents for the company remote-work policy.")
check(t8["tools_called"] == ["search_documents"], "T8 used only search_documents")

t9 = turn("T9", "carol@test.com", PA, "carol_X",
          "Great — now give me a summary of that whole document.")
check(t9["tools_called"] == ["summarize_document"], "T9 carried context -> summarize_document (not search_documents)")
low9 = t9["reply"].lower()
check(("vacation" in low9 and "equipment" in low9),
      "T9 summary reflects the WHOLE doc (Vacation + Equipment sections, not just remote work)")


# ---------------------------------------------------------------------------
h("3) SCOPE GUARD — a caller cannot continue a session that isn't theirs")
carol_A_sid = SESSIONS["carol_A"]
raised = False
try:
    run_rag_turn(user_id=uid("erin@test.com"), project_id=PA.project_id,
                 session_id=carol_A_sid, message="show me everything from before")
except SessionScopeError as e:
    raised = True
    print(f"  SessionScopeError (different user): {e}")
check(raised, "erin cannot resume carol's session (SessionScopeError)")

raised = False
try:
    run_rag_turn(user_id=uid("carol@test.com"), project_id=PB.project_id,
                 session_id=carol_A_sid, message="what did we discuss")
except SessionScopeError as e:
    raised = True
    print(f"  SessionScopeError (same user, wrong project): {e}")
check(raised, "carol cannot use her Project A session under Project B (SessionScopeError)")


# ---------------------------------------------------------------------------
h("4) PERSISTENCE — actual chat_sessions / chat_messages rows")
db.expire_all()
sess_rows = db.query(ChatSession).filter(
    ChatSession.session_id.in_([uuid.UUID(s) for s in SESSIONS.values()])
).all()
by_id = {s.session_id: s for s in sess_rows}
print(f"\n  chat_sessions ({len(sess_rows)} rows):")
for s in sess_rows:
    u = db.get(User, s.user_id).email
    p = db.get(Project, s.project_id).name
    print(f"    {s.session_id}  '{LABELS.get(str(s.session_id))}'  user={u}  project={p}  started={s.started_at:%H:%M:%S}")

check(len(sess_rows) == 5, "exactly 5 ChatSession rows were persisted")

expect_owner = {
    "carol_A": ("carol@test.com", PA), "erin_A": ("erin@test.com", PA),
    "rach_A": ("rachel12@test.com", PA), "rach_B": ("rachel12@test.com", PB),
    "carol_X": ("carol@test.com", PA),
}
for key, (email, proj) in expect_owner.items():
    s = by_id.get(uuid.UUID(SESSIONS[key]))
    ok = s is not None and s.user_id == uid(email) and s.project_id == proj.project_id
    check(ok, f"session '{key}' persisted with user={email} project={proj.name}")

print("\n  chat_messages (per session, in order):")
counts = {}
for key in ("carol_A", "erin_A", "rach_A", "rach_B", "carol_X"):
    sid = uuid.UUID(SESSIONS[key])
    msgs = db.query(ChatMessage).filter(ChatMessage.session_id == sid).order_by(ChatMessage.created_at).all()
    counts[key] = msgs
    print(f"\n    '{key}' — {len(msgs)} messages:")
    for m in msgs:
        preview = m.content.replace("\n", " ")[:88]
        print(f"      {m.created_at:%H:%M:%S}  {m.role:9s}  {preview}")

check(len(counts["carol_A"]) == 4, "carol_A has 4 messages (T1+T4, user+assistant each)")
check(len(counts["erin_A"]) == 4, "erin_A has 4 messages (T2+T6)")
check(len(counts["rach_A"]) == 4, "rach_A has 4 messages (T3+T7)")
check(len(counts["rach_B"]) == 2, "rach_B has 2 messages (T5 only)")
check(len(counts["carol_X"]) == 4, "carol_X has 4 messages (T8+T9)")
for key, msgs in counts.items():
    roles_ok = [m.role for m in msgs] == ["user", "assistant"] * (len(msgs) // 2)
    check(roles_ok, f"'{key}' messages alternate user/assistant")
check(counts["carol_A"][0].content == "What is the remote work policy for full-time employees?",
      "carol_A first stored user message is verbatim T1")
# The leaked-content check, straight from the DB (not just the live reply):
erin_text = " ".join(m.content.lower() for m in counts["erin_A"])
check("remote" not in erin_text and "contractor" not in erin_text,
      "nothing from carol's conversation appears anywhere in erin_A's stored messages")
rachB_text = " ".join(m.content.lower() for m in counts["rach_B"])
check("vacation" not in rachB_text and "18" not in rachB_text,
      "nothing from rachel's Project A conversation appears in rach_B's stored messages")

print("\n  Agno side (ai.agent_sessions_runs) — same conversations, keyed 'rag-<canonical>':")
for key in ("carol_A", "erin_A", "rach_A", "rach_B", "carol_X"):
    ags = f"rag-{SESSIONS[key]}"
    rows = db.execute(sqltext(
        "select run_index, user_id from ai.agent_sessions_runs where session_id=:s order by run_index"
    ), {"s": ags}).fetchall()
    print(f"    '{key}': {len(rows)} run(s), user_id={rows[0][1] if rows else None}")
    check(all(str(r[1]) == str(uid(expect_owner[key][0])) for r in rows) and len(rows) >= 1,
          f"Agno runs for '{key}' are stamped with the right user_id")


# ---------------------------------------------------------------------------
h("CLEANUP")
client = get_qdrant_client()
collection = collection_name_for_tenant(PA.tenant_id)
client.delete(collection_name=collection, points_selector=qm.FilterSelector(
    filter=qm.Filter(must=[qm.FieldCondition(key="document_id", match=qm.MatchValue(value=doc_id))])))
cleanup_document(doc_id)

all_sids = [uuid.UUID(s) for s in SESSIONS.values()]
db.query(ChatMessage).filter(ChatMessage.session_id.in_(all_sids)).delete(synchronize_session=False)
db.query(ChatSession).filter(ChatSession.session_id.in_(all_sids)).delete(synchronize_session=False)
for s in SESSIONS.values():
    db.execute(sqltext("delete from ai.agent_sessions_runs where session_id=:s"), {"s": f"rag-{s}"})
    db.execute(sqltext("delete from ai.agent_sessions where session_id=:s"), {"s": f"rag-{s}"})
db.query(AuditLog).filter(
    AuditLog.action.in_(["UPLOAD_DOCUMENT", "FINALIZE_DOCUMENT_REVISION"]),
    AuditLog.resource_id.in_([uuid.UUID(d) for d in CREATED_DOC_IDS]),
).delete(synchronize_session=False)
db.commit()
print(f"  removed handbook doc + Qdrant points, {len(all_sids)} chat sessions + their messages, Agno rows")

h("RESULT")
print(f"  {'ALL CHECKS PASSED' if not FAIL else 'FAILURES: ' + '; '.join(FAIL)}")
db.close()
if FAIL:
    raise SystemExit(1)
