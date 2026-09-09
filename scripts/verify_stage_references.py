"""
Verification for Phase A / Part 1 — stage_references.

FastAPI TestClient -> real Postgres. Covers: setting references (project_admin),
the resolver, contributor 403, cross-project rejection, self-reference
rejection, replace semantics, GET/workspace exposure, and the DB CHECK.
"""

from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models.user import User
from app.models.stage import Stage, StageReference
from app.models.project import Project
from app.services.auth import create_session_token
from app.services.rag.stage_scope import resolve_stage_scope

c = TestClient(app)
db = SessionLocal()

def uid(email):
    return db.query(User).filter(User.email == email).one().user_id

def tok(email):
    return {"Authorization": f"Bearer {create_session_token(str(uid(email)))}"}

import uuid as _uuid

PA = db.query(Project).filter(Project.name == "Project A").one()
OTHER_PROJECT = db.query(Project).filter(Project.name == "Test Project").one()
PID = str(PA.project_id)

def stage(project, name):
    return db.query(Stage).filter(
        Stage.project_id == project.project_id, Stage.name == name, Stage.deleted_at.is_(None)
    ).one()

TESTING = stage(PA, "Testing")
REQUIREMENTS = stage(PA, "Requirements")
DESIGN = stage(PA, "Design")
OTHER_STAGE = db.query(Stage).filter(
    Stage.project_id == OTHER_PROJECT.project_id, Stage.deleted_at.is_(None)
).first()

FAIL = []
def check(cond, msg):
    print(f"  {'PASS' if cond else 'FAIL'}: {msg}")
    if not cond:
        FAIL.append(msg)

def h(t):
    print("\n" + "=" * 78 + f"\n {t}\n" + "=" * 78)

def put_refs(email, stage_id, refs):
    return c.put(f"/projects/{PID}/stages/{stage_id}/references",
                 headers=tok(email), json={"references": [str(x) for x in refs]})

# clean slate for the stages we touch
db.query(StageReference).filter(
    StageReference.stage_id.in_([TESTING.stage_id, REQUIREMENTS.stage_id, DESIGN.stage_id])
).delete(synchronize_session=False)
db.commit()


# ---------------------------------------------------------------------------
h("1)  bob (project_admin) sets Testing -> references Requirements")
r = put_refs("bob@test.com", TESTING.stage_id, [REQUIREMENTS.stage_id])
print(f"  PUT -> {r.status_code} {r.json()}")
check(r.status_code == 200, "HTTP 200")
check(r.json().get("references") == [str(REQUIREMENTS.stage_id)], "response.references == [Requirements]")
db.expire_all()
row = db.query(StageReference).filter(
    StageReference.stage_id == TESTING.stage_id,
    StageReference.references_stage_id == REQUIREMENTS.stage_id,
).one_or_none()
check(row is not None, "stage_references row (Testing -> Requirements) exists in DB")

# ---------------------------------------------------------------------------
h("2)  resolve_stage_scope(Testing) == {Testing, Requirements}  (one level)")
scope = resolve_stage_scope(db, TESTING.stage_id)
print(f"  resolve_stage_scope(Testing) = {[str(x) for x in scope]}")
check(scope[0] == TESTING.stage_id, "stage itself is first")
check(set(scope) == {TESTING.stage_id, REQUIREMENTS.stage_id}, "== {Testing, Requirements}")
# one-way: Requirements does NOT get Testing back
scope_req = resolve_stage_scope(db, REQUIREMENTS.stage_id)
print(f"  resolve_stage_scope(Requirements) = {[str(x) for x in scope_req]}")
check(set(scope_req) == {REQUIREMENTS.stage_id}, "one-way: Requirements scope is just {Requirements}")
# NOT transitive: Requirements -> Design, Testing -> Requirements; Testing must NOT pull Design
put_refs("bob@test.com", REQUIREMENTS.stage_id, [DESIGN.stage_id])
db.expire_all()
scope2 = resolve_stage_scope(db, TESTING.stage_id)
check(DESIGN.stage_id not in scope2, "NOT transitive: Testing scope does not include Design (Requirements' ref)")
check(set(scope2) == {TESTING.stage_id, REQUIREMENTS.stage_id}, "Testing scope still {Testing, Requirements}")

# ---------------------------------------------------------------------------
h("3)  carol (contributor) cannot set references")
r = put_refs("carol@test.com", TESTING.stage_id, [DESIGN.stage_id])
check(r.status_code == 403, f"carol PUT references -> 403  ({r.json().get('detail','')[:50]})")
r = put_refs("erin@test.com", TESTING.stage_id, [DESIGN.stage_id])
check(r.status_code == 403, "erin (team_lead) PUT references -> 403")
# contributor CAN still read the stage list (with references)
r = c.get(f"/projects/{PID}/stages", headers=tok("carol@test.com"))
t_out = next((s for s in r.json() if s["stage_id"] == str(TESTING.stage_id)), None)
check(r.status_code == 200 and t_out is not None
      and t_out["references"] == [str(REQUIREMENTS.stage_id)],
      "GET /stages (as contributor) still shows Testing.references = [Requirements]")

# ---------------------------------------------------------------------------
h("4)  cross-project reference is rejected")
r = put_refs("bob@test.com", TESTING.stage_id, [OTHER_STAGE.stage_id])
check(r.status_code == 422, f"referencing a '{OTHER_PROJECT.name}' stage -> 422  ({r.json().get('detail','')[:60]})")
db.expire_all()
check(db.query(StageReference).filter(
        StageReference.stage_id == TESTING.stage_id,
        StageReference.references_stage_id == OTHER_STAGE.stage_id,
      ).count() == 0, "no cross-project row was written")
r = put_refs("bob@test.com", TESTING.stage_id, [_uuid.uuid4()])
check(r.status_code == 422, "referencing a non-existent stage_id -> 422")

# ---------------------------------------------------------------------------
h("5)  a stage cannot reference itself")
r = put_refs("bob@test.com", TESTING.stage_id, [TESTING.stage_id])
check(r.status_code == 422, f"self-reference via API -> 422  ({r.json().get('detail','')[:50]})")
# DB CHECK constraint is the backstop
import sqlalchemy
try:
    db.add(StageReference(stage_id=TESTING.stage_id, references_stage_id=TESTING.stage_id))
    db.commit()
    check(False, "DB rejected the self-reference row")
except sqlalchemy.exc.IntegrityError as e:
    db.rollback()
    check("ck_stage_reference_not_self" in str(e), "DB CHECK 'ck_stage_reference_not_self' blocks a self-reference row")

# ---------------------------------------------------------------------------
h("6)  PUT is replace-all + dedupe; workspace exposes references")
r = put_refs("bob@test.com", TESTING.stage_id, [DESIGN.stage_id, DESIGN.stage_id, REQUIREMENTS.stage_id])
check(r.status_code == 200 and set(r.json()["references"]) == {str(DESIGN.stage_id), str(REQUIREMENTS.stage_id)},
      "PUT [Design, Design, Requirements] -> refs {Design, Requirements} (deduped)")
r = put_refs("bob@test.com", TESTING.stage_id, [])
check(r.status_code == 200 and r.json()["references"] == [], "PUT [] clears all references (replace semantics)")
db.expire_all()
check(db.query(StageReference).filter(StageReference.stage_id == TESTING.stage_id).count() == 0,
      "no stage_references rows left for Testing")
# put one back and check /workspace
put_refs("bob@test.com", TESTING.stage_id, [REQUIREMENTS.stage_id])
ws = c.get("/workspace", headers=tok("bob@test.com")).json()
ws_pa = next(p for p in ws["projects"] if p["project_id"] == PID)
ws_testing = next(s for s in ws_pa["stages"] if s["stage_id"] == str(TESTING.stage_id))
check(ws_testing["references"] == [str(REQUIREMENTS.stage_id)], "GET /workspace Testing.references = [Requirements]")

# ---------------------------------------------------------------------------
h("CLEANUP")
db.query(StageReference).filter(
    StageReference.stage_id.in_([TESTING.stage_id, REQUIREMENTS.stage_id, DESIGN.stage_id])
).delete(synchronize_session=False)
from app.models.audit import AuditLog
db.query(AuditLog).filter(AuditLog.action == "UPDATE_STAGE_REFERENCES").delete(synchronize_session=False)
db.commit()
print("  removed all test stage_references rows + their audit rows")

h("RESULT")
print(f"  {'ALL CHECKS PASSED' if not FAIL else 'FAILURES: ' + '; '.join(FAIL)}")
db.close()
