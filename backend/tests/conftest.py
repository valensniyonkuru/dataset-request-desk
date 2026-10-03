"""Shared test setup.

Tests use their own database: TEST_DATABASE_URL, or else DATABASE_URL with
"_test" added to the database name. At the start of every test run that
database is dropped, recreated empty and migrated with Alembic. Each test then
runs inside a transaction that is rolled back, so tests never see each other's data.
"""

import os
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

from app.db import engine  # noqa: E402  (must come after the line above)

ALEMBIC_CONFIG = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))


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
