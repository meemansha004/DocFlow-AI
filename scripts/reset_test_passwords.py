"""
Reset every seeded test user's password to a single known value, hashed with
app.services.auth.hash_password() (PBKDF2-HMAC-SHA256).

Why this exists: the seeded users were created with bcrypt ($2b$...) hashes
added outside the migration chain during the repo merge. Phase 2's
verify_password() only understands the pbkdf2_sha256$... format, so none of
the seeded users could log in via POST /auth/login. This normalises all of
them onto the project's actual hashing scheme.

Run:  python scripts/reset_test_passwords.py
"""

from app.database import SessionLocal
from app.models.user import User
from app.services.auth import hash_password

TEST_PASSWORD = "docflow-test-pw"

SEEDED_EMAILS = [
    "alice@test.com",
    "bob@test.com",
    "carol@test.com",
    "dave@test.com",
    "erin@test.com",
    "frank@test.com",
    "test@example.com",
]


def main() -> None:
    db = SessionLocal()
    try:
        updated, missing = [], []
        for email in SEEDED_EMAILS:
            user = db.query(User).filter(User.email == email).one_or_none()
            if user is None:
                missing.append(email)
                continue
            user.password_hash = hash_password(TEST_PASSWORD)
            updated.append(email)
        db.commit()

        print(f"Reset {len(updated)} user(s) to password {TEST_PASSWORD!r}:")
        for email in updated:
            print(f"  - {email}")
        if missing:
            print("Not found (skipped):")
            for email in missing:
                print(f"  - {email}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
