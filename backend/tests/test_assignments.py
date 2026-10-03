from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.models import Assignment

NOT_OPEN = "Episodes can only be assigned while the request is in_progress"


def assign(client, auth_headers, user, dataset_request, episode_ids):
    return client.post(
        f"/requests/{dataset_request.id}/assignments", json={"episode_ids": episode_ids}, headers=auth_headers(user)
    )


def unassign(client, auth_headers, user, dataset_request, episode_id):
    return client.delete(f"/requests/{dataset_request.id}/assignments/{episode_id}", headers=auth_headers(user))


def assigned_ids(db_session, dataset_request) -> set[str]:
    return set(db_session.scalars(select(Assignment.episode_id).where(Assignment.request_id == dataset_request.id)))


@pytest.fixture
def operator(create_user):
    return create_user("ops@example.com", role="operator")


@pytest.fixture
def owner(create_user):
    return create_user("client-a@example.com")


@pytest.fixture
def open_request(create_request, owner):
    """A request that is in_progress (open for assignments) and wants 5 episodes."""
    return create_request(owner, status="in_progress", episodes_requested=5)


# --- assigning ---


@pytest.mark.parametrize("role", ["operator", "admin"])
def test_operators_and_admins_can_assign(
    client, auth_headers, create_user, create_episode, open_request, db_session, role
):
    user = create_user(f"{role}@example.com", role=role)
    episode_ids = [create_episode().episode_id, create_episode().episode_id]

    response = assign(client, auth_headers, user, open_request, episode_ids)

    assert response.status_code == 201
    assert response.json() == {"request_id": open_request.id, "assigned_count": 2, "assigned_episode_ids": episode_ids}
    rows = db_session.scalars(select(Assignment).where(Assignment.request_id == open_request.id)).all()
    assert {row.episode_id for row in rows} == set(episode_ids)
    assert {row.assigned_by for row in rows} == {user.id}


def test_assigned_count_adds_up_across_calls(client, auth_headers, operator, create_episode, open_request):
    assign(client, auth_headers, operator, open_request, [create_episode().episode_id, create_episode().episode_id])

    response = assign(client, auth_headers, operator, open_request, [create_episode().episode_id])

    assert response.json()["assigned_count"] == 3
    detail = client.get(f"/requests/{open_request.id}", headers=auth_headers(operator))
    assert detail.json()["assigned_count"] == 3


def test_usable_episodes_can_be_assigned(client, auth_headers, operator, create_episode, open_request):
    usable = create_episode(quality="usable")

    response = assign(client, auth_headers, operator, open_request, [usable.episode_id])

    assert response.status_code == 201


def test_bad_episodes_are_rejected_and_nothing_is_saved(
    client, auth_headers, operator, create_episode, open_request, db_session
):
    good = create_episode(quality="good")
    bad = create_episode(quality="bad")

    response = assign(client, auth_headers, operator, open_request, [good.episode_id, bad.episode_id])

    assert response.status_code == 409
    assert response.json()["detail"] == f"Only good or usable episodes can be assigned: {bad.episode_id} (bad)"
    assert assigned_ids(db_session, open_request) == set()  # not even the good one


def test_unknown_episode_ids_give_422_listing_them(
    client, auth_headers, operator, create_episode, open_request, db_session
):
    known = create_episode()

    response = assign(client, auth_headers, operator, open_request, [known.episode_id, "EP-NOPE-1", "EP-NOPE-2"])

    assert response.status_code == 422
    assert response.json()["detail"] == "Unknown episode ids: EP-NOPE-1, EP-NOPE-2"
    assert assigned_ids(db_session, open_request) == set()


def test_episode_assigned_to_another_request_gives_409_naming_that_request(
    client, auth_headers, operator, owner, create_request, assign_episodes, open_request
):
    other_request = create_request(owner, status="in_progress")
    [taken] = assign_episodes(other_request, 1, assigned_by=operator)

    response = assign(client, auth_headers, operator, open_request, [taken.episode_id])

    assert response.status_code == 409
    assert response.json()["detail"] == f"Already assigned: {taken.episode_id} (request {other_request.id})"


def test_episode_already_assigned_to_this_request_gives_409(
    client, auth_headers, operator, assign_episodes, open_request
):
    [already] = assign_episodes(open_request, 1, assigned_by=operator)

    response = assign(client, auth_headers, operator, open_request, [already.episode_id])

    assert response.status_code == 409
    assert response.json()["detail"] == f"Already assigned: {already.episode_id} (request {open_request.id})"


def test_going_over_episodes_requested_gives_409_and_saves_nothing(
    client, auth_headers, operator, create_episode, assign_episodes, open_request, db_session
):
    existing = assign_episodes(open_request, 3, assigned_by=operator)
    batch = [create_episode().episode_id for _ in range(3)]

    response = assign(client, auth_headers, operator, open_request, batch)

    assert response.status_code == 409
    assert response.json()["detail"] == "Too many episodes: would be 6 of 5"
    assert assigned_ids(db_session, open_request) == {episode.episode_id for episode in existing}


def test_filling_exactly_up_to_episodes_requested_is_allowed(
    client, auth_headers, operator, create_episode, assign_episodes, open_request
):
    assign_episodes(open_request, 3, assigned_by=operator)

    response = assign(client, auth_headers, operator, open_request, [create_episode().episode_id for _ in range(2)])

    assert response.status_code == 201
    assert response.json()["assigned_count"] == 5


@pytest.mark.parametrize(
    "episode_ids",
    [
        [],
        [f"EP-X-{n}" for n in range(501)],
        ["EP-SAME", "EP-SAME"],
    ],
    ids=["empty", "more-than-500", "same-id-twice"],
)
def test_invalid_body_gives_422(client, auth_headers, operator, open_request, episode_ids):
    response = assign(client, auth_headers, operator, open_request, episode_ids)

    assert response.status_code == 422


def test_assigning_to_a_missing_request_gives_404(client, auth_headers, operator, create_episode):
    body = {"episode_ids": [create_episode().episode_id]}

    response = client.post("/requests/999999/assignments", json=body, headers=auth_headers(operator))

    assert response.status_code == 404


# --- only while in_progress ---


@pytest.mark.parametrize("status", ["submitted", "delivered", "accepted", "rejected"])
def test_assigning_is_only_allowed_in_progress(
    client, auth_headers, operator, owner, create_request, create_episode, db_session, status
):
    dataset_request = create_request(owner, status=status, episodes_requested=5)

    response = assign(client, auth_headers, operator, dataset_request, [create_episode().episode_id])

    assert response.status_code == 409
    assert response.json()["detail"] == NOT_OPEN
    assert assigned_ids(db_session, dataset_request) == set()


@pytest.mark.parametrize("status", ["submitted", "delivered", "accepted", "rejected"])
def test_unassigning_is_only_allowed_in_progress(
    client, auth_headers, operator, owner, create_request, assign_episodes, db_session, status
):
    dataset_request = create_request(owner, status=status)
    [episode] = assign_episodes(dataset_request, 1, assigned_by=operator)

    response = unassign(client, auth_headers, operator, dataset_request, episode.episode_id)

    assert response.status_code == 409
    assert response.json()["detail"] == NOT_OPEN
    assert assigned_ids(db_session, dataset_request) == {episode.episode_id}


# --- who may assign and unassign ---


def test_clients_cannot_assign_or_unassign(
    client, auth_headers, owner, operator, create_episode, assign_episodes, open_request
):
    [assigned] = assign_episodes(open_request, 1, assigned_by=operator)

    assert assign(client, auth_headers, owner, open_request, [create_episode().episode_id]).status_code == 403
    assert unassign(client, auth_headers, owner, open_request, assigned.episode_id).status_code == 403


def test_assigning_and_unassigning_require_login(client, operator, create_episode, assign_episodes, open_request):
    [assigned] = assign_episodes(open_request, 1, assigned_by=operator)

    body = {"episode_ids": [create_episode().episode_id]}
    post = client.post(f"/requests/{open_request.id}/assignments", json=body)
    delete = client.delete(f"/requests/{open_request.id}/assignments/{assigned.episode_id}")

    assert post.status_code == 401
    assert delete.status_code == 401


# --- unassigning ---


def test_unassign_removes_the_episode(client, auth_headers, operator, assign_episodes, open_request, db_session):
    first, second = assign_episodes(open_request, 2, assigned_by=operator)

    response = unassign(client, auth_headers, operator, open_request, first.episode_id)

    assert response.status_code == 204
    assert assigned_ids(db_session, open_request) == {second.episode_id}
    detail = client.get(f"/requests/{open_request.id}", headers=auth_headers(operator))
    assert detail.json()["assigned_count"] == 1


def test_an_unassigned_episode_can_go_to_another_request(
    client, auth_headers, operator, owner, create_request, create_episode, open_request
):
    other_request = create_request(owner, status="in_progress")
    episode = create_episode()
    assert assign(client, auth_headers, operator, open_request, [episode.episode_id]).status_code == 201

    assert unassign(client, auth_headers, operator, open_request, episode.episode_id).status_code == 204
    response = assign(client, auth_headers, operator, other_request, [episode.episode_id])

    assert response.status_code == 201


def test_unassigning_an_episode_of_another_request_gives_404_and_changes_nothing(
    client, auth_headers, operator, owner, create_request, assign_episodes, open_request, db_session
):
    other_request = create_request(owner, status="in_progress")
    [theirs] = assign_episodes(other_request, 1, assigned_by=operator)

    response = unassign(client, auth_headers, operator, open_request, theirs.episode_id)

    assert response.status_code == 404
    assert assigned_ids(db_session, other_request) == {theirs.episode_id}


def test_unassigning_an_episode_that_is_not_assigned_gives_404(
    client, auth_headers, operator, create_episode, open_request
):
    response = unassign(client, auth_headers, operator, open_request, create_episode().episode_id)

    assert response.status_code == 404


def test_unassigning_from_a_missing_request_gives_404(client, auth_headers, operator):
    response = client.delete("/requests/999999/assignments/EP-ANY", headers=auth_headers(operator))

    assert response.status_code == 404


# --- together with the step 4 workflow ---


def test_assign_exactly_enough_through_the_api_then_deliver(
    client, auth_headers, operator, owner, create_request, create_episode
):
    dataset_request = create_request(owner, status="in_progress", episodes_requested=3)
    episode_ids = [create_episode().episode_id for _ in range(3)]

    assert assign(client, auth_headers, operator, dataset_request, episode_ids).status_code == 201
    response = client.post(
        f"/requests/{dataset_request.id}/transition", json={"to_status": "delivered"}, headers=auth_headers(operator)
    )

    assert response.status_code == 200
    assert response.json()["status"] == "delivered"


def test_one_episode_short_still_blocks_delivery(client, auth_headers, operator, owner, create_request, create_episode):
    dataset_request = create_request(owner, status="in_progress", episodes_requested=3)
    episode_ids = [create_episode().episode_id for _ in range(3)]
    assign(client, auth_headers, operator, dataset_request, episode_ids)
    unassign(client, auth_headers, operator, dataset_request, episode_ids[0])

    response = client.post(
        f"/requests/{dataset_request.id}/transition", json={"to_status": "delivered"}, headers=auth_headers(operator)
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Cannot deliver: 2 of 3 episodes assigned"


# --- a request's episodes ---


def test_a_client_can_read_their_own_requests_episodes_in_assignment_order(
    client, auth_headers, operator, owner, assign_episodes, open_request, db_session
):
    episodes = assign_episodes(open_request, 3, assigned_by=operator)
    # Assignment times deliberately not in id order, so the test proves the sort uses assigned_at.
    base = datetime(2026, 9, 1, tzinfo=timezone.utc)
    rows = {row.episode_id: row for row in db_session.scalars(select(Assignment))}
    for episode, minutes in zip(episodes, [2, 0, 1]):
        rows[episode.episode_id].assigned_at = base + timedelta(minutes=minutes)
    db_session.flush()

    response = client.get(f"/requests/{open_request.id}/episodes", headers=auth_headers(owner))

    assert response.status_code == 200
    page = response.json()
    assert [item["episode_id"] for item in page["items"]] == [
        episodes[1].episode_id,
        episodes[2].episode_id,
        episodes[0].episode_id,
    ]
    assert page["total"] == 3
    assert {"quality", "robot_id", "recorded_at", "assigned_at"} <= page["items"][0].keys()


def test_request_episodes_are_paginated(client, auth_headers, operator, assign_episodes, open_request):
    episodes = assign_episodes(open_request, 3, assigned_by=operator)  # same assigned_at: ordered by id

    response = client.get(
        f"/requests/{open_request.id}/episodes", params={"limit": 2, "offset": 2}, headers=auth_headers(operator)
    )

    assert [item["episode_id"] for item in response.json()["items"]] == [episodes[2].episode_id]
    assert response.json()["total"] == 3


def test_another_clients_request_episodes_look_like_a_missing_request(
    client, auth_headers, create_user, operator, assign_episodes, open_request
):
    assign_episodes(open_request, 1, assigned_by=operator)
    other_client = create_user("client-b@example.com")

    foreign = client.get(f"/requests/{open_request.id}/episodes", headers=auth_headers(other_client))
    missing = client.get("/requests/999999/episodes", headers=auth_headers(other_client))

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()


def test_reading_request_episodes_requires_login(client, open_request):
    assert client.get(f"/requests/{open_request.id}/episodes").status_code == 401
