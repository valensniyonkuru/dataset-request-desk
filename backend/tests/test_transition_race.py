"""Two transitions racing on the same request (e.g. a double click on accept and reject).

A row lock only matters between separate database connections that commit, so
these tests cannot use the rollback-wrapped db_session. They commit their own
rows through real sessions and delete them again afterwards.
"""

import threading
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.auth import create_access_token
from app.db import SessionLocal
from app.main import app
from app.models import Request, RequestStatusHistory, User


@pytest.fixture
def delivered_request():
    """A committed client and a delivered request. Yields (owner_id, request_id), then deletes both."""
    with SessionLocal() as session:
        owner = User(
            email="race-client@example.com", name="Race Client", role="client", organisation="Race Org",
            password_hash="not-used",
        )
        session.add(owner)
        session.flush()
        dataset_request = Request(
            client_id=owner.id, task_name="pick cup", episodes_requested=1, deadline=date(2030, 1, 1), status="delivered"
        )
        session.add(dataset_request)
        session.commit()
        owner_id, request_id = owner.id, dataset_request.id

    yield owner_id, request_id

    with SessionLocal() as session:
        session.execute(delete(RequestStatusHistory).where(RequestStatusHistory.request_id == request_id))
        session.execute(delete(Request).where(Request.id == request_id))
        session.execute(delete(User).where(User.id == owner_id))
        session.commit()


def post_transition(owner_id: int, request_id: int, to_status: str):
    headers = {"Authorization": f"Bearer {create_access_token(User(id=owner_id))}"}
    # A TestClient without the db override, so the API uses its own real session.
    return TestClient(app).post(f"/requests/{request_id}/transition", json={"to_status": to_status}, headers=headers)


def test_a_transition_waits_for_the_row_lock_and_then_sees_the_new_status(delivered_request):
    owner_id, request_id = delivered_request
    result = {}

    with SessionLocal() as first_click:
        # The first click is half-way through: it holds the row lock and has accepted, but not committed.
        locked = first_click.scalar(select(Request).where(Request.id == request_id).with_for_update())
        locked.status = "accepted"
        first_click.flush()

        second_click = threading.Thread(
            target=lambda: result.update(response=post_transition(owner_id, request_id, "rejected")), daemon=True
        )
        second_click.start()
        second_click.join(timeout=1)
        assert second_click.is_alive(), "the second transition should be waiting for the row lock"

        first_click.commit()  # releases the lock

    second_click.join(timeout=10)
    assert result["response"].status_code == 409
    assert result["response"].json()["detail"] == "Cannot move from accepted to rejected"


def test_concurrent_accept_and_reject_only_one_succeeds(delivered_request):
    owner_id, request_id = delivered_request
    start_together = threading.Barrier(2)
    status_codes = {}

    def click(to_status):
        start_together.wait()
        status_codes[to_status] = post_transition(owner_id, request_id, to_status).status_code

    clicks = [threading.Thread(target=click, args=(to_status,), daemon=True) for to_status in ("accepted", "rejected")]
    for thread in clicks:
        thread.start()
    for thread in clicks:
        thread.join(timeout=10)

    assert sorted(status_codes.values()) == [200, 409]
    with SessionLocal() as session:
        history_count = session.scalar(
            select(func.count()).select_from(RequestStatusHistory).where(RequestStatusHistory.request_id == request_id)
        )
    assert history_count == 1
