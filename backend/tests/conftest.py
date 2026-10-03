"""Shared test setup.

Tests use their own database: TEST_DATABASE_URL, or else DATABASE_URL with
"_test" added to the database name. At the start of every test run that
database is dropped, recreated empty and migrated with Alembic. Each test then
runs inside a transaction that is rolled back, so tests never see each other's data.
"""

import functools
import itertools
import os
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.orm import Session


def _test_database_url() -> str:
    if os.environ.get("TEST_DATABASE_URL"):
        return os.environ["TEST_DATABASE_URL"]
    url = make_url(os.environ["DATABASE_URL"])
    return url.set(database=f"{url.database}_test").render_as_string(hide_password=False)


TEST_DATABASE_URL = _test_database_url()

# Point the whole app at the test database. This must happen before anything
# imports "app", because app.config reads DATABASE_URL when it is imported.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

from fastapi.testclient import TestClient  # noqa: E402

from app.auth import create_access_token  # noqa: E402  (app imports must come after the line above)
from app.db import engine, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Assignment, Episode, Request, User  # noqa: E402
from app.security import hash_password  # noqa: E402

ALEMBIC_CONFIG = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))

# Argon2 is slow on purpose (~0.1 s per hash). Tests create hundreds of users,
# so each distinct password is hashed once and the hash reused.
_cached_hash = functools.cache(hash_password)


def _recreate_test_database() -> None:
    url = make_url(TEST_DATABASE_URL)
    # This drops the database, so refuse anything that is not clearly a test database.
    if not url.database or not url.database.endswith("_test"):
        raise RuntimeError(f"Refusing to drop {url.database!r}: the test database name must end in '_test'")

    # CREATE/DROP DATABASE cannot run inside a transaction, hence AUTOCOMMIT,
    # and must be run from another database, hence the default "postgres" one.
    admin_engine = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin_engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def migrated_database():
    """Once per test run: an empty test database, upgraded to the latest migration."""
    _recreate_test_database()
    command.upgrade(ALEMBIC_CONFIG, "head")
    yield
    engine.dispose()


@pytest.fixture
def alembic_config() -> Config:
    return ALEMBIC_CONFIG


@pytest.fixture
def db_session():
    """A session whose changes are all rolled back after the test.

    The test runs inside an outer transaction that is never committed.
    join_transaction_mode="create_savepoint" turns a session.commit() in the
    code under test into a savepoint, so the final rollback still undoes it.
    """
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(db_session):
    """An HTTP test client whose requests use the test's rolled-back session."""
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def create_user(db_session):
    """Factory: create_user("a@example.com", role="admin", password="...") -> User."""

    def _create_user(email: str, role: str = "client", password: str = "test-password", is_active: bool = True) -> User:
        user = User(
            email=email,
            name=email.split("@")[0],
            role=role,
            organisation="Test Org" if role == "client" else None,
            password_hash=_cached_hash(password),
            is_active=is_active,
        )
        db_session.add(user)
        db_session.flush()
        return user

    return _create_user


@pytest.fixture
def create_request(db_session):
    """Factory: create_request(client, status="delivered", episodes_requested=2) -> Request.

    Inserts the row directly, in any status, without history. Use the API when
    the test is about how a request gets into a status.
    """

    def _create_request(
        client: User, status: str = "submitted", episodes_requested: int = 2, task_name: str = "pick cup"
    ) -> Request:
        dataset_request = Request(
            client_id=client.id,
            task_name=task_name,
            episodes_requested=episodes_requested,
            deadline=date(2030, 1, 1),
            status=status,
        )
        db_session.add(dataset_request)
        db_session.flush()
        return dataset_request

    return _create_request


@pytest.fixture
def assign_episodes(db_session):
    """Factory: assign_episodes(request, count, assigned_by) creates `count` new episodes and assigns them.

    Inserts rows directly, because there is no assignment endpoint yet.
    """
    episode_numbers = itertools.count(1)

    def _assign_episodes(dataset_request: Request, count: int, assigned_by: User) -> None:
        episodes = [
            Episode(
                episode_id=f"EP-TEST-{next(episode_numbers)}",
                robot_id="arm-01",
                task_name=dataset_request.task_name,
                recorded_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                duration_seconds=30,
                operator_name="Aline",
                quality="good",
            )
            for _ in range(count)
        ]
        db_session.add_all(episodes)
        db_session.flush()  # episodes first: assignments reference them
        db_session.add_all(
            Assignment(request_id=dataset_request.id, episode_id=episode.episode_id, assigned_by=assigned_by.id)
            for episode in episodes
        )
        db_session.flush()

    return _assign_episodes


@pytest.fixture
def auth_headers():
    """auth_headers(user) -> an "Authorization: Bearer <token>" header for that user."""

    def _auth_headers(user: User) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_access_token(user)}"}

    return _auth_headers
