from alembic import command
from sqlalchemy import inspect, text

from app.db import engine

APP_TABLES = {"users", "episodes", "requests", "assignments", "request_status_history", "import_runs"}


def table_names() -> set[str]:
    return set(inspect(engine).get_table_names())


def test_downgrade_to_base_then_upgrade_to_head(alembic_config):
    # conftest.py has already upgraded an empty database to head.
    assert APP_TABLES <= table_names()

    command.downgrade(alembic_config, "base")
    assert table_names() == {"alembic_version"}

    command.upgrade(alembic_config, "head")
    assert APP_TABLES <= table_names()


def test_models_match_migrations(alembic_config):
    # Fails if a model was changed without a matching migration.
    command.check(alembic_config)


def test_the_analytics_day_statistics_exist():
    # Created by a migration with raw SQL: `alembic check` cannot see statistics objects.
    with engine.connect() as connection:
        names = set(connection.scalars(text("SELECT stxname FROM pg_statistic_ext")))

    assert "st_episodes_utc_day_robot" in names
