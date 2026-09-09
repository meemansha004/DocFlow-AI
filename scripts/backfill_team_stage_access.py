"""
Backfill team_stage_access for Project A's existing teams.

Phase A Part 3 made stage access default-deny: a team needs an explicit
team_stage_access grant to upload to or see a stage (org_admin/project_admin
bypass). Before that migration, every team could act on every stage in its
project — so without this backfill, every existing team in Project A would
suddenly lose access to everything the moment the enforcement ships.

Grants Engineering, QA, and Design access to all 4 of Project A's existing
stages (Requirements, Design, Development, Testing) — matching the
unrestricted access they had before this feature existed, and what the
existing verify_audit_activity.py / verify_stage_management.py scripts
already assume when they upload as these teams. Idempotent — skips a
(team, stage) pair that already has a grant.

Run:  PYTHONPATH=. python scripts/backfill_team_stage_access.py
"""

from app.database import SessionLocal
from app.models.project import Project
from app.models.stage import Stage, TeamStageAccess
from app.models.team import Team

PROJECT_NAME = "Project A"
TEAM_NAMES = ["Engineering", "QA", "Design"]


def main() -> None:
    db = SessionLocal()
    try:
        project = db.query(Project).filter(Project.name == PROJECT_NAME).one_or_none()
        if project is None:
            print(f"Project {PROJECT_NAME!r} not found — nothing to do.")
            return

        stages = db.query(Stage).filter(
            Stage.project_id == project.project_id, Stage.deleted_at.is_(None)
        ).all()
        teams = db.query(Team).filter(
            Team.project_id == project.project_id, Team.name.in_(TEAM_NAMES)
        ).all()

        existing = {
            (r.team_id, r.stage_id)
            for r in db.query(TeamStageAccess).filter(
                TeamStageAccess.stage_id.in_([s.stage_id for s in stages])
            ).all()
        }

        granted, skipped = [], []
        for team in teams:
            for stage in stages:
                if (team.team_id, stage.stage_id) in existing:
                    skipped.append(f"{team.name} x {stage.name} (already granted)")
                    continue
                db.add(TeamStageAccess(team_id=team.team_id, stage_id=stage.stage_id))
                granted.append(f"{team.name} x {stage.name}")
        db.commit()

        print(f"Granted {len(granted)} team_stage_access row(s) in {PROJECT_NAME!r}:")
        for line in granted:
            print(f"  - {line}")
        if skipped:
            print("Left unchanged (already existed):")
            for line in skipped:
                print(f"  - {line}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
