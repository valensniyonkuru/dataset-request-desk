"""Imports running at the same time, with real database connections.

Like the other race tests, these commit their own rows (through real sessions,
not the rollback-wrapped db_session) and delete them again afterwards, even
when a test fails.
"""

import io
import threading
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, func, select

from app.db import SessionLocal
from app.importer import import_episodes
from app.models import Episode, ImportRun

FILE_NAME = "race.csv"
EPISODE_IDS = [f"EP-IMPRACE-{n:04d}" for n in range(300)]
RECORDED_AT = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
CONTENT = (
    "episode_id,robot_id,task_name,recorded_at,duration_seconds,operator_name,quality\n"
    + "".join(f"{episode_id},arm-01,pick cup,2026-09-01T10:00:00,30,Aline,good\n" for episode_id in EPISODE_IDS)
    + "EP-IMPRACE-BAD,arm-99,pick cup,2026-09-01T10:00:00,30,Aline,good\n"  # rejected
).encode()


@pytest.fixture
def cleanup():
    yield
    with SessionLocal() as session:
        session.execute(delete(Episode).where(Episode.episode_id.like("EP-IMPRACE-%")))
        session.execute(delete(ImportRun).where(ImportRun.file_name == FILE_NAME))
        session.commit()


def import_in_own_session(chunk_size: int) -> dict:
    with SessionLocal() as db:
        _, report = import_episodes(
            db, io.BytesIO(CONTENT), file_name=FILE_NAME, started_by=None, chunk_size=chunk_size
        )
    return report


def stored_count() -> int:
    with SessionLocal() as session:
        return session.scalar(
            select(func.count()).select_from(Episode).where(Episode.episode_id.in_(EPISODE_IDS))
        )


def assert_counted_once(report: dict) -> None:
    counted = report["imported"] + sum(report["skipped"].values()) + sum(report["rejected"].values())
    assert report["total_rows"] == counted, report


def test_an_import_waits_for_a_concurrent_insert_and_skips_those_rows(cleanup):
    result = {}

    with SessionLocal() as other_import:
        # Another import has inserted the first 5 ids (same values) but not committed:
        # our lookup cannot see them, so our INSERT has to wait on the unique index.
        other_import.add_all(
            Episode(
                episode_id=episode_id, robot_id="arm-01", task_name="pick cup", recorded_at=RECORDED_AT,
                duration_seconds=30, operator_name="Aline", quality="good",
            )
            for episode_id in EPISODE_IDS[:5]
        )
        other_import.flush()

        thread = threading.Thread(target=lambda: result.update(report=import_in_own_session(2000)), daemon=True)
        thread.start()
        thread.join(timeout=1)
        assert thread.is_alive(), "the import's INSERT should wait for the other transaction"

        other_import.commit()

    thread.join(timeout=30)
    report = result["report"]
    assert report["status"] == "finished"
    assert report["imported"] == 295
    assert report["skipped"] == {"duplicate_in_file": 0, "already_exists": 5, "conflict": 0}
    assert_counted_once(report)
    assert stored_count() == 300


@pytest.mark.parametrize("attempt", range(3))
def test_two_imports_of_the_same_file_at_the_same_moment(cleanup, attempt):
    start_together = threading.Barrier(2)
    reports, errors = [], []

    def run_import():
        start_together.wait()
        try:
            reports.append(import_in_own_session(chunk_size=10))  # many small chunks, so they interleave
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=run_import, daemon=True) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == []
    assert len(reports) == 2
    for report in reports:
        assert report["status"] == "finished"
        assert_counted_once(report)
        assert report["skipped"]["conflict"] == 0
        assert report["imported"] + report["skipped"]["already_exists"] == 300
        assert report["rejected"]["unknown_robot"] == 1
    assert sum(report["imported"] for report in reports) == 300  # every valid row inserted exactly once
    assert stored_count() == 300
