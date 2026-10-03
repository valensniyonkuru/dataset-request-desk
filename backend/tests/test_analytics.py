"""GET /analytics, with expected numbers worked out by hand from small data sets."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, text

from app.db import engine
from app.models import RequestStatusHistory

FROM, TO = "2026-09-01", "2026-09-03"
EMPTY_BY_STATUS = {"submitted": 0, "in_progress": 0, "delivered": 0, "accepted": 0, "rejected": 0}


def utc(*parts) -> datetime:
    return datetime(*parts, tzinfo=timezone.utc)


def get_analytics(client, headers, start=FROM, end=TO):
    response = client.get("/analytics", params={"from": start, "to": end}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def operator_headers(create_user, auth_headers):
    return auth_headers(create_user("ops@example.com", role="operator"))


@pytest.fixture
def add_request(create_user, create_request, db_session):
    """Factory: add_request(created_at, [(to_status, hours after creation), ...]) -> Request.

    Writes the request and its history rows directly, with exact timestamps, so the
    expected hours are exact. The request's status is the last status in the history.
    """
    owner = create_user("client-a@example.com")

    def _add_request(created_at: datetime, steps: list[tuple[str, float]] = ()):
        statuses = ["submitted", *(to_status for to_status, _ in steps)]
        dataset_request = create_request(owner, status=statuses[-1])
        dataset_request.created_at = created_at
        db_session.add(
            RequestStatusHistory(
                request_id=dataset_request.id, from_status=None, to_status="submitted",
                changed_by=owner.id, changed_at=created_at,
            )
        )
        for from_status, (to_status, hours) in zip(statuses, steps):
            db_session.add(
                RequestStatusHistory(
                    request_id=dataset_request.id, from_status=from_status, to_status=to_status,
                    changed_by=owner.id, changed_at=created_at + timedelta(hours=hours),
                )
            )
        db_session.flush()
        return dataset_request

    return _add_request


# --- episodes per day, and the range boundaries ---


@pytest.fixture
def boundary_episodes(create_episode):
    create_episode(robot_id="arm-01", recorded_at=utc(2026, 9, 1, 0, 0, 0), task_name="pick cup")  # first instant: in
    create_episode(robot_id="arm-01", recorded_at=utc(2026, 9, 1, 12, 0), task_name="pick cup")
    create_episode(robot_id="arm-02", recorded_at=utc(2026, 9, 1, 23, 30), task_name="fold towel", quality="usable")
    create_episode(robot_id="arm-02", recorded_at=utc(2026, 9, 2, 8, 0), task_name="fold towel")
    # last instant of `to`: in
    create_episode(robot_id="arm-01", recorded_at=utc(2026, 9, 3, 23, 59, 59), task_name="pick cup", quality="bad")
    create_episode(robot_id="arm-01", recorded_at=utc(2026, 9, 4, 0, 0, 0), task_name="pick cup")  # day after `to`: out
    create_episode(robot_id="arm-02", recorded_at=utc(2026, 8, 31, 23, 59, 59), task_name="fold towel")  # before: out


def test_episodes_per_day_and_robot_include_both_ends_of_the_range(client, operator_headers, boundary_episodes):
    body = get_analytics(client, operator_headers)

    assert body["episodes_per_day"] == [
        {"date": "2026-09-01", "robot_id": "arm-01", "count": 2},
        {"date": "2026-09-01", "robot_id": "arm-02", "count": 1},
        {"date": "2026-09-02", "robot_id": "arm-02", "count": 1},
        {"date": "2026-09-03", "robot_id": "arm-01", "count": 1},
    ]  # sparse: no row for arm-01 on 2 Sept, nor arm-02 on 3 Sept


def test_top_tasks_on_the_boundary_data(client, operator_headers, boundary_episodes):
    body = get_analytics(client, operator_headers)

    # good and in range: pick cup at 00:00 and 12:00 on 1 Sept; fold towel at 08:00 on 2 Sept.
    assert body["top_tasks"] == [
        {"task_name": "pick cup", "good_episodes": 2},
        {"task_name": "fold towel", "good_episodes": 1},
    ]


def test_a_single_day_range(client, operator_headers, boundary_episodes):
    body = get_analytics(client, operator_headers, start="2026-09-02", end="2026-09-02")

    assert body["episodes_per_day"] == [{"date": "2026-09-02", "robot_id": "arm-02", "count": 1}]
    assert body["meta"]["days"] == 1


@pytest.mark.parametrize("zone", ["Pacific/Auckland", "America/Los_Angeles"])
def test_days_are_utc_days_whatever_the_session_time_zone(client, operator_headers, create_episode, db_session, zone):
    # 23:30 UTC on 1 Sept is already 2 Sept in Auckland, and 00:30 UTC on 2 Sept is still 1 Sept in Los Angeles.
    db_session.execute(text(f"SET TIME ZONE '{zone}'"))  # undone with the test transaction
    create_episode(robot_id="arm-01", recorded_at=utc(2026, 9, 1, 23, 30))
    create_episode(robot_id="arm-01", recorded_at=utc(2026, 9, 2, 0, 30))

    body = get_analytics(client, operator_headers)

    assert body["episodes_per_day"] == [
        {"date": "2026-09-01", "robot_id": "arm-01", "count": 1},
        {"date": "2026-09-02", "robot_id": "arm-01", "count": 1},
    ]


# --- top tasks ---


def test_top_tasks_count_only_good_episodes_sort_by_count_then_name_and_stop_at_five(
    client, operator_headers, create_episode
):
    day = utc(2026, 9, 2, 10, 0)
    good_counts = {
        "wipe table": 4, "pick cup": 3, "fold towel": 3, "open drawer": 2, "pour water": 2, "stack blocks": 1,
    }
    for task_name, good in good_counts.items():
        for _ in range(good):
            create_episode(task_name=task_name, quality="good", recorded_at=day)
    for _ in range(10):  # many usable and bad ones: they must not count
        create_episode(task_name="stack blocks", quality="usable", recorded_at=day)
        create_episode(task_name="stack blocks", quality="bad", recorded_at=day)

    body = get_analytics(client, operator_headers)

    assert body["top_tasks"] == [
        {"task_name": "wipe table", "good_episodes": 4},
        {"task_name": "fold towel", "good_episodes": 3},  # tie with pick cup: alphabetical
        {"task_name": "pick cup", "good_episodes": 3},
        {"task_name": "open drawer", "good_episodes": 2},
        {"task_name": "pour water", "good_episodes": 2},
    ]  # stack blocks (1 good, 20 usable or bad) is sixth and not shown


# --- requests: scope and status counts ---


def test_requests_are_counted_by_status_for_requests_created_in_the_range(client, operator_headers, add_request):
    add_request(utc(2026, 9, 1, 9, 0))  # submitted
    add_request(utc(2026, 9, 2, 9, 0), [("in_progress", 1)])
    add_request(utc(2026, 9, 3, 9, 0), [("in_progress", 1), ("delivered", 5)])
    add_request(utc(2026, 8, 31, 9, 0), [("in_progress", 1)])  # created before the range: not counted
    add_request(utc(2026, 9, 4, 0, 0), [("in_progress", 1)])  # created the day after `to`: not counted

    requests = get_analytics(client, operator_headers)["requests"]

    assert requests["by_status"] == {"submitted": 1, "in_progress": 1, "delivered": 1, "accepted": 0, "rejected": 0}
    assert requests["total"] == 3
    assert requests["delivered_count"] == 1


def test_a_request_created_before_the_range_but_delivered_in_it_is_not_counted(client, operator_headers, add_request):
    add_request(utc(2026, 8, 20, 9, 0), [("in_progress", 1), ("delivered", 24 * 12)])  # delivered on 1 Sept

    requests = get_analytics(client, operator_headers)["requests"]

    assert requests["total"] == 0
    assert requests["median_hours_submitted_to_delivered"] is None


def test_a_request_created_in_the_range_but_delivered_after_it_is_counted(client, operator_headers, add_request):
    add_request(utc(2026, 9, 3, 12, 0), [("in_progress", 1), ("delivered", 100)])  # delivered on 7 Sept

    requests = get_analytics(client, operator_headers)["requests"]

    assert requests["delivered_count"] == 1
    assert requests["median_hours_submitted_to_delivered"] == 100.0


# --- requests: the median time to the first delivery ---


@pytest.mark.parametrize(
    "hours_to_delivery, median",
    [
        ([10, 40, 20], 20.0),  # odd: the middle value
        ([10, 50, 20, 30], 25.0),  # even: the average of 20 and 30
        ([7.5], 7.5),  # exactly one
        ([1 + 20 / 60], 1.33),  # 1 h 20 min, rounded to 2 decimals
    ],
    ids=["odd", "even", "one", "rounded"],
)
def test_median_hours_to_the_first_delivery(client, operator_headers, add_request, hours_to_delivery, median):
    for n, hours in enumerate(hours_to_delivery):
        add_request(utc(2026, 9, 1, 8 + n, 0), [("in_progress", 0.5), ("delivered", hours)])

    requests = get_analytics(client, operator_headers)["requests"]

    assert requests["delivered_count"] == len(hours_to_delivery)
    assert requests["median_hours_submitted_to_delivered"] == median


def test_median_is_null_when_nothing_was_delivered(client, operator_headers, add_request):
    add_request(utc(2026, 9, 1, 9, 0), [("in_progress", 1)])

    requests = get_analytics(client, operator_headers)["requests"]

    assert requests["delivered_count"] == 0
    assert requests["median_hours_submitted_to_delivered"] is None


def test_a_rework_loop_keeps_the_first_delivery(client, operator_headers, add_request):
    add_request(
        utc(2026, 9, 1, 9, 0),
        [("in_progress", 1), ("delivered", 10), ("rejected", 12), ("in_progress", 13), ("delivered", 30)],
    )

    requests = get_analytics(client, operator_headers)["requests"]

    assert requests["by_status"]["delivered"] == 1
    assert requests["median_hours_submitted_to_delivered"] == 10.0  # not 30


def test_a_never_delivered_request_is_counted_by_status_but_not_in_the_median(client, operator_headers, add_request):
    add_request(utc(2026, 9, 1, 9, 0), [("in_progress", 1), ("delivered", 8), ("accepted", 9)])
    add_request(utc(2026, 9, 2, 9, 0), [("in_progress", 1)])  # never delivered

    requests = get_analytics(client, operator_headers)["requests"]

    assert requests["by_status"]["in_progress"] == 1
    assert requests["by_status"]["accepted"] == 1
    assert (requests["total"], requests["delivered_count"]) == (2, 1)
    assert requests["median_hours_submitted_to_delivered"] == 8.0


# --- empty data ---


def test_an_empty_database_gives_empty_sections(client, operator_headers):
    body = get_analytics(client, operator_headers)

    assert body["episodes_per_day"] == []
    assert body["top_tasks"] == []
    assert body["requests"] == {
        "by_status": EMPTY_BY_STATUS,
        "total": 0,
        "delivered_count": 0,
        "median_hours_submitted_to_delivered": None,
    }


def test_a_range_without_data_gives_empty_sections(client, operator_headers, boundary_episodes):
    body = get_analytics(client, operator_headers, start="2025-01-01", end="2025-01-31")

    assert body["episodes_per_day"] == []
    assert body["top_tasks"] == []
    assert body["requests"]["by_status"] == EMPTY_BY_STATUS


def test_meta_describes_the_range(client, operator_headers):
    meta = get_analytics(client, operator_headers)["meta"]

    assert (meta["from"], meta["to"], meta["days"]) == (FROM, TO, 3)
    assert meta["generated_at"]


# --- validation ---


@pytest.mark.parametrize(
    "params",
    [
        {"to": TO},  # from missing
        {"from": FROM},  # to missing
        {"from": "2026-09-03", "to": "2026-09-01"},  # from after to
        {"from": "2025-01-01", "to": "2026-01-02"},  # 367 days
        {"from": "2026-13-01", "to": TO},
        {"from": "yesterday", "to": TO},
    ],
    ids=["no-from", "no-to", "from-after-to", "367-days", "month-13", "not-a-date"],
)
def test_invalid_ranges_give_422(client, operator_headers, params):
    response = client.get("/analytics", params=params, headers=operator_headers)

    assert response.status_code == 422


def test_a_366_day_range_is_allowed(client, operator_headers):
    body = get_analytics(client, operator_headers, start="2024-01-01", end="2024-12-31")  # a leap year

    assert body["meta"]["days"] == 366


# --- who may see analytics ---


@pytest.mark.parametrize("role", ["operator", "admin"])
def test_operators_and_admins_can_see_analytics(client, create_user, auth_headers, role):
    get_analytics(client, auth_headers(create_user(f"{role}@example.com", role=role)))


def test_clients_get_403(client, create_user, auth_headers):
    headers = auth_headers(create_user("client-a@example.com"))

    assert client.get("/analytics", params={"from": FROM, "to": TO}, headers=headers).status_code == 403


def test_analytics_require_login(client):
    assert client.get("/analytics", params={"from": FROM, "to": TO}).status_code == 401


# --- a fixed number of queries ---


def count_statements(client, headers) -> int:
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        get_analytics(client, headers)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    return len(statements)


def test_the_number_of_queries_does_not_depend_on_the_data(client, operator_headers, create_episode, add_request):
    create_episode(recorded_at=utc(2026, 9, 1, 10, 0))
    with_little_data = count_statements(client, operator_headers)

    for n in range(200):
        create_episode(robot_id=["arm-01", "arm-02", "mobile-01"][n % 3], recorded_at=utc(2026, 9, 1 + n % 3, 10, 0))
    for n in range(20):
        add_request(utc(2026, 9, 2, 9, 0), [("in_progress", 1), ("delivered", 5 + n)])
    with_more_data = count_statements(client, operator_headers)

    assert with_little_data == with_more_data == 4  # loading the logged-in user, then the three analytics queries
