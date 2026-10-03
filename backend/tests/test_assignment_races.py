"""Assignments under concurrency, with real database connections.

Locks and the UNIQUE constraint only matter between separate connections that
commit, so these tests cannot use the rollback-wrapped db_session. They commit
their own rows and delete them again afterwards, even when a test fails.

Two kinds of test:
- deterministic: another transaction holds a lock (or an uncommitted row) at
  an exact moment, so the test knows which path the API takes;
- races: two API calls start at the same moment from two threads, repeated a
  few times; whatever the timing, the end state must be valid.
"""

import threading
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.auth import create_access_token
from app.db import SessionLocal
from app.main import app
from app.models import Assignment, Episode, Request, RequestStatusHistory, User

NOT_OPEN = "Episodes can only be assigned while the request is in_progress"


@pytest.fixture
def committed():
    """Committed rows: an operator, a client, two in_progress requests that want 2 episodes each,
    and three good episodes. Everything is deleted again after the test."""
    with SessionLocal() as session:
        operator = User(email="race-ops@example.com", name="Race Ops", role="operator", password_hash="not-used")
        owner = User(
            email="race-client@example.com", name="Race Client", role="client", organisation="Race Org",
            password_hash="not-used",
        )
        session.add_all([operator, owner])
        session.flush()
        first, second = (
            Request(
                client_id=owner.id, task_name="pick cup", episodes_requested=2, deadline=date(2030, 1, 1),
                status="in_progress",
            )
            for _ in range(2)
        )
        episodes = [
            Episode(
                episode_id=f"EP-RACE-{n}", robot_id="arm-01", task_name="pick cup",
                recorded_at=datetime(2026, 9, 1, tzinfo=timezone.utc), duration_seconds=30, operator_name="Aline",
                quality="good",
            )
            for n in range(3)
        ]
        session.add_all([first, second, *episodes])
        session.commit()
        rows = SimpleNamespace(
            operator_id=operator.id,
            owner_id=owner.id,
            first_id=first.id,
            second_id=second.id,
            episode_ids=[episode.episode_id for episode in episodes],
        )

    yield rows

    with SessionLocal() as session:
        request_ids = [rows.first_id, rows.second_id]
        session.execute(delete(Assignment).where(Assignment.request_id.in_(request_ids)))
        session.execute(delete(RequestStatusHistory).where(RequestStatusHistory.request_id.in_(request_ids)))
        session.execute(delete(Request).where(Request.id.in_(request_ids)))
        session.execute(delete(Episode).where(Episode.episode_id.in_(rows.episode_ids)))
        session.execute(delete(User).where(User.id.in_([rows.operator_id, rows.owner_id])))
        session.commit()


def as_operator(rows, method: str, path: str, json=None):
    """Call the API as the operator. No db override, so the API uses its own real session."""
    headers = {"Authorization": f"Bearer {create_access_token(User(id=rows.operator_id))}"}
    return TestClient(app).request(method, path, json=json, headers=headers)


def assign_call(rows, request_id: int, episode_id: str):
    return lambda: as_operator(rows, "POST", f"/requests/{request_id}/assignments", json={"episode_ids": [episode_id]})


def unassign_call(rows, request_id: int, episode_id: str):
    return lambda: as_operator(rows, "DELETE", f"/requests/{request_id}/assignments/{episode_id}")


def commit_assignments(rows, request_id: int, episode_ids: list[str]) -> None:
    with SessionLocal() as session:
        session.add_all(
            Assignment(request_id=request_id, episode_id=episode_id, assigned_by=rows.operator_id)
            for episode_id in episode_ids
        )
        session.commit()


def count_assignments(*conditions) -> int:
    with SessionLocal() as session:
        return session.scalar(select(func.count()).select_from(Assignment).where(*conditions))


def run_in_background(call) -> tuple[threading.Thread, dict]:
    result = {}
    thread = threading.Thread(target=lambda: result.update(response=call()), daemon=True)
    thread.start()
    return thread, result


def run_together(*calls) -> list:
    """Run the calls in threads released at the same moment. Returns their results, in order."""
    start_together = threading.Barrier(len(calls))
    results = [None] * len(calls)

    def run(index, call):
        start_together.wait()
        results[index] = call()

    threads = [threading.Thread(target=run, args=(i, call), daemon=True) for i, call in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
    return results


# --- deterministic ---


@pytest.mark.parametrize("operation", ["assign", "unassign"])
def test_assign_and_unassign_wait_for_a_delivery_in_progress(committed, operation):
    rows = committed
    commit_assignments(rows, rows.first_id, rows.episode_ids[:2])  # 2 of 2: ready to deliver
    if operation == "assign":
        call = assign_call(rows, rows.first_id, rows.episode_ids[2])
    else:
        call = unassign_call(rows, rows.first_id, rows.episode_ids[0])

    with SessionLocal() as delivery:
        # A delivery is half-way through: it holds the request's row lock and has set the status.
        locked = delivery.scalar(select(Request).where(Request.id == rows.first_id).with_for_update())
        locked.status = "delivered"
        delivery.flush()

        thread, result = run_in_background(call)
        thread.join(timeout=1)
        assert thread.is_alive(), f"{operation} should wait for the request's row lock"

        delivery.commit()  # releases the lock

    thread.join(timeout=10)
    assert result["response"].status_code == 409
    assert result["response"].json()["detail"] == NOT_OPEN
    assert count_assignments(Assignment.request_id == rows.first_id) == 2


def test_losing_the_unique_constraint_race_gives_409_not_500(committed):
    rows = committed
    episode_id = rows.episode_ids[0]

    with SessionLocal() as winner:
        # Another transaction has inserted the assignment but not committed yet,
        # so the API's "already assigned?" check cannot see it.
        winner.add(Assignment(request_id=rows.first_id, episode_id=episode_id, assigned_by=rows.operator_id))
        winner.flush()

        thread, result = run_in_background(assign_call(rows, rows.second_id, episode_id))
        thread.join(timeout=1)
        assert thread.is_alive(), "the API's INSERT should wait on the unique index"

        winner.commit()  # now the API's INSERT fails with a unique violation

    thread.join(timeout=10)
    assert result["response"].status_code == 409
    assert result["response"].json()["detail"] == f"Already assigned: {episode_id} (request {rows.first_id})"
    assert count_assignments(Assignment.episode_id == episode_id) == 1


# --- races ---


@pytest.mark.parametrize("attempt", range(5))
def test_two_requests_assigning_the_same_episode_at_once(committed, attempt):
    rows = committed
    episode_id = rows.episode_ids[0]

    responses = run_together(
        assign_call(rows, rows.first_id, episode_id),
        assign_call(rows, rows.second_id, episode_id),
    )

    assert sorted(response.status_code for response in responses) == [201, 409]
    assert count_assignments(Assignment.episode_id == episode_id) == 1


@pytest.mark.parametrize("attempt", range(5))
def test_unassign_racing_with_delivery_never_leaves_a_short_delivery(committed, attempt):
    rows = committed
    commit_assignments(rows, rows.first_id, rows.episode_ids[:2])  # 2 of 2: ready to deliver

    deliver, remove = run_together(
        lambda: as_operator(rows, "POST", f"/requests/{rows.first_id}/transition", json={"to_status": "delivered"}),
        unassign_call(rows, rows.first_id, rows.episode_ids[0]),
    )

    with SessionLocal() as session:
        status = session.scalar(select(Request.status).where(Request.id == rows.first_id))
    assigned = count_assignments(Assignment.request_id == rows.first_id)
    if deliver.status_code == 200:
        assert (remove.status_code, status, assigned) == (409, "delivered", 2)
    else:
        assert (deliver.status_code, remove.status_code, status, assigned) == (409, 204, "in_progress", 1)
