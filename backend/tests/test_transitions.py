from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.models import Request, RequestStatusHistory

STATUSES = ["submitted", "in_progress", "delivered", "accepted", "rejected"]
ROLES = ["client", "operator", "admin"]

# The workflow from the brief, written out again here on purpose. Importing
# app.workflow.TRANSITIONS instead would make these tests compare the table with itself.
ALLOWED = {
    ("submitted", "in_progress"): {"operator", "admin"},
    ("in_progress", "delivered"): {"operator", "admin"},
    ("delivered", "accepted"): {"client"},
    ("delivered", "rejected"): {"client"},
    ("rejected", "in_progress"): {"operator", "admin"},
}

AN_HOUR_AGO = datetime.now(timezone.utc) - timedelta(hours=1)


def transition(client, auth_headers, user, dataset_request, to_status):
    return client.post(
        f"/requests/{dataset_request.id}/transition", json={"to_status": to_status}, headers=auth_headers(user)
    )


def actor_with_role(role, owner, create_user):
    """The owning client for client cases, otherwise a new user with that role."""
    return owner if role == "client" else create_user(f"{role}@example.com", role=role)


def history_rows(db_session, dataset_request) -> list[RequestStatusHistory]:
    return list(
        db_session.scalars(
            select(RequestStatusHistory)
            .where(RequestStatusHistory.request_id == dataset_request.id)
            .order_by(RequestStatusHistory.id)
        )
    )


# --- every (from, to) pair for every role ---


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("to_status", STATUSES)
@pytest.mark.parametrize("from_status", STATUSES)
def test_transition_matrix(
    client, create_user, create_request, assign_episodes, auth_headers, from_status, to_status, role
):
    owner = create_user("owner@example.com")
    actor = actor_with_role(role, owner, create_user)
    dataset_request = create_request(owner, status=from_status, episodes_requested=2)
    if to_status == "delivered":
        assign_episodes(dataset_request, 2, assigned_by=actor)  # so the delivery gate never decides here

    response = transition(client, auth_headers, actor, dataset_request, to_status)

    if (from_status, to_status) not in ALLOWED:
        expected = 409
    elif role in ALLOWED[(from_status, to_status)]:
        expected = 200
    else:
        expected = 403
    assert response.status_code == expected, response.text
    if expected == 200:
        assert response.json()["status"] == to_status


# --- delivery needs enough assigned episodes ---


@pytest.mark.parametrize("assigned, expected", [(0, 409), (4, 409), (5, 200), (6, 200)])
def test_delivery_needs_enough_assigned_episodes(
    client, create_user, create_request, assign_episodes, auth_headers, db_session, assigned, expected
):
    operator = create_user("ops@example.com", role="operator")
    dataset_request = create_request(create_user("client-a@example.com"), status="in_progress", episodes_requested=5)
    assign_episodes(dataset_request, assigned, assigned_by=operator)

    response = transition(client, auth_headers, operator, dataset_request, "delivered")

    assert response.status_code == expected
    db_session.refresh(dataset_request)
    if expected == 409:
        assert response.json()["detail"] == f"Cannot deliver: {assigned} of 5 episodes assigned"
        assert dataset_request.status == "in_progress"
    else:
        assert dataset_request.status == "delivered"


# --- the full loop, through the API ---


def test_rework_loop_end_to_end(client, create_user, assign_episodes, auth_headers, db_session):
    owner = create_user("client-a@example.com")
    operator = create_user("ops@example.com", role="operator")
    created = client.post(
        "/requests",
        json={"task_name": "pick cup", "episodes_requested": 2, "deadline": "2030-01-01"},
        headers=auth_headers(owner),
    )
    dataset_request = db_session.get(Request, created.json()["id"])
    assign_episodes(dataset_request, 2, assigned_by=operator)

    steps = [
        (operator, "in_progress"),
        (operator, "delivered"),
        (owner, "rejected"),
        (operator, "in_progress"),
        (operator, "delivered"),
        (owner, "accepted"),
    ]
    for user, to_status in steps:
        response = transition(client, auth_headers, user, dataset_request, to_status)
        assert response.status_code == 200, f"{to_status}: {response.text}"

    history = [(h["from_status"], h["to_status"], h["changed_by_name"]) for h in response.json()["history"]]
    assert history == [
        (None, "submitted", owner.name),  # written when the request was created
        ("submitted", "in_progress", operator.name),
        ("in_progress", "delivered", operator.name),
        ("delivered", "rejected", owner.name),
        ("rejected", "in_progress", operator.name),
        ("in_progress", "delivered", operator.name),
        ("delivered", "accepted", owner.name),
    ]
    assert response.json()["available_transitions"] == []  # accepted is final


# --- history is written for every change, and only for changes ---


def test_a_successful_transition_adds_exactly_one_history_row(
    client, create_user, create_request, auth_headers, db_session
):
    operator = create_user("ops@example.com", role="operator")
    dataset_request = create_request(create_user("client-a@example.com"))
    dataset_request.updated_at = AN_HOUR_AGO
    db_session.flush()

    response = transition(client, auth_headers, operator, dataset_request, "in_progress")

    assert response.status_code == 200
    [row] = history_rows(db_session, dataset_request)
    assert (row.from_status, row.to_status, row.changed_by) == ("submitted", "in_progress", operator.id)
    db_session.refresh(dataset_request)
    assert dataset_request.updated_at > AN_HOUR_AGO


@pytest.mark.parametrize(
    "from_status, role, to_status",
    [
        ("submitted", "client", "in_progress"),  # 403: a real change, but not the client's
        ("submitted", "operator", "accepted"),  # 409: not a valid change
        ("in_progress", "operator", "delivered"),  # 409: no episodes assigned yet
    ],
)
def test_a_failed_transition_changes_nothing(
    client, create_user, create_request, auth_headers, db_session, from_status, role, to_status
):
    owner = create_user("client-a@example.com")
    actor = actor_with_role(role, owner, create_user)
    dataset_request = create_request(owner, status=from_status)
    dataset_request.updated_at = AN_HOUR_AGO
    db_session.flush()

    response = transition(client, auth_headers, actor, dataset_request, to_status)

    assert response.status_code in (403, 409)
    db_session.refresh(dataset_request)
    assert dataset_request.status == from_status
    assert dataset_request.updated_at == AN_HOUR_AGO
    assert history_rows(db_session, dataset_request) == []


def test_unknown_target_status_gives_422(client, create_user, create_request, auth_headers):
    operator = create_user("ops@example.com", role="operator")
    dataset_request = create_request(create_user("client-a@example.com"))

    response = transition(client, auth_headers, operator, dataset_request, "done")

    assert response.status_code == 422


def test_transition_on_a_missing_request_gives_404(client, create_user, auth_headers):
    operator = create_user("ops@example.com", role="operator")

    response = client.post("/requests/999999/transition", json={"to_status": "in_progress"}, headers=auth_headers(operator))

    assert response.status_code == 404


# --- what the detail says the current user may do ---


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("status", STATUSES)
def test_available_transitions_in_the_detail(client, create_user, create_request, auth_headers, status, role):
    owner = create_user("owner@example.com")
    actor = actor_with_role(role, owner, create_user)
    dataset_request = create_request(owner, status=status)

    response = client.get(f"/requests/{dataset_request.id}", headers=auth_headers(actor))

    expected = {to for (frm, to), roles in ALLOWED.items() if frm == status and role in roles}
    assert response.status_code == 200
    assert set(response.json()["available_transitions"]) == expected
