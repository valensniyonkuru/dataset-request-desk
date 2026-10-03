from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.models import Request

TODAY = datetime.now(timezone.utc).date()

NEW_REQUEST = {
    "task_name": "pick cup",
    "episodes_requested": 200,
    "deadline": str(TODAY + timedelta(days=30)),
    "notes": "Robot arm, white cups",
}


def count_requests(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(Request))


def listed_ids(client, user_headers, **params) -> list[int]:
    response = client.get("/requests", params=params, headers=user_headers)
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()]


# --- creating a request ---


def test_client_can_create_a_request(client, create_user, auth_headers):
    owner = create_user("client-a@example.com")

    response = client.post("/requests", json=NEW_REQUEST | {"task_name": "  pick cup  "}, headers=auth_headers(owner))

    assert response.status_code == 201
    body = response.json()
    assert body["client_id"] == owner.id
    assert body["client_name"] == owner.name
    assert body["task_name"] == "pick cup"  # trimmed
    assert body["episodes_requested"] == 200
    assert body["status"] == "submitted"
    assert body["assigned_count"] == 0
    assert body["available_transitions"] == []  # the next step belongs to an operator


def test_creating_a_request_writes_the_first_history_row(client, create_user, auth_headers):
    owner = create_user("client-a@example.com")

    response = client.post("/requests", json=NEW_REQUEST, headers=auth_headers(owner))

    [first_entry] = response.json()["history"]
    assert first_entry["from_status"] is None
    assert first_entry["to_status"] == "submitted"
    assert first_entry["changed_by_name"] == owner.name


@pytest.mark.parametrize("role", ["operator", "admin"])
def test_operators_and_admins_cannot_create_requests(client, create_user, auth_headers, role):
    user = create_user(f"{role}@example.com", role=role)

    response = client.post("/requests", json=NEW_REQUEST, headers=auth_headers(user))

    assert response.status_code == 403


def test_creating_a_request_requires_login(client):
    response = client.post("/requests", json=NEW_REQUEST)

    assert response.status_code == 401


def test_client_id_in_the_body_is_rejected(client, create_user, auth_headers, db_session):
    owner = create_user("client-a@example.com")
    other_client = create_user("client-b@example.com")

    response = client.post("/requests", json=NEW_REQUEST | {"client_id": other_client.id}, headers=auth_headers(owner))

    assert response.status_code == 422
    assert count_requests(db_session) == 0


@pytest.mark.parametrize(
    "change",
    [
        {"episodes_requested": 0},
        {"episodes_requested": -5},
        {"episodes_requested": 100_001},
        {"episodes_requested": "10"},  # a string, not a number
        {"deadline": str(TODAY - timedelta(days=1))},
        {"task_name": "   "},
        {"task_name": "x" * 101},
        {"notes": "x" * 2001},
        {"status": "accepted"},  # the status cannot be chosen
    ],
)
def test_invalid_new_request_gives_422(client, create_user, auth_headers, db_session, change):
    owner = create_user("client-a@example.com")

    response = client.post("/requests", json=NEW_REQUEST | change, headers=auth_headers(owner))

    assert response.status_code == 422
    assert count_requests(db_session) == 0


def test_deadline_today_is_allowed(client, create_user, auth_headers):
    owner = create_user("client-a@example.com")

    response = client.post("/requests", json=NEW_REQUEST | {"deadline": str(TODAY)}, headers=auth_headers(owner))

    assert response.status_code == 201


# --- who sees what ---


def test_clients_only_see_their_own_requests(client, create_user, create_request, auth_headers):
    client_a = create_user("client-a@example.com")
    client_b = create_user("client-b@example.com")
    own_request = create_request(client_a)
    create_request(client_b)

    assert listed_ids(client, auth_headers(client_a)) == [own_request.id]


def test_another_clients_request_looks_exactly_like_a_missing_one(client, create_user, create_request, auth_headers):
    client_a = create_user("client-a@example.com")
    client_b = create_user("client-b@example.com")
    foreign_request = create_request(client_b)

    foreign = client.get(f"/requests/{foreign_request.id}", headers=auth_headers(client_a))
    missing = client.get("/requests/999999", headers=auth_headers(client_a))

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()


def test_client_cannot_transition_another_clients_request(
    client, create_user, create_request, auth_headers, db_session
):
    client_a = create_user("client-a@example.com")
    client_b = create_user("client-b@example.com")
    foreign_request = create_request(client_b, status="delivered")

    response = client.post(
        f"/requests/{foreign_request.id}/transition", json={"to_status": "accepted"}, headers=auth_headers(client_a)
    )

    assert response.status_code == 404
    db_session.refresh(foreign_request)
    assert foreign_request.status == "delivered"


@pytest.mark.parametrize("role", ["operator", "admin"])
def test_operators_and_admins_see_every_request(client, create_user, create_request, auth_headers, role):
    user = create_user(f"{role}@example.com", role=role)
    request_a = create_request(create_user("client-a@example.com"))
    request_b = create_request(create_user("client-b@example.com"))

    assert set(listed_ids(client, auth_headers(user))) == {request_a.id, request_b.id}
    for dataset_request in (request_a, request_b):
        assert client.get(f"/requests/{dataset_request.id}", headers=auth_headers(user)).status_code == 200


def test_reading_requests_requires_login(client, create_user, create_request):
    dataset_request = create_request(create_user("client-a@example.com"))

    assert client.get("/requests").status_code == 401
    assert client.get(f"/requests/{dataset_request.id}").status_code == 401


# --- filters, counts and pagination ---


def test_list_filters(client, create_user, create_request, auth_headers):
    operator = create_user("ops@example.com", role="operator")
    client_a = create_user("client-a@example.com")
    client_b = create_user("client-b@example.com")
    a_cups = create_request(client_a, task_name="pick cup")
    a_towels = create_request(client_a, task_name="fold towel", status="in_progress")
    b_cups = create_request(client_b, task_name="pick cup", status="in_progress")
    headers = auth_headers(operator)

    assert set(listed_ids(client, headers, status="in_progress")) == {a_towels.id, b_cups.id}
    assert set(listed_ids(client, headers, task_name="pick cup")) == {a_cups.id, b_cups.id}
    assert set(listed_ids(client, headers, client_id=client_b.id)) == {b_cups.id}
    assert set(listed_ids(client, headers, status="in_progress", task_name="pick cup")) == {b_cups.id}


def test_client_id_filter_is_ignored_for_clients(client, create_user, create_request, auth_headers):
    client_a = create_user("client-a@example.com")
    client_b = create_user("client-b@example.com")
    own_request = create_request(client_a)
    create_request(client_b)

    assert listed_ids(client, auth_headers(client_a), client_id=client_b.id) == [own_request.id]


@pytest.mark.parametrize("params", [{"status": "done"}, {"limit": 0}, {"limit": 201}, {"offset": -1}])
def test_invalid_list_parameters_give_422(client, create_user, auth_headers, params):
    operator = create_user("ops@example.com", role="operator")

    response = client.get("/requests", params=params, headers=auth_headers(operator))

    assert response.status_code == 422


def test_list_is_newest_first_and_paginated(client, create_user, create_request, auth_headers, db_session):
    operator = create_user("ops@example.com", role="operator")
    owner = create_user("client-a@example.com")
    requests = [create_request(owner) for _ in range(5)]
    # Creation times deliberately not in id order, so the test proves the sort uses created_at.
    base = datetime(2026, 9, 1, tzinfo=timezone.utc)
    for dataset_request, minutes in zip(requests, [3, 0, 4, 1, 2]):
        dataset_request.created_at = base + timedelta(minutes=minutes)
    db_session.flush()
    newest_first = [requests[2].id, requests[0].id, requests[4].id, requests[3].id, requests[1].id]
    headers = auth_headers(operator)

    assert listed_ids(client, headers) == newest_first
    assert listed_ids(client, headers, limit=2, offset=0) == newest_first[0:2]
    assert listed_ids(client, headers, limit=2, offset=2) == newest_first[2:4]
    assert listed_ids(client, headers, limit=2, offset=4) == newest_first[4:]


def test_assigned_count_is_per_request(client, create_user, create_request, assign_episodes, auth_headers):
    operator = create_user("ops@example.com", role="operator")
    owner = create_user("client-a@example.com")
    with_three = create_request(owner)
    with_none = create_request(owner)
    assign_episodes(with_three, 3, assigned_by=operator)

    response = client.get("/requests", headers=auth_headers(operator))

    counts = {item["id"]: item["assigned_count"] for item in response.json()}
    assert counts == {with_three.id: 3, with_none.id: 0}
