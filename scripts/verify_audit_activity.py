"""
Role-by-role verification of the finalized Audit Log + Project Activity design.
Exercises the real HTTP surface (FastAPI TestClient -> real Postgres) as each role.

Ordering matters: the Project-Activity confidential check for rachel12 runs
BEFORE she is granted confidential access, then again AFTER.
"""

from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models.user import User
from app.models.team import Team, AccessRequest, AccessRequestStatus
from app.models.project import Project
from app.models.stage import Stage
from app.models.document import Document
from app.services.auth import create_session_token

c = TestClient(app)
db = SessionLocal()

def uid(email):
    return db.query(User).filter(User.email == email).one().user_id

def tok(email):
    return {"Authorization": f"Bearer {create_session_token(str(uid(email)))}"}

PA = db.query(Project).filter(Project.name == "Project A").one()
ENG = db.query(Team).filter(Team.name == "Engineering", Team.project_id == PA.project_id).one()
QA = db.query(Team).filter(Team.name == "QA", Team.project_id == PA.project_id).one()
DESIGN = db.query(Team).filter(Team.name == "Design", Team.project_id == PA.project_id).one()
DEV_STAGE = db.query(Stage).filter(Stage.name == "Development", Stage.project_id == PA.project_id).one()
TEST_STAGE = db.query(Stage).filter(Stage.name == "Testing", Stage.project_id == PA.project_id).one()

def h(t):
    print("\n" + "=" * 78 + f"\n {t}\n" + "=" * 78)

def approve_pending_request(requester_email, team, approver_email):
    db.expire_all()
    req = db.query(AccessRequest).filter(
        AccessRequest.user_id == uid(requester_email), AccessRequest.team_id == team.team_id,
    ).order_by(AccessRequest.requested_at.desc()).first()
    if req and req.status == AccessRequestStatus.pending:
        r = c.post(f"/access-requests/{req.request_id}/approve", headers=tok(approver_email))
        return f"approved ({r.status_code})"
    return f"status={req.status.value if req else 'none'}"


# ---------------------------------------------------------------------------
h("SETUP  (idempotent — reuses anything already created)")

r = c.post("/admin/users", headers=tok("alice@test.com"),
           json={"email": "vic@test.com", "full_name": "Vic Viewer"})
print(f"create vic@test.com ................. {r.status_code}")
r = c.post("/admin/assign-roles", headers=tok("alice@test.com"), json={
    "email": "vic@test.com", "mode": "team_member",
    "team_assignments": [{"team_id": str(ENG.team_id), "role": "viewer"}]})
print(f"vic -> viewer @ Engineering/A ....... {r.status_code}")

CONF = "Confidential Arch Spec"
if not db.query(Document).filter(Document.original_filename == CONF + ".md",
                                 Document.uploaded_as_team_id == ENG.team_id).first():
    r = c.post("/documents/upload", headers=tok("erin@test.com"), json={
        "document_type": CONF, "stage_id": str(DEV_STAGE.stage_id),
        "content": "# secret", "team_id": str(ENG.team_id), "sensitivity_level": "confidential"})
    print(f"erin upload '{CONF}' (confidential) . {r.status_code} sens={r.json().get('sensitivity_level')}")
else:
    print(f"'{CONF}' .......................... already present")

NOTES = "Sprint Notes"
if not db.query(Document).filter(Document.original_filename == NOTES + ".md",
                                 Document.uploaded_as_team_id == ENG.team_id).first():
    r = c.post("/documents/upload", headers=tok("carol@test.com"), json={
        "document_type": NOTES, "stage_id": str(TEST_STAGE.stage_id),
        "content": "# notes", "team_id": str(ENG.team_id), "sensitivity_level": "internal"})
    did = r.json()["document_id"]
    c.post(f"/documents/{did}/submit", headers=tok("carol@test.com"))
    c.post(f"/documents/{did}/approve", headers=tok("erin@test.com"))
    print(f"carol upload+submit '{NOTES}', erin approve (internal, Testing)")
else:
    print(f"'{NOTES}' workflow doc ............ already present")

r = c.post("/admin/assign-roles", headers=tok("alice@test.com"), json={
    "email": "rachel12@test.com", "mode": "team_member",
    "team_assignments": [{"team_id": str(QA.team_id), "role": "contributor"}]})
print(f"alice assigns rachel12 contributor @ QA/A ... {r.status_code}  (ASSIGN_ROLE, scope QA)")

# access-request lifecycle events for the audit log (carol gets the grant here)
r = c.post("/access-requests", headers=tok("carol@test.com"), json={"team_id": str(ENG.team_id)})
print(f"carol requests confidential @ Eng ... {r.status_code} {r.json().get('detail','ok')}")
print(f"erin decides carol's request ........ {approve_pending_request('carol@test.com', ENG, 'erin@test.com')}")


# ===========================================================================
h("1)  AUDIT LOG  —  GET /admin/audit-log")

def audit_as(email):
    r = c.get("/admin/audit-log", headers=tok(email))
    print(f"\n  {email}  ->  HTTP {r.status_code}")
    if r.status_code != 200:
        print(f"     {r.json().get('detail')}")
        return
    for e in r.json():
        out = f"  outcome={e['status']}" if e.get("status") else ""
        print(f"     {e['created_at'][:19]}  {e['action']:<27}{(e['details'] or '')[:64]}{out}")

for w in ["alice@test.com", "bob@test.com", "dave@test.com", "erin@test.com",
          "carol@test.com", "vic@test.com"]:
    audit_as(w)
print("""
  EXPECT: alice=tenant-wide | bob=Project-A only | dave=QA-scope only |
          erin=Engineering-scope only | carol=403 | vic=403 | no LOGIN rows""")


# ===========================================================================
h("2)  PROJECT ACTIVITY  —  GET /activity/projects  (which cards + teams)")

for w in ["alice@test.com", "bob@test.com", "erin@test.com", "carol@test.com",
          "rachel12@test.com", "monika24@test.com", "vic@test.com"]:
    r = c.get("/activity/projects", headers=tok(w))
    cards = r.json() if r.status_code == 200 else []
    d = " | ".join(f"{p['project_name']}({'admin' if p['admin_here'] else 'member'}:"
                   f"{','.join(t['name'] for t in p['teams'])})" for p in cards)
    print(f"  {w:<20} HTTP {r.status_code}  {d or '(no cards — viewer-only)'}")


# ---- fresh contributor with NO confidential grant, created this run --------
import time as _t
PROBE = f"probe{int(_t.time())}@test.com"
c.post("/admin/users", headers=tok("alice@test.com"), json={"email": PROBE, "full_name": "Probe Contributor"})
c.post("/admin/assign-roles", headers=tok("alice@test.com"), json={
    "email": PROBE, "mode": "team_member",
    "team_assignments": [{"team_id": str(ENG.team_id), "role": "contributor"}]})
print(f"\ncreated fresh contributor {PROBE} on Engineering/A (no confidential grant)")


# ===========================================================================
h("3)  PROJECT ACTIVITY feed — Engineering/ProjectA  (probe contributor has NO grant)")

def feed_as(email, team, label):
    r = c.get("/activity", headers=tok(email),
              params={"project_id": str(PA.project_id), "team_id": str(team.team_id)})
    print(f"\n  {email}  (team={label})  ->  HTTP {r.status_code}")
    if r.status_code != 200:
        print(f"     {r.json().get('detail')}")
        return
    rows = r.json()
    seen = sorted({e["filename"] for e in rows})
    conf = "  <-- SEES CONFIDENTIAL" if any("Confidential" in f for f in seen) else ""
    print(f"     {len(rows)} entries; docs={seen}{conf}")
    for e in rows:
        print(f"       {e['action']:<17} {e['filename']:<27} stage={e['stage']:<12} "
              f"sens={e['sensitivity_level']:<12} status={e['status']}")

for w in ["alice@test.com", "bob@test.com", "erin@test.com", PROBE, "vic@test.com", "monika24@test.com"]:
    feed_as(w, ENG, "Engineering/A")
print(f"""
  KEY ASSERTION (point 9): 'Confidential Arch Spec' visible to
     alice (org_admin), bob (project_admin), erin (team_lead)
     — but NOT {PROBE} (contributor on the team, no confidential grant).
     vic (viewer) / monika24 (not on this team) = 403.""")


# ===========================================================================
h(f"4)  Grant {PROBE} confidential access on Engineering, then re-check the feed")
r = c.post("/access-requests", headers=tok(PROBE), json={"team_id": str(ENG.team_id)})
print(f"  {PROBE} requests confidential @ Eng ... {r.status_code} {r.json().get('detail','ok')}")
print(f"  erin decides the request ............. {approve_pending_request(PROBE, ENG, 'erin@test.com')}")
feed_as(PROBE, ENG, "Engineering/A")
print(f"\n  EXPECT: {PROBE} NOW sees 'Confidential Arch Spec' (active grant).")


# ===========================================================================
h("5)  Role-scoping spot check — monika24 is team_lead on Design/A (own team ok)")
feed_as("monika24@test.com", DESIGN, "Design/A")
print("  EXPECT: HTTP 200 (0 entries — no docs in Design yet, but access is allowed).")

db.close()
print("\nDONE.")
