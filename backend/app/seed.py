"""Create or update the seed users from users.json.

Run with: python -m app.seed

Safe to run any number of times: users are matched by email, so a second run
updates the existing rows instead of creating duplicates.
"""

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import User
from app.security import hash_password


def seed_users(session: Session, users_file: Path) -> list[tuple[str, str]]:
    """Insert or update each user by email. Returns (email, "created" or "updated") pairs."""
    results = []
    for entry in json.loads(users_file.read_text(encoding="utf-8")):
        email = entry["email"].strip().lower()
        user = session.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email)
            session.add(user)
            action = "created"
        else:
            # is_active is left as it is, so re-seeding never reactivates a user.
            action = "updated"

        user.name = entry["name"]
        user.role = entry["role"]
        user.organisation = entry.get("organisation")
        user.password_hash = hash_password(entry["password"])
        results.append((email, action))

    session.flush()
    return results


def main() -> None:
    users_file = Path(settings.SEED_DIR) / "users.json"
    with SessionLocal() as session:
        results = seed_users(session, users_file)
        session.commit()

    # Print only after the commit succeeded. Never print passwords or hashes.
    for email, action in results:
        print(f"{action}: {email}")


if __name__ == "__main__":
    main()
