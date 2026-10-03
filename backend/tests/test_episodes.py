from datetime import datetime, timedelta, timezone

import pytest

from app.models import Assignment

BASE_TIME = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def list_episodes(client, headers, **params) -> dict:
    response = client.get("/episodes", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def ids(page: dict) -> set[str]:
    return {item["episode_id"] for item in page["items"]}


@pytest.fixture
def library(create_user, create_request, create_episode, db_session):
    """An operator, five varied episodes, and one of them assigned to a request."""
    operator = create_user("ops@example.com", role="operator")
    episodes = {
        "cup_good_arm1": create_episode(task_name="pick cup", quality="good", robot_id="arm-01"),
        "cup_bad_arm1": create_episode(task_name="pick cup", quality="bad", robot_id="arm-01"),
        "cup_usable_arm2": create_episode(task_name="pick cup", quality="usable", robot_id="arm-02"),
        "towel_good_arm2": create_episode(task_name="fold towel", quality="good", robot_id="arm-02"),
        "towel_good_mobile": create_episode(task_name="fold towel", quality="good", robot_id="mobile-01"),
    }
    dataset_request = create_request(create_user("client-a@example.com"), status="in_progress")
    db_session.add(
        Assignment(
            request_id=dataset_request.id, episode_id=episodes["cup_good_arm1"].episode_id, assigned_by=operator.id
        )
    )
    db_session.flush()
    return operator, episodes, dataset_request


def test_every_field_and_the_assigned_request_are_returned(client, auth_headers, library):
    operator, episodes, dataset_request = library

    page = list_episodes(client, auth_headers(operator), task_name="pick cup", quality="good")

    [item] = page["items"]
    assert item["episode_id"] == episodes["cup_good_arm1"].episode_id
    assert item["robot_id"] == "arm-01"
    assert item["duration_seconds"] == 30
    assert item["operator_name"] == "Aline"
    assert item["assigned_request_id"] == dataset_request.id
    assert {"recorded_at", "import_run_id", "created_at"} <= item.keys()


@pytest.mark.parametrize(
    "params, expected",
    [
        ({"task_name": "fold towel"}, {"towel_good_arm2", "towel_good_mobile"}),
        ({"quality": "usable"}, {"cup_usable_arm2"}),
        ({"robot_id": "arm-02"}, {"cup_usable_arm2", "towel_good_arm2"}),
        ({"assigned": "true"}, {"cup_good_arm1"}),
        ({"assigned": "false"}, {"cup_bad_arm1", "cup_usable_arm2", "towel_good_arm2", "towel_good_mobile"}),
        ({"task_name": "pick cup", "quality": "good"}, {"cup_good_arm1"}),
        ({"task_name": "fold towel", "robot_id": "arm-02", "assigned": "false"}, {"towel_good_arm2"}),
        ({"quality": "good", "assigned": "false"}, {"towel_good_arm2", "towel_good_mobile"}),
        # quality given more than once: any of the values
        ({"quality": ["good", "usable"]}, {"cup_good_arm1", "cup_usable_arm2", "towel_good_arm2", "towel_good_mobile"}),
        ({"quality": ["usable", "bad"]}, {"cup_usable_arm2", "cup_bad_arm1"}),
        ({"quality": ["good", "usable"], "task_name": "pick cup", "assigned": "false"}, {"cup_usable_arm2"}),
        ({"quality": ["good", "usable"], "assigned": "true"}, {"cup_good_arm1"}),
        ({"task_name": "no such task"}, set()),
    ],
)
def test_filters(client, auth_headers, library, params, expected):
    operator, episodes, _ = library

    page = list_episodes(client, auth_headers(operator), **params)

    assert ids(page) == {episodes[name].episode_id for name in expected}
    assert page["total"] == len(expected)


def test_unassigned_and_assigned_episodes_both_appear_without_the_filter(client, auth_headers, library):
    operator, episodes, _ = library

    page = list_episodes(client, auth_headers(operator))

    assert page["total"] == 5
    assert ids(page) == {episode.episode_id for episode in episodes.values()}


def test_newest_recording_first_ties_by_episode_id(client, create_user, create_episode, auth_headers):
    operator = create_user("ops@example.com", role="operator")
    oldest = create_episode(recorded_at=BASE_TIME)
    newest = create_episode(recorded_at=BASE_TIME + timedelta(hours=2))
    tie_b = create_episode(episode_id="EP-TIE-B", recorded_at=BASE_TIME + timedelta(hours=1))
    tie_a = create_episode(episode_id="EP-TIE-A", recorded_at=BASE_TIME + timedelta(hours=1))

    page = list_episodes(client, auth_headers(operator))

    order = [item["episode_id"] for item in page["items"]]
    assert order == [newest.episode_id, tie_a.episode_id, tie_b.episode_id, oldest.episode_id]


def test_pagination_returns_the_total(client, create_user, create_episode, auth_headers):
    operator = create_user("ops@example.com", role="operator")
    episodes = [create_episode(recorded_at=BASE_TIME - timedelta(minutes=n)) for n in range(5)]  # newest first
    headers = auth_headers(operator)

    first = list_episodes(client, headers, limit=2, offset=0)
    second = list_episodes(client, headers, limit=2, offset=2)
    last = list_episodes(client, headers, limit=2, offset=4)
    beyond = list_episodes(client, headers, limit=2, offset=10)

    assert [item["episode_id"] for item in first["items"]] == [episodes[0].episode_id, episodes[1].episode_id]
    assert [item["episode_id"] for item in second["items"]] == [episodes[2].episode_id, episodes[3].episode_id]
    assert [item["episode_id"] for item in last["items"]] == [episodes[4].episode_id]
    assert beyond["items"] == []
    assert {page["total"] for page in (first, second, last, beyond)} == {5}
    assert (first["limit"], first["offset"]) == (2, 0)


@pytest.mark.parametrize(
    "params",
    [
        {"quality": "excellent"},
        {"quality": ["excellent", "good"]},  # one invalid value among valid ones (the old code kept only the last)
        {"assigned": "maybe"},
        {"limit": 0},
        {"limit": 201},
        {"offset": -1},
    ],
)
def test_invalid_parameters_give_422(client, create_user, auth_headers, params):
    operator = create_user("ops@example.com", role="operator")

    response = client.get("/episodes", params=params, headers=auth_headers(operator))

    assert response.status_code == 422


def test_admin_can_list_episodes(client, create_user, auth_headers):
    admin = create_user("admin@example.com", role="admin")

    assert client.get("/episodes", headers=auth_headers(admin)).status_code == 200


def test_clients_cannot_list_episodes(client, create_user, auth_headers):
    client_user = create_user("client-a@example.com")

    assert client.get("/episodes", headers=auth_headers(client_user)).status_code == 403


def test_listing_episodes_requires_login(client):
    assert client.get("/episodes").status_code == 401


def test_several_qualities_keep_total_and_pagination_right(client, create_user, create_episode, auth_headers):
    operator = create_user("ops@example.com", role="operator")
    for n, quality in enumerate(["good", "bad", "usable", "good", "bad", "usable", "good"]):
        create_episode(quality=quality, recorded_at=BASE_TIME - timedelta(minutes=n))  # newest first
    headers = auth_headers(operator)

    first = list_episodes(client, headers, quality=["good", "usable"], limit=3, offset=0)
    second = list_episodes(client, headers, quality=["good", "usable"], limit=3, offset=3)

    assert first["total"] == second["total"] == 5  # the two bad ones are not counted
    assert [item["quality"] for item in first["items"]] == ["good", "usable", "good"]
    assert [item["quality"] for item in second["items"]] == ["usable", "good"]


def test_the_docs_describe_quality_as_repeatable(client):
    parameters = client.get("/openapi.json").json()["paths"]["/episodes"]["get"]["parameters"]
    quality = next(parameter for parameter in parameters if parameter["name"] == "quality")

    assert quality["schema"]["type"] == "array"
    assert quality["examples"]["good or usable"]["value"] == ["good", "usable"]
