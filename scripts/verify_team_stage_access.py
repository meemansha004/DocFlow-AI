"""
Verification for Phase A / Part 3 — team_stage_access.

FastAPI TestClient -> real Postgres. Covers: bob (project_admin) granting
Engineering access to Testing + Development only; carol (contributor,
Engineering) can upload to those two and is denied (clear error) on
Design/Requirements; carol's stage list is filtered to exactly those two;
erin (team_lead Engineering + viewer Design) sees the UNION of both teams'
accessible stages; alice (org_admin) and bob (project_admin) bypass entirely
— see/upload to ALL stages regardless of team_stage_access.
"""

from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models.user import User
from app.models.stage import Stage, TeamStageAccess
from app.models.team import Team
from app.models.project import Project
from app.models.document import Document
from app.services.auth import create_session_token
from app.services.access_control import get_accessible_stages_for_user, has_stage_access

c = TestClient(app)
db = SessionLocal()


def uid(email):
    return db.query(User).filter(User.email == email).one().user_id


def tok(email):
    return {"Authorization": f"Bearer {create_session_token(str(uid(email)))}"}


PA = db.query(Project).filter(Project.name == "Project A").one()
PID = str(PA.project_id)


def stage(name):
    return db.query(Stage).filter(
        Stage.project_id == PA.project_id, Stage.name == name, Stage.deleted_at.is_(None)
    ).one()


def team(name):
    return db.query(Team).filter(Team.project_id == PA.project_id, Team.name == name).one()


REQUIREMENTS = stage("Requirements")
DESIGN_STAGE = stage("Design")
DEVELOPMENT = stage("Development")
TESTING = stage("Testing")
ENGINEERING = team("Engineering")
DESIGN_TEAM = team("Design")

FAIL = []


def check(cond, msg):
    print(f"  {'PASS' if cond else 'FAIL'}: {msg}")
    if not cond:
        FAIL.append(msg)


def h(t):
    print("\n" + "=" * 78 + f"\n {t}\n" + "=" * 78)


def put_team_access(email, stage_id, team_ids):
    return c.put(
        f"/projects/{PID}/stages/{stage_id}/team-access",
        headers=tok(email), json={"team_ids": [str(x) for x in team_ids]},
    )


def upload(email, team_id, stage_id, doc_type="Note"):
    return c.post(
        "/documents/upload", headers=tok(email),
        json={
            "document_type": doc_type, "stage_id": str(stage_id),
            "content": "test content", "team_id": str(team_id),
            "sensitivity_level": "internal",
        },
    )


TOUCHED_STAGE_IDS = [TESTING.stage_id, DEVELOPMENT.stage_id, DESIGN_STAGE.stage_id, REQUIREMENTS.stage_id]

# Snapshot + clear the real baseline (scripts/backfill_team_stage_access.py's
# grants included) for a clean slate, so this script's narrow Engineering-only
# / Design-only scenario isn't polluted by broader pre-existing access. CLEANUP
# at the end restores exactly this snapshot — running this script must NOT
# permanently erase the backfilled baseline other teams rely on.
baseline_snapshot = [
    (r.team_id, r.stage_id)
    for r in db.query(TeamStageAccess).filter(TeamStageAccess.stage_id.in_(TOUCHED_STAGE_IDS)).all()
]
db.query(TeamStageAccess).filter(TeamStageAccess.stage_id.in_(TOUCHED_STAGE_IDS)).delete(
    synchronize_session=False
)
db.commit()


# ---------------------------------------------------------------------------
h("1)  bob (project_admin) grants Engineering access to Testing + Development only")
r = put_team_access("bob@test.com", TESTING.stage_id, [ENGINEERING.team_id])
check(r.status_code == 200, f"PUT Testing team-access -> {r.status_code}")
check(r.json().get("team_access") == [str(ENGINEERING.team_id)], "Testing.team_access == [Engineering]")
r = put_team_access("bob@test.com", DEVELOPMENT.stage_id, [ENGINEERING.team_id])
check(r.status_code == 200, f"PUT Development team-access -> {r.status_code}")
check(r.json().get("team_access") == [str(ENGINEERING.team_id)], "Development.team_access == [Engineering]")
db.expire_all()
check(
    db.query(TeamStageAccess).filter(
        TeamStageAccess.team_id == ENGINEERING.team_id,
        TeamStageAccess.stage_id.in_([TESTING.stage_id, DEVELOPMENT.stage_id]),
    ).count() == 2,
    "2 team_stage_access rows exist (Engineering x Testing, Engineering x Development)",
)
check(
    db.query(TeamStageAccess).filter(
        TeamStageAccess.team_id == ENGINEERING.team_id,
        TeamStageAccess.stage_id.in_([DESIGN_STAGE.stage_id, REQUIREMENTS.stage_id]),
    ).count() == 0,
    "no Engineering access rows for Design/Requirements",
)

# ---------------------------------------------------------------------------
h("2)  carol (contributor, Engineering) can upload to Testing/Development")
r = upload("carol@test.com", ENGINEERING.team_id, TESTING.stage_id, "Test Plan")
check(r.status_code == 201, f"carol upload to Testing -> {r.status_code}")
carol_testing_doc_id = r.json().get("document_id") if r.status_code == 201 else None
r = upload("carol@test.com", ENGINEERING.team_id, DEVELOPMENT.stage_id, "Dev Notes")
check(r.status_code == 201, f"carol upload to Development -> {r.status_code}")
carol_dev_doc_id = r.json().get("document_id") if r.status_code == 201 else None

# ---------------------------------------------------------------------------
h("3)  carol CANNOT upload to Design or Requirements (clear error)")
r = upload("carol@test.com", ENGINEERING.team_id, DESIGN_STAGE.stage_id, "Design Doc")
check(r.status_code == 403, f"carol upload to Design -> {r.status_code}")
check(
    "does not have access" in r.json().get("detail", "").lower(),
    f"clear error message: {r.json().get('detail', '')!r}",
)
r = upload("carol@test.com", ENGINEERING.team_id, REQUIREMENTS.stage_id, "Reqs Doc")
check(r.status_code == 403, f"carol upload to Requirements -> {r.status_code}")
check(
    "does not have access" in r.json().get("detail", "").lower(),
    f"clear error message: {r.json().get('detail', '')!r}",
)

# ---------------------------------------------------------------------------
h("4)  carol's stage list / dropdowns only show Testing + Development")
r = c.get(f"/projects/{PID}/stages", headers=tok("carol@test.com"))
carol_stage_names = {s["name"] for s in r.json()}
check(r.status_code == 200, "GET /stages as carol -> 200")
check(carol_stage_names == {"Testing", "Development"}, f"carol sees exactly {{Testing, Development}}, got {carol_stage_names}")

ws = c.get("/workspace", headers=tok("carol@test.com")).json()
ws_pa = next(p for p in ws["projects"] if p["project_id"] == PID)
carol_ws_names = {s["name"] for s in ws_pa["stages"]}
check(carol_ws_names == {"Testing", "Development"}, f"GET /workspace stages for carol == {{Testing, Development}}, got {carol_ws_names}")

resolved = set(get_accessible_stages_for_user(db, uid("carol@test.com"), PA.project_id))
check(resolved == {TESTING.stage_id, DEVELOPMENT.stage_id}, "get_accessible_stages_for_user(carol) == {Testing, Development}")

# ---------------------------------------------------------------------------
h("5)  erin (team_lead Engineering + viewer Design) sees the UNION of both teams' access")
# Grant Design team access to Design + Requirements stages, so erin's union
# spans both her teams' grants.
r = put_team_access("bob@test.com", DESIGN_STAGE.stage_id, [DESIGN_TEAM.team_id])
check(r.status_code == 200, f"PUT Design team-access (Design team) -> {r.status_code}")
r = put_team_access("bob@test.com", REQUIREMENTS.stage_id, [DESIGN_TEAM.team_id])
check(r.status_code == 200, f"PUT Requirements team-access (Design team) -> {r.status_code}")

r = c.get(f"/projects/{PID}/stages", headers=tok("erin@test.com"))
erin_stage_names = {s["name"] for s in r.json()}
check(
    erin_stage_names == {"Testing", "Development", "Design", "Requirements"},
    f"erin (Engineering+Design) sees the UNION of all 4 granted stages, got {erin_stage_names}",
)
resolved_erin = set(get_accessible_stages_for_user(db, uid("erin@test.com"), PA.project_id))
check(
    resolved_erin == {TESTING.stage_id, DEVELOPMENT.stage_id, DESIGN_STAGE.stage_id, REQUIREMENTS.stage_id},
    "get_accessible_stages_for_user(erin) == union of Engineering + Design grants",
)
# erin (team_lead on Engineering) can also upload to Testing (has_permission + has_stage_access both pass)
r = upload("erin@test.com", ENGINEERING.team_id, TESTING.stage_id, "Erin Test Doc")
check(r.status_code == 201, f"erin upload to Testing as Engineering -> {r.status_code}")
erin_doc_id = r.json().get("document_id") if r.status_code == 201 else None

# ---------------------------------------------------------------------------
h("6)  alice (org_admin) and bob (project_admin) bypass entirely — ALL stages, ALL uploads")
r = c.get(f"/projects/{PID}/stages", headers=tok("alice@test.com"))
alice_names = {s["name"] for s in r.json()}
check(alice_names == {"Requirements", "Design", "Development", "Testing"}, f"alice sees ALL 4 stages, got {alice_names}")

r = c.get(f"/projects/{PID}/stages", headers=tok("bob@test.com"))
bob_names = {s["name"] for s in r.json()}
check(bob_names == {"Requirements", "Design", "Development", "Testing"}, f"bob sees ALL 4 stages, got {bob_names}")

resolved_alice = set(get_accessible_stages_for_user(db, uid("alice@test.com"), PA.project_id))
check(
    resolved_alice == {TESTING.stage_id, DEVELOPMENT.stage_id, DESIGN_STAGE.stage_id, REQUIREMENTS.stage_id},
    "get_accessible_stages_for_user(alice, org_admin) == ALL active stages (bypass)",
)

# alice/bob can upload anywhere, even to a stage with ZERO team_stage_access
# grants for the team they act as (bob acting as QA, which has no grants at all)
QA = team("QA")
check(
    db.query(TeamStageAccess).filter(TeamStageAccess.team_id == QA.team_id).count() == 0,
    "sanity: QA team has NO team_stage_access grants anywhere",
)
r = upload("bob@test.com", QA.team_id, DESIGN_STAGE.stage_id, "Bob Admin Doc")
check(r.status_code == 201, f"bob (project_admin, acting as ungranted QA team) uploads to Design -> {r.status_code} (bypass)")
bob_doc_id = r.json().get("document_id") if r.status_code == 201 else None

check(
    has_stage_access(db, uid("alice@test.com"), QA.team_id, DESIGN_STAGE.stage_id, PA.project_id) is True,
    "has_stage_access(alice, org_admin) == True even for an ungranted team/stage pair (bypass)",
)

# ---------------------------------------------------------------------------
h("7)  deleting a stage drops its team_stage_access rows (no dangling grants)")
# Use a scratch stage so we don't disturb the 4 real ones.
scratch = Stage(project_id=PA.project_id, name="__scratch_tsa__", order_index=99, requires_approval=False)
db.add(scratch)
db.commit()
db.add(TeamStageAccess(team_id=ENGINEERING.team_id, stage_id=scratch.stage_id))
db.commit()
r = c.delete(f"/projects/{PID}/stages/{scratch.stage_id}", headers=tok("bob@test.com"))
check(r.status_code == 200, f"delete scratch stage -> {r.status_code}")
db.expire_all()
check(
    db.query(TeamStageAccess).filter(TeamStageAccess.stage_id == scratch.stage_id).count() == 0,
    "team_stage_access rows for the deleted stage are gone",
)

# ---------------------------------------------------------------------------
h("CLEANUP")
# Restore the exact pre-test baseline (backfill_team_stage_access.py's grants
# included) rather than leaving the table empty — this script must be safe to
# rerun without eroding real access other teams depend on.
db.query(TeamStageAccess).filter(TeamStageAccess.stage_id.in_(TOUCHED_STAGE_IDS)).delete(
    synchronize_session=False
)
for team_id, stage_id in baseline_snapshot:
    db.add(TeamStageAccess(team_id=team_id, stage_id=stage_id))
db.commit()
print(f"  restored {len(baseline_snapshot)} pre-test team_stage_access row(s)")
from app.models.document import DocumentTeamVisibility, DocumentVersion
from app.models.workflow import WorkflowState
from app.models.audit import AuditLog

test_doc_ids = [d for d in (carol_testing_doc_id, carol_dev_doc_id, erin_doc_id, bob_doc_id) if d]
for doc_id in test_doc_ids:
    # current_version_id FKs into document_versions — null it first or the
    # DocumentVersion delete below violates fk_documents_current_version_id.
    db.query(Document).filter(Document.document_id == doc_id).update(
        {"current_version_id": None}, synchronize_session=False
    )
    db.query(WorkflowState).filter(WorkflowState.document_id == doc_id).delete(synchronize_session=False)
    db.query(DocumentVersion).filter(DocumentVersion.document_id == doc_id).delete(synchronize_session=False)
    db.query(DocumentTeamVisibility).filter(DocumentTeamVisibility.document_id == doc_id).delete(synchronize_session=False)
    db.query(Document).filter(Document.document_id == doc_id).delete(synchronize_session=False)

# Scoped by resource_id, not action type, so we don't wipe unrelated audit
# history from other UPLOAD_DOCUMENT/DELETE_STAGE events elsewhere in the app.
db.query(AuditLog).filter(AuditLog.action == "UPDATE_STAGE_TEAM_ACCESS").delete(synchronize_session=False)
db.query(AuditLog).filter(
    AuditLog.action == "UPLOAD_DOCUMENT", AuditLog.resource_id.in_(test_doc_ids)
).delete(synchronize_session=False)
db.query(AuditLog).filter(
    AuditLog.action == "DELETE_STAGE", AuditLog.resource_id == scratch.stage_id
).delete(synchronize_session=False)
db.commit()
print("  removed test team_stage_access rows, test-uploaded documents, and their audit rows")

h("RESULT")
print(f"  {'ALL CHECKS PASSED' if not FAIL else 'FAILURES: ' + '; '.join(FAIL)}")
db.close()

if FAIL:
    raise SystemExit(1)
