from app.database import SessionLocal
from app.models.user import User
from app.models.team import Team
from app.models.project import Project
from app.services.access_control import has_permission

db = SessionLocal()

alice = db.query(User).filter(User.email == "alice@test.com").one()
bob = db.query(User).filter(User.email == "bob@test.com").one()
frank = db.query(User).filter(User.email == "frank@test.com").one()
dave = db.query(User).filter(User.email == "dave@test.com").one()

engineering = db.query(Team).filter(Team.name == "Engineering", Team.project_id == db.query(Project).filter(Project.name == "Project A").one().project_id).one()
qa_team = db.query(Team).filter(Team.name == "QA").one()
project_a = db.query(Project).filter(Project.name == "Project A").one()

# alice (org_admin) should bypass everything, even on a team she has no membership in
print("alice upload on Engineering:", has_permission(db, alice.user_id, "upload", engineering.team_id, project_a.project_id))  # expect True

# bob (project_admin of Project A) should bypass, even on QA where he has no membership
print("bob upload on QA:", has_permission(db, bob.user_id, "upload", qa_team.team_id, project_a.project_id))  # expect True

# frank (contributor, but on Project B, no membership in Project A's QA team) should be denied
print("frank upload on QA (Project A):", has_permission(db, frank.user_id, "upload", qa_team.team_id, project_a.project_id))  # expect False

# dave (team_lead on QA) attempting a team_lead-only action on his own team
print("dave approve_access_request on QA:", has_permission(db, dave.user_id, "approve_access_request", qa_team.team_id, project_a.project_id))  # expect True