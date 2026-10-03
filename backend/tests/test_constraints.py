"""The database itself rejects invalid rows (CHECK and UNIQUE constraints).

Each test checks the error names the expected constraint, so a test cannot
pass by failing for some other reason (e.g. a missing required column).
"""

from datetime import date, datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Assignment, Episode, Request, User


def make_user(**fields) -> User:
    values = {"email": "user@example.com", "name": "Test User", "password_hash": "not-a-real-hash", "role": "client"}
    return User(**(values | fields))


def make_episode(**fields) -> Episode:
    values = {
        "episode_id": "EP-1",
        "robot_id": "arm-01",
        "task_name": "pick cup",
        "recorded_at": datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
        "duration_seconds": 30,
        "operator_name": "Aline",
        "quality": "good",
    }
    return Episode(**(values | fields))


def make_request(client_id: int, **fields) -> Request:
    values = {"client_id": client_id, "task_name": "pick cup", "episodes_requested": 10, "deadline": date(2026, 12, 1)}
    return Request(**(values | fields))


def add(session, row):
    session.add(row)
    session.flush()
    return row


def assert_rejected(session, row, constraint_name: str) -> None:
    session.add(row)
    with pytest.raises(IntegrityError, match=constraint_name):
        session.flush()


def test_episode_cannot_be_assigned_to_two_requests(db_session):
    operator = add(db_session, make_user(email="ops@example.com", role="operator"))
    client = add(db_session, make_user())
    episode = add(db_session, make_episode())
    first_request = add(db_session, make_request(client.id))
    second_request = add(db_session, make_request(client.id))
    add(db_session, Assignment(request_id=first_request.id, episode_id=episode.episode_id, assigned_by=operator.id))

    assert_rejected(
        db_session,
        Assignment(request_id=second_request.id, episode_id=episode.episode_id, assigned_by=operator.id),
        "uq_assignments_episode_id",
    )


def test_invalid_role_is_rejected(db_session):
    assert_rejected(db_session, make_user(role="superuser"), "ck_users_role")


def test_uppercase_email_is_rejected(db_session):
    assert_rejected(db_session, make_user(email="User@Example.com"), "ck_users_email_lowercase")


def test_invalid_quality_is_rejected(db_session):
    assert_rejected(db_session, make_episode(quality="excellent"), "ck_episodes_quality")


def test_zero_duration_is_rejected(db_session):
    assert_rejected(db_session, make_episode(duration_seconds=0), "ck_episodes_duration_positive")


def test_invalid_request_status_is_rejected(db_session):
    client = add(db_session, make_user())
    assert_rejected(db_session, make_request(client.id, status="done"), "ck_requests_status")


def test_zero_episodes_requested_is_rejected(db_session):
    client = add(db_session, make_user())
    assert_rejected(db_session, make_request(client.id, episodes_requested=0), "ck_requests_episodes_requested_positive")
