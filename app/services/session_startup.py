"""
CLI session startup — prompts for which test user is "using" this
session. Not called from anywhere except cli.py's main() at startup.
"""

from app.models.user import User
from app.models.team import Team, UserTeamMembership, ProjectAdmin
from app.models.project import Project


def select_current_user(db):
    users = db.query(User).order_by(User.email).all()
    print("\nWho's using this session?")
    for i, u in enumerate(users):
        print(f"  {i+1}. {u.email}")
    choice = int(input("Select: ")) - 1
    user = users[choice]

    if user.is_org_admin:
        projects = db.query(Project).all()
        print("\nActing within which project?")
        for i, p in enumerate(projects):
            print(f"  {i+1}. {p.name}")
        project = projects[int(input("Select: ")) - 1]
        teams = db.query(Team).filter(Team.project_id == project.project_id).all()
        print("\nActing as which team?")
        for i, t in enumerate(teams):
            print(f"  {i+1}. {t.name}")
        team = teams[int(input("Select: ")) - 1]
        return user, team, project, "org_admin"

    admin_project = db.query(ProjectAdmin).filter(ProjectAdmin.user_id == user.user_id).first()
    if admin_project:
        project = db.get(Project, admin_project.project_id)
        teams = db.query(Team).filter(Team.project_id == project.project_id).all()
        print(f"\nActing as which team in {project.name}?")
        for i, t in enumerate(teams):
            print(f"  {i+1}. {t.name}")
        team = teams[int(input("Select: ")) - 1]
        return user, team, project, "project_admin"

    memberships = db.query(UserTeamMembership).filter(UserTeamMembership.user_id == user.user_id).all()
    if len(memberships) == 1:
        m = memberships[0]
        team = db.get(Team, m.team_id)
        project = db.get(Project, m.project_id)
        return user, team, project, m.role
    else:
        print(f"\n{user.email} has multiple team roles — which are you acting as?")
        for i, m in enumerate(memberships):
            team = db.get(Team, m.team_id)
            print(f"  {i+1}. {team.name} ({m.role.value})")
        m = memberships[int(input("Select: ")) - 1]
        team = db.get(Team, m.team_id)
        project = db.get(Project, m.project_id)
        return user, team, project, m.role