"""Importing the real seed/episodes.csv.

The expected numbers below were counted by hand from the file (see
docs/import-rules.md, part (a)), not read back from the importer.
"""

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select

from app.config import settings
from app.models import Episode, ImportRun

SEED_FILE = Path(settings.SEED_DIR) / "episodes.csv"

EXPECTED_SKIPPED = {"duplicate_in_file": 4, "already_exists": 0, "conflict": 0}
EXPECTED_REJECTED = {
    "malformed_row": 1,  # line 185, 5 columns
    "missing_episode_id": 1,  # line 59
    "missing_robot": 1,  # line 170
    "missing_task": 0,
    "missing_timestamp": 0,
    "missing_duration": 1,  # line 95
    "missing_operator": 1,  # line 190
    "missing_quality": 1,  # line 69
    "unknown_robot": 1,  # line 162, arm-99
    "invalid_timestamp": 1,  # line 131, "not a date"
    "future_timestamp": 1,  # line 113, 2031
    "invalid_duration": 3,  # lines 66 (45.5), 100 (-5), 187 (N/A)
    "duration_too_long": 1,  # line 188, 999999
    "invalid_quality": 1,  # line 79, excellent
}
EXPECTED_NORMALISED = {
    "episode_id": 1,  # line 189, ep-00003
    "robot_id": 1,  # line 32, " arm-01"
    "task_name": 2,  # lines 158 "  Pick Cup ", 163 "PICK CUP"
    "quality": 2,  # lines 65 "Good", 133 "USABLE"
    "operator_name": 0,
    "duration_seconds": 0,
    "recorded_at": 2,  # line 35 (space instead of T), line 127 (Z suffix)
    "date_assumed_day_first": 1,  # line 29, 14/08/2026 09:15
}
EXPECTED_PROBLEMS = [
    (59, None, "missing_episode_id"),
    (66, "EP-00018", "invalid_duration"),
    (69, "EP-00019", "missing_quality"),
    (79, "EP-00020", "invalid_quality"),
    (95, "EP-00016", "missing_duration"),
    (100, "EP-00017", "invalid_duration"),
    (113, "EP-00025", "future_timestamp"),
    (131, "EP-00023", "invalid_timestamp"),
    (162, "EP-00024", "unknown_robot"),
    (168, "EP-00011", "conflict_in_file"),  # differs from line 3 (quality)
    (170, "EP-00021", "missing_robot"),
    (185, "EP-90001", "malformed_row"),
    (187, "EP-90003", "invalid_duration"),
    (188, "EP-90004", "duration_too_long"),
    (189, "EP-00003", "conflict_in_file"),  # differs from line 9 once ep- is uppercased
    (190, "EP-90005", "missing_operator"),
]


def episode_count(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(Episode))


def test_importing_the_seed_file_gives_the_counts_found_by_hand(import_file, db_session):
    _, report = import_file(SEED_FILE.read_bytes(), file_name="episodes.csv")

    assert report["status"] == "finished"
    assert report["total_rows"] == 189
    assert report["blank_lines"] == 2  # line 191 empty, line 192 two spaces
    assert report["imported"] == 171
    assert report["skipped"] == EXPECTED_SKIPPED
    assert report["rejected"] == EXPECTED_REJECTED
    assert report["normalised"] == EXPECTED_NORMALISED
    assert [(p["line"], p["episode_id"], p["reason"]) for p in report["problems"]] == EXPECTED_PROBLEMS
    assert report["problems_truncated"] is False
    assert episode_count(db_session) == 171


def test_seed_values_are_stored_normalised(import_file, db_session):
    import_file(SEED_FILE.read_bytes())

    assert db_session.get(Episode, "EP-00008").robot_id == "arm-01"  # was " arm-01"
    assert db_session.get(Episode, "EP-00006").task_name == "pick cup"  # was "  Pick Cup "
    assert db_session.get(Episode, "EP-00010").quality == "usable"  # was "USABLE"
    assert db_session.get(Episode, "EP-00014").recorded_at == datetime(2026, 8, 14, 9, 15, tzinfo=timezone.utc)
    assert db_session.get(Episode, "EP-90002").task_name == "pick cup, then place"  # quoted comma
    assert db_session.get(Episode, "EP-00011").quality == "bad"  # first copy wins (line 3)
    assert db_session.get(Episode, "EP-00003").robot_id == "humanoid-01"  # line 9, not ep-00003 on line 189


def test_importing_the_seed_file_twice_imports_nothing_the_second_time(import_file, db_session):
    first_run_id, _ = import_file(SEED_FILE.read_bytes())
    count_after_first = episode_count(db_session)

    _, second = import_file(SEED_FILE.read_bytes())

    assert second["imported"] == 0
    assert second["skipped"] == {"duplicate_in_file": 4, "already_exists": 171, "conflict": 0}
    assert second["rejected"] == EXPECTED_REJECTED
    assert second["previous_runs_with_same_file"] == [first_run_id]
    assert episode_count(db_session) == count_after_first


def test_the_run_and_its_report_are_stored(import_file, db_session):
    run_id, report = import_file(SEED_FILE.read_bytes(), file_name="episodes.csv")

    run = db_session.get(ImportRun, run_id)
    assert run.file_name == "episodes.csv"
    assert len(run.file_sha256) == 64
    assert run.finished_at is not None
    assert run.report == report
    imported_by_this_run = db_session.scalar(
        select(func.count()).select_from(Episode).where(Episode.import_run_id == run_id)
    )
    assert imported_by_this_run == 171
