"""Each import rule from docs/import-rules.md, with a small crafted CSV."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, text

from app import importer
from app.importer import CHUNK_SIZE, ImportFileError
from app.models import Episode, ImportRun

COLUMNS = ("episode_id", "robot_id", "task_name", "recorded_at", "duration_seconds", "operator_name", "quality")
HEADER = ",".join(COLUMNS)
DEFAULTS = {
    "episode_id": "EP-1",
    "robot_id": "arm-01",
    "task_name": "pick cup",
    "recorded_at": "2026-09-01T10:00:00",
    "duration_seconds": "30",
    "operator_name": "Aline",
    "quality": "good",
}
TEN_AM = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)


def row(**changes) -> str:
    """One CSV line: the default valid episode, with some values changed."""
    return ",".join((DEFAULTS | changes)[column] for column in COLUMNS)


def csv_text(*lines: str, header: str = HEADER) -> str:
    return "\n".join([header, *lines]) + "\n"


def only_nonzero(counts: dict) -> dict:
    return {key: value for key, value in counts.items() if value}


def count(db_session, model) -> int:
    return db_session.scalar(select(func.count()).select_from(model))


# --- normalisation: the row is imported with a cleaned value ---


@pytest.mark.parametrize(
    "column, raw, stored, kind",
    [
        ("episode_id", " ep-7 ", "EP-7", "episode_id"),
        ("robot_id", " ARM-01 ", "arm-01", "robot_id"),
        ("task_name", "  Pick   Cup ", "pick cup", "task_name"),
        ("quality", " GOOD ", "good", "quality"),
        ("operator_name", "  diane  ", "Diane", "operator_name"),
        ("operator_name", "JEAN-CLAUDE", "Jean-Claude", "operator_name"),
        ("operator_name", "Anne   Marie", "Anne Marie", "operator_name"),
        ("duration_seconds", " 45 ", 45, "duration_seconds"),
        ("duration_seconds", "45.0", 45, "duration_seconds"),
        ("recorded_at", "2026-09-01 10:00:00", TEN_AM, "recorded_at"),
        ("recorded_at", "2026-09-01T10:00:00Z", TEN_AM, "recorded_at"),
        ("recorded_at", " 2026-09-01T10:00:00 ", TEN_AM, "recorded_at"),
        ("recorded_at", "2026-09-01", TEN_AM.replace(hour=0), "recorded_at"),
        ("recorded_at", "01/09/2026 10:00", TEN_AM, "date_assumed_day_first"),
        ("recorded_at", "01/09/2026 10:00:00", TEN_AM, "date_assumed_day_first"),
        ("recorded_at", "01/09/2026", TEN_AM.replace(hour=0), "date_assumed_day_first"),
        ("recorded_at", "05/09/2026 10:00", TEN_AM.replace(day=5), "date_assumed_day_first"),  # 5 Sept, not 9 May
    ],
)
def test_normalisation(import_file, db_session, column, raw, stored, kind):
    _, report = import_file(csv_text(row(**{column: raw})))

    assert report["imported"] == 1
    assert only_nonzero(report["normalised"]) == {kind: 1}
    episode_id = "EP-7" if column == "episode_id" else "EP-1"
    assert getattr(db_session.get(Episode, episode_id), column) == stored


def test_values_already_in_the_normal_form_are_not_counted(import_file, db_session):
    _, report = import_file(csv_text(row(operator_name="McDonald")))

    assert only_nonzero(report["normalised"]) == {}
    assert db_session.get(Episode, "EP-1").operator_name == "McDonald"  # mixed case is kept


# --- rejection: the row is not imported, and is listed with the reason and value ---


@pytest.mark.parametrize(
    "changes, reason, value",
    [
        ({"episode_id": ""}, "missing_episode_id", ""),
        ({"robot_id": "   "}, "missing_robot", "   "),
        ({"task_name": ""}, "missing_task", ""),
        ({"recorded_at": ""}, "missing_timestamp", ""),
        ({"duration_seconds": ""}, "missing_duration", ""),
        ({"operator_name": ""}, "missing_operator", ""),
        ({"quality": ""}, "missing_quality", ""),
        ({"robot_id": "arm-99"}, "unknown_robot", "arm-99"),
        ({"recorded_at": "not a date"}, "invalid_timestamp", "not a date"),
        ({"recorded_at": "2026-13-01T10:00:00"}, "invalid_timestamp", "2026-13-01T10:00:00"),
        ({"recorded_at": "31/02/2026 10:00"}, "invalid_timestamp", "31/02/2026 10:00"),
        ({"recorded_at": "2026-09-01T10:00"}, "invalid_timestamp", "2026-09-01T10:00"),  # no seconds
        ({"recorded_at": "2026-09-01T10:00:00+02:00"}, "invalid_timestamp", "2026-09-01T10:00:00+02:00"),
        ({"duration_seconds": "N/A"}, "invalid_duration", "N/A"),
        ({"duration_seconds": "0"}, "invalid_duration", "0"),
        ({"duration_seconds": "-5"}, "invalid_duration", "-5"),
        ({"duration_seconds": "45.5"}, "invalid_duration", "45.5"),
        ({"duration_seconds": "1e3"}, "invalid_duration", "1e3"),
        ({"duration_seconds": "3601"}, "duration_too_long", "3601"),
        ({"duration_seconds": "999999"}, "duration_too_long", "999999"),
        ({"quality": "excellent"}, "invalid_quality", "excellent"),
    ],
)
def test_rejection(import_file, db_session, changes, reason, value):
    _, report = import_file(csv_text(row(**changes)))

    assert report["imported"] == 0
    assert only_nonzero(report["rejected"]) == {reason: 1}
    expected_id = None if "episode_id" in changes else "EP-1"
    assert report["problems"] == [{"line": 2, "episode_id": expected_id, "reason": reason, "value": value}]
    assert count(db_session, Episode) == 0


def test_a_timestamp_more_than_a_day_ahead_is_rejected(import_file):
    in_two_days = (datetime.now(timezone.utc) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S")

    _, report = import_file(csv_text(row(recorded_at=in_two_days)))

    assert only_nonzero(report["rejected"]) == {"future_timestamp": 1}


def test_a_timestamp_less_than_a_day_ahead_is_accepted(import_file):
    in_twelve_hours = (datetime.now(timezone.utc) + timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%S")

    _, report = import_file(csv_text(row(recorded_at=in_twelve_hours)))

    assert report["imported"] == 1


def test_the_longest_allowed_duration_is_accepted(import_file):
    _, report = import_file(csv_text(row(duration_seconds="3600")))

    assert report["imported"] == 1


@pytest.mark.parametrize("line", ["EP-1,arm-01,pick cup,2026-09-01T10:00:00,30", row() + ",extra"])
def test_a_row_with_the_wrong_number_of_columns_is_malformed(import_file, line):
    _, report = import_file(csv_text(line))

    assert only_nonzero(report["rejected"]) == {"malformed_row": 1}
    assert report["problems"][0]["episode_id"] == "EP-1"


def test_the_first_problem_found_is_the_reason(import_file):
    _, report = import_file(csv_text(row(robot_id="arm-99", quality="excellent")))

    assert only_nonzero(report["rejected"]) == {"unknown_robot": 1}


# --- duplicates within the file ---


def test_an_exact_duplicate_is_skipped_silently(import_file, db_session):
    _, report = import_file(csv_text(row(), row()))

    assert report["imported"] == 1
    assert only_nonzero(report["skipped"]) == {"duplicate_in_file": 1}
    assert report["problems"] == []


def test_a_differing_duplicate_is_skipped_and_reported_and_the_first_copy_wins(import_file, db_session):
    _, report = import_file(csv_text(row(quality="good"), row(quality="bad")))

    assert only_nonzero(report["skipped"]) == {"duplicate_in_file": 1}
    assert report["problems"] == [
        {"line": 3, "episode_id": "EP-1", "reason": "conflict_in_file", "value": "differs from line 2"}
    ]
    assert db_session.get(Episode, "EP-1").quality == "good"


def test_ids_that_only_differ_in_case_are_duplicates(import_file):
    _, report = import_file(csv_text(row(episode_id="EP-1"), row(episode_id="ep-1")))

    assert only_nonzero(report["skipped"]) == {"duplicate_in_file": 1}


def test_a_rejected_first_copy_does_not_block_a_later_valid_one(import_file, db_session):
    _, report = import_file(csv_text(row(quality="excellent"), row(quality="usable")))

    assert report["imported"] == 1
    assert only_nonzero(report["rejected"]) == {"invalid_quality": 1}
    assert db_session.get(Episode, "EP-1").quality == "usable"


# --- rows that are already in the database ---


def test_an_identical_episode_in_the_database_is_already_exists(import_file, create_episode):
    create_episode(episode_id="EP-1", recorded_at=TEN_AM)

    _, report = import_file(csv_text(row()))

    assert report["imported"] == 0
    assert only_nonzero(report["skipped"]) == {"already_exists": 1}
    assert report["problems"] == []


def test_a_different_episode_in_the_database_is_a_conflict_and_is_not_overwritten(
    import_file, create_episode, db_session
):
    stored = create_episode(episode_id="EP-1", recorded_at=TEN_AM, duration_seconds=99, quality="bad")

    _, report = import_file(csv_text(row(duration_seconds="30", quality="good")))

    assert only_nonzero(report["skipped"]) == {"conflict": 1}
    assert report["problems"] == [
        {
            "line": 2,
            "episode_id": "EP-1",
            "reason": "conflict",
            "fields": {
                "duration_seconds": {"file": 30, "database": 99},
                "quality": {"file": "good", "database": "bad"},
            },
        }
    ]
    db_session.refresh(stored)
    assert (stored.duration_seconds, stored.quality) == (99, "bad")


def test_a_time_in_a_repeated_daylight_saving_hour_is_still_already_exists(import_file, db_session):
    # Timestamps come back in the database session's time zone. In Cairo, local
    # 23:00 to 23:59 on 30 October 2025 happened twice when daylight saving ended.
    # (Found when the volume test ran against a Postgres set to Africa/Cairo.)
    db_session.execute(text("SET TIME ZONE 'Africa/Cairo'"))  # undone with the test transaction
    content = csv_text(row(recorded_at="2025-10-30T20:25:00"))
    import_file(content)

    _, second = import_file(content)

    assert only_nonzero(second["skipped"]) == {"already_exists": 1}


def test_imported_episodes_point_to_their_import_run(import_file, db_session):
    run_id, _ = import_file(csv_text(row()))

    assert db_session.get(Episode, "EP-1").import_run_id == run_id


def test_previous_runs_with_the_same_file_are_listed(import_file):
    content = csv_text(row())
    first_id, first = import_file(content)
    second_id, second = import_file(content)
    _, third = import_file(content)

    assert first["previous_runs_with_same_file"] == []
    assert second["previous_runs_with_same_file"] == [first_id]
    assert third["previous_runs_with_same_file"] == [first_id, second_id]


# --- the file as a whole ---


@pytest.mark.parametrize(
    "content, message",
    [
        (b"", "The file is empty"),
        (b"\xef\xbb\xbf", "The file is empty"),  # only a BOM
        (b"\n\n", "The file is empty"),
        (b"episode_id,robot_id\nEP-1,arm-01\n", "Missing required columns: task_name, recorded_at"),
        (csv_text(row()).encode() + b"EP-2,arm-01,pick cup,2026-09-01T10:00:00,30,Ren\xe9,good\n", "not valid UTF-8"),
    ],
    ids=["empty", "only-bom", "only-blank-lines", "missing-columns", "invalid-utf8-at-the-end"],
)
def test_a_refused_file_saves_nothing(import_file, db_session, content, message):
    with pytest.raises(ImportFileError) as refused:
        import_file(content)

    assert refused.value.status_code == 422
    assert message in refused.value.detail
    assert count(db_session, Episode) == 0
    assert count(db_session, ImportRun) == 0


def test_header_names_are_trimmed_case_insensitive_in_any_order_and_extra_columns_are_ignored(import_file, db_session):
    header = " Quality ,EPISODE_ID,notes,robot_id,task_name,recorded_at,duration_seconds,operator_name"
    line = "usable,EP-1,ignored,arm-02,pick cup,2026-09-01T10:00:00,30,Aline"

    _, report = import_file(csv_text(line, header=header))

    assert report["imported"] == 1
    episode = db_session.get(Episode, "EP-1")
    assert (episode.quality, episode.robot_id) == ("usable", "arm-02")


def test_a_byte_order_mark_is_accepted(import_file):
    _, report = import_file(b"\xef\xbb\xbf" + csv_text(row()).encode())

    assert report["imported"] == 1


def test_windows_line_endings_are_accepted(import_file):
    _, report = import_file(csv_text(row(), row(episode_id="EP-2")).replace("\n", "\r\n"))

    assert report["imported"] == 2


def test_lines_without_values_are_counted_as_blank_lines(import_file):
    _, report = import_file(csv_text("", row(), "   ", ",,,,,,", row(episode_id="EP-2"), ""))

    assert report["blank_lines"] == 4
    assert report["total_rows"] == 2
    assert report["imported"] == 2


def test_a_quoted_comma_stays_inside_the_field(import_file, db_session):
    _, report = import_file(csv_text(row(task_name='"pick cup, then place"')))

    assert db_session.get(Episode, "EP-1").task_name == "pick cup, then place"


def test_a_header_only_file_imports_nothing(import_file):
    _, report = import_file(csv_text())

    assert (report["total_rows"], report["imported"]) == (0, 0)


def test_problems_are_capped_but_still_counted(import_file):
    _, report = import_file(csv_text(*[row(episode_id=f"EP-{n}", quality="excellent") for n in range(250)]))

    assert report["rejected"]["invalid_quality"] == 250
    assert len(report["problems"]) == 200
    assert report["problems_truncated"] is True


# --- chunk boundaries ---

# Duplicates, an in-file conflict and database conflicts spread over the file,
# so that with small chunk sizes they fall on both sides of chunk boundaries.
STRADDLING_LINES = [
    row(episode_id="EP-1"),
    row(episode_id="EP-2"),
    row(episode_id="EP-3", quality="bad"),  # differs from the database copy
    row(episode_id="EP-1"),  # exact duplicate of line 2
    row(episode_id="EP-4", robot_id="arm-99"),  # rejected
    row(episode_id="EP-5"),  # identical to the database copy
    row(episode_id="EP-2", quality="usable"),  # differs from line 3
    row(episode_id="EP-6"),
    row(episode_id="ep-6"),  # duplicate of line 9 after uppercasing
    row(episode_id="EP-7", duration_seconds="45.0"),
]


@pytest.mark.parametrize("chunk_size", [1, 2, 3, CHUNK_SIZE])
def test_results_do_not_depend_on_the_chunk_size(import_file, create_episode, db_session, chunk_size):
    create_episode(episode_id="EP-3", recorded_at=TEN_AM, quality="good")
    create_episode(episode_id="EP-5", recorded_at=TEN_AM)

    _, report = import_file(csv_text(*STRADDLING_LINES), chunk_size=chunk_size)

    assert report["total_rows"] == 10
    assert report["imported"] == 4  # EP-1, EP-2, EP-6, EP-7
    assert report["skipped"] == {"duplicate_in_file": 3, "already_exists": 1, "conflict": 1}
    assert only_nonzero(report["rejected"]) == {"unknown_robot": 1}
    assert only_nonzero(report["normalised"]) == {"episode_id": 1, "duration_seconds": 1}
    problems = sorted((p["line"], p["reason"]) for p in report["problems"])
    assert problems == [(4, "conflict"), (6, "unknown_robot"), (8, "conflict_in_file")]
    stored = set(db_session.scalars(select(Episode.episode_id)))
    assert stored == {"EP-1", "EP-2", "EP-3", "EP-5", "EP-6", "EP-7"}


# --- a failure half-way through ---


def test_a_failure_keeps_committed_chunks_records_the_error_and_a_rerun_finishes(
    import_file, db_session, monkeypatch
):
    content = csv_text(*[row(episode_id=f"EP-{n}") for n in range(1, 5)])
    real_save_chunk = importer._save_chunk
    calls = []

    def save_chunk_failing_the_second_time(*args):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("database went away")
        real_save_chunk(*args)

    monkeypatch.setattr(importer, "_save_chunk", save_chunk_failing_the_second_time)
    with pytest.raises(RuntimeError):
        import_file(content, chunk_size=2)

    failed_run = db_session.scalars(select(ImportRun)).one()
    assert failed_run.finished_at is not None
    assert failed_run.report["status"] == "failed"
    assert failed_run.report["error"] == "RuntimeError: database went away"
    assert set(db_session.scalars(select(Episode.episode_id))) == {"EP-1", "EP-2"}  # the first chunk stays

    monkeypatch.setattr(importer, "_save_chunk", real_save_chunk)
    _, rerun = import_file(content, chunk_size=2)

    assert rerun["imported"] == 2
    assert rerun["skipped"]["already_exists"] == 2
    assert set(db_session.scalars(select(Episode.episode_id))) == {"EP-1", "EP-2", "EP-3", "EP-4"}


def test_broken_csv_syntax_part_way_stops_with_422_and_keeps_earlier_chunks(import_file, db_session):
    content = csv_text(row(episode_id="EP-1"), row(episode_id="EP-2"), 'EP-3,arm-01,"pick cup')

    with pytest.raises(ImportFileError) as refused:
        import_file(content, chunk_size=1)

    assert refused.value.status_code == 422
    assert "Unreadable CSV at line" in refused.value.detail
    assert set(db_session.scalars(select(Episode.episode_id))) == {"EP-1", "EP-2"}
    assert db_session.scalars(select(ImportRun)).one().report["status"] == "failed"
