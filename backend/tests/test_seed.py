import json
from pathlib import Path

from sqlalchemy import func, select

from app.config import settings
from app.models import User
from app.security import verify_password
from app.seed import seed_users

USERS_FILE = Path(settings.SEED_DIR) / "users.json"


def test_running_seed_twice_creates_each_user_once(db_session):
    first = seed_users(db_session, USERS_FILE)
    second = seed_users(db_session, USERS_FILE)

    assert [action for _, action in first] == ["created"] * 5
    assert [action for _, action in second] == ["updated"] * 5
    assert db_session.scalar(select(func.count()).select_from(User)) == 5


def test_passwords_are_hashed_and_verifiable(db_session):
    seed_users(db_session, USERS_FILE)
    passwords = {entry["email"]: entry["password"] for entry in json.loads(USERS_FILE.read_text(encoding="utf-8"))}

    for user in db_session.scalars(select(User)):
        plain = passwords[user.email]
        assert user.password_hash != plain
        assert user.password_hash.startswith("$argon2id$")
        assert verify_password(plain, user.password_hash)
        assert not verify_password("wrong-password", user.password_hash)


def test_emails_are_stored_lowercase(db_session, tmp_path):
    users_file = tmp_path / "users.json"
    users_file.write_text(
        json.dumps([{"email": "  Mixed.Case@Example.COM ", "password": "pw", "role": "client", "name": "Mixed"}])
    )

    seed_users(db_session, users_file)

    assert db_session.scalar(select(User.email)) == "mixed.case@example.com"


def test_reseeding_keeps_a_deactivated_user_inactive(db_session):
    seed_users(db_session, USERS_FILE)
    user = db_session.scalar(select(User).where(User.email == "ops2@example.com"))
    user.is_active = False
    db_session.flush()

    seed_users(db_session, USERS_FILE)

    db_session.refresh(user)
    assert user.is_active is False
