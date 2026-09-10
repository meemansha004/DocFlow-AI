"""
Verification for project team creation (POST /projects/{id}/teams) and its
gating, plus the "shows up in Assign Roles" guarantee.

FastAPI TestClient -> real Postgres.
"""

import time as _t

from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models.user import User
from app.models.team import Team, UserTeamMembership
from app.models.project import Project
from app.services.auth import create_session_token

c = TestClient(app)
db = SessionLocal()

def uid(email):
    return db.query(User).filter(User.email == email).one().user_id

def tok(email):
    return {"Authorization": f"Bearer {create_session_token(str(uid(email)))}"}

PA = db.query(Project).filter(Project.name == "Project A").one()
PID = str(PA.project_id)
NEW_TEAM = f"Platform {_t.strftime('%H%M%S')}"

FAIL = []
def check(cond, msg):
    print(f"  {'PASS' if cond else 'FAIL'}: {msg}")
    if not cond:
        FAIL.append(msg)

def h(t):
    print("\n" + "=" * 78 + f"\n {t}\n" + "=" * 78)


# ---------------------------------------------------------------------------
h("BASELINE — teams in Project A")
r = c.get(f"/projects/{PID}/teams", headers=tok("bob@test.com"))
print(f"  GET teams -> {r.status_code}")
for t in r.json():
    print(f"    {t['name']:<16} members={t['member_count']}  id={t['team_id']}")

# ---------------------------------------------------------------------------
h("1)  bob (project_admin) creates a team")
r = c.post(f"/projects/{PID}/teams", headers=tok("bob@test.com"), json={"name": NEW_TEAM})
print(f"  POST create '{NEW_TEAM}' -> {r.status_code} {r.json()}")
check(r.status_code == 201, "HTTP 201")
new_id = r.json().get("team_id")
row = db.query(Team).filter(Team.team_id == new_id).one_or_none() if new_id else None
check(row is not None, "real Team row exists in DB")
check(row is not None and str(row.project_id) == PID, "Team row is scoped to Project A")
check(r.json().get("member_count") == 0, "new team has 0 members")

# ---------------------------------------------------------------------------
h("2)  it appears in the team list + duplicate-name guard")
r = c.get(f"/projects/{PID}/teams", headers=tok("bob@test.com"))
names = [t["name"] for t in r.json()]
check(NEW_TEAM in names, f"'{NEW_TEAM}' now in GET /projects/{{id}}/teams  ({names})")
r = c.post(f"/projects/{PID}/teams", headers=tok("bob@test.com"), json={"name": NEW_TEAM.lower()})
check(r.status_code == 409, "duplicate team name (case-insensitive) -> 409")

# ---------------------------------------------------------------------------
h("3)  it shows up where the Assign Roles dropdown reads teams (GET /workspace)")
r = c.get("/workspace", headers=tok("bob@test.com"))
ws_pa = next((p for p in r.json()["projects"] if p["project_id"] == PID), None)
ws_team_names = [t["name"] for t in (ws_pa["teams"] if ws_pa else [])]
check(NEW_TEAM in ws_team_names, f"'{NEW_TEAM}' is in /workspace Project A teams  ({ws_team_names})")

# ...and Assign Roles can actually assign someone to it
r = c.post("/admin/assign-roles", headers=tok("bob@test.com"), json={
    "email": "carol@test.com", "mode": "team_member",
    "team_assignments": [{"team_id": new_id, "role": "contributor"}],
})
print(f"  assign carol contributor@'{NEW_TEAM}' -> {r.status_code} {r.json().get('message', r.json().get('detail'))}")
check(r.status_code == 200, "Assign Roles accepts the brand-new team_id")
db.expire_all()
mship = db.query(UserTeamMembership).filter(
    UserTeamMembership.user_id == uid("carol@test.com"),
    UserTeamMembership.team_id == new_id,
).one_or_none()
check(mship is not None, "UserTeamMembership row was created for the new team")
r = c.get(f"/projects/{PID}/teams", headers=tok("bob@test.com"))
mc = next((t["member_count"] for t in r.json() if t["team_id"] == new_id), None)
check(mc == 1, "team's member_count now reflects the assignment (1)")

# ---------------------------------------------------------------------------
h("4)  gating — non-admins are denied (403)")
for email, who in [("carol@test.com", "contributor"), ("erin@test.com", "team_lead"), ("dave@test.com", "team_lead (other team)")]:
    rc = c.post(f"/projects/{PID}/teams", headers=tok(email), json={"name": "Nope Team"})
    print(f"  {who:<24} create -> {rc.status_code}  {rc.json().get('detail','')[:60]}")
    check(rc.status_code == 403, f"{who} gets 403 on team creation")
# but a contributor CAN read the team list (needed to render the modal)
r = c.get(f"/projects/{PID}/teams", headers=tok("carol@test.com"))
check(r.status_code == 200, "contributor CAN read the team list (GET)")

# ---------------------------------------------------------------------------
h("CLEANUP")
db.query(UserTeamMembership).filter(UserTeamMembership.team_id == new_id).delete(synchronize_session=False)
from app.models.audit import AuditLog
db.query(AuditLog).filter(AuditLog.resource_id == new_id).delete(synchronize_session=False)
db.query(Team).filter(Team.team_id == new_id).delete(synchronize_session=False)
db.commit()
print(f"  removed team '{NEW_TEAM}', its membership, and its audit row")

h("RESULT")
print(f"  {'ALL CHECKS PASSED' if not FAIL else 'FAILURES: ' + '; '.join(FAIL)}")
db.close()
