"""
Quick ABAC smoke check: erin acting as her Design *viewer* role must NOT be
able to upload. Exercises the refactored create_document() (explicit params).
"""

from app.database import SessionLocal
from app.services.session_context import set_current_session, get_current_session
from app.services.document_persistence import create_document, PermissionDeniedError
from app.models.user import User
from app.models.team import Team
from app.models.project import Project
from app.models.stage import Stage

db = SessionLocal()
erin = db.query(User).filter(User.email == "erin@test.com").one()
project_a = db.query(Project).filter(Project.name == "Project A").one()
design = db.query(Team).filter(Team.name == "Design", Team.project_id == project_a.project_id).one()
testing = db.query(Stage).filter(Stage.name == "Testing", Stage.project_id == project_a.project_id).one()

set_current_session(
    user_id=erin.user_id, team_id=design.team_id, project_id=project_a.project_id, role="viewer"
)
session = get_current_session()

try:
    result = create_document(
        db,
        user_id=session["user_id"],
        team_id=session["team_id"],
        project_id=session["project_id"],
        role=session["role"],
        document_type="Should Fail",
        stage_id=testing.stage_id,
        content="# Blocked\n\nThis should not save.",
    )
    print("UNEXPECTED SUCCESS:", result)
except PermissionDeniedError as exc:
    print(f"Blocked as expected: {exc}")
finally:
    db.close()
