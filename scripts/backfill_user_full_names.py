"""
Backfill users.full_name for the seeded test accounts.

The full_name column was added in migration c7f1a9e34b21 (nullable). The seeded
users predate it, so their name shows as the email in the admin directory until
this runs. New users created through "Add organization user" already store a
real name; this only fixes the historical rows.

Run:  python scripts/backfill_user_full_names.py
"""

from app.database import SessionLocal
from app.models.user import User

SEEDED_NAMES = {
    "alice@test.com": "Alice Nakamura",
    "bob@test.com": "Bob Iglesias",
    "carol@test.com": "Carol Whitfield",
    "dave@test.com": "Dave Okonkwo",
    "erin@test.com": "Erin Zhao",
    "frank@test.com": "Frank Delgado",
    "test@example.com": "Test Administrator",
}


def main() -> None:
    db = SessionLocal()
    try:
        updated, skipped, missing = [], [], []
        for email, name in SEEDED_NAMES.items():
            user = db.query(User).filter(User.email == email).one_or_none()
            if user is None:
                missing.append(email)
                continue
            if user.full_name:
                skipped.append(f"{email} (already {user.full_name!r})")
                continue
            user.full_name = name
            updated.append(f"{email} -> {name}")
        db.commit()

        print(f"Set full_name on {len(updated)} user(s):")
        for line in updated:
            print(f"  - {line}")
        if skipped:
            print("Left unchanged:")
            for line in skipped:
                print(f"  - {line}")
        if missing:
            print("Not found (skipped):")
            for email in missing:
                print(f"  - {email}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
