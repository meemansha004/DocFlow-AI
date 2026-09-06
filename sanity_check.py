from app.database import SessionLocal
from app.services.session_context import set_current_session
from app.services.document_persistence import create_document
from app.models.user import User
from app.models.team import Team
from app.models.project import Project

db = SessionLocal()
erin = db.query(User).filter(User.email == "erin@test.com").one()
project_a = db.query(Project).filter(Project.name == "Project A").one()
design = db.query(Team).filter(Team.name == "Design", Team.project_id == project_a.project_id).one()

set_current_session(user_id=erin.user_id, team_id=design.team_id, project_id=project_a.project_id, role="viewer")

result = create_document("Should Fail", "Testing", "# Blocked\n\nThis should not save.")
print(result)