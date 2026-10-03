"""Import episodes from a CSV export. Used by POST /imports and by `python -m app.import_csv`.

Every rule is written down in docs/import-rules.md. In short: each row is
normalised, rejected or skipped, never guessed at; an existing episode is never
overwritten; importing the same file twice inserts nothing the second time.
Every data row ends up in exactly one bucket:

    total_rows == imported + sum(skipped) + sum(rejected)
"""

import codecs
import csv
import hashlib
import io
import re
import time
from datetime import datetime, timedelta, timezone
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import KNOWN_ROBOTS, MAX_DURATION_SECONDS
from app.models import Episode, ImportRun

REQUIRED_COLUMNS = (
    "episode_id",
    "robot_id",
    "task_name",
    "recorded_at",
    "duration_seconds",
    "operator_name",
    "quality",
)
MISSING_REASONS = {
    "episode_id": "missing_episode_id",
    "robot_id": "missing_robot",
    "task_name": "missing_task",
    "recorded_at": "missing_timestamp",
    "duration_seconds": "missing_duration",
    "operator_name": "missing_operator",
    "quality": "missing_quality",
}
REJECT_REASONS = (
    "malformed_row",
    *MISSING_REASONS.values(),
    "unknown_robot",
    "invalid_timestamp",
    "future_timestamp",
    "invalid_duration",
    "duration_too_long",
    "invalid_quality",
)
SKIP_REASONS = ("duplicate_in_file", "already_exists", "conflict")
NORMALISED_KINDS = (
    "episode_id",
    "robot_id",
    "task_name",
    "quality",
    "operator_name",
    "duration_seconds",
    "recorded_at",
    "date_assumed_day_first",
)
QUALITIES = ("good", "usable", "bad")

# Accepted timestamp formats, all read as UTC, with the normalisation kind
# counted when a row uses them (None: the normal format, nothing to count).
TIMESTAMP_FORMATS = (
    ("%Y-%m-%dT%H:%M:%S", None),
    ("%Y-%m-%dT%H:%M:%SZ", "recorded_at"),
    ("%Y-%m-%d %H:%M:%S", "recorded_at"),
    ("%Y-%m-%d", "recorded_at"),
    ("%d/%m/%Y %H:%M:%S", "date_assumed_day_first"),
    ("%d/%m/%Y %H:%M", "date_assumed_day_first"),
    ("%d/%m/%Y", "date_assumed_day_first"),
)

# The fields compared with an episode that is already in the database (all but the id).
COMPARED_FIELDS = ("robot_id", "task_name", "recorded_at", "duration_seconds", "operator_name", "quality")

CHUNK_SIZE = 2000
MAX_PROBLEMS = 200
READ_BLOCK_BYTES = 1024 * 1024


class ImportFileError(Exception):
    """The file as a whole is refused. status_code is the HTTP status the API answers with."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class RowRejected(Exception):
    """One row is rejected for `reason`; `value` is the offending input."""

    def __init__(self, reason: str, value: str | None):
        super().__init__(reason)
        self.reason = reason
        self.value = value


def import_episodes(
    db: Session, file: BinaryIO, file_name: str, started_by: int | None, chunk_size: int = CHUNK_SIZE
) -> tuple[int, dict]:
    """Import one CSV file. Returns (import_run_id, report). Raises ImportFileError for a refused file."""
    started = time.perf_counter()
    now = datetime.now(timezone.utc)

    # Everything that can refuse the whole file is checked before anything is saved.
    file_sha256 = _hash_and_check(file)
    # utf-8-sig: a UTF-8 byte order mark (BOM) at the start is accepted and dropped.
    reader = csv.reader(io.TextIOWrapper(file, encoding="utf-8-sig", newline=""), strict=True)
    try:
        header = next(reader, None)
    except csv.Error as error:
        raise ImportFileError(422, f"Unreadable CSV header: {error}") from None
    if header is None or not any(cell.strip() for cell in header):
        raise ImportFileError(422, "The file is empty")
    positions = _column_positions(header)

    previous_runs = list(
        db.scalars(select(ImportRun.id).where(ImportRun.file_sha256 == file_sha256).order_by(ImportRun.id))
    )
    run = ImportRun(file_name=file_name, file_sha256=file_sha256, started_by=started_by)
    db.add(run)
    db.commit()
    report = _empty_report(file_name, file_sha256, previous_runs)

    try:
        # episode_id -> (line of its first valid row, hash of that row's values).
        # A hash is enough to tell whether a later copy differs, and much smaller than the values.
        seen: dict[str, tuple[int, int]] = {}
        chunk: list[tuple[int, dict]] = []
        for cells in reader:
            line = reader.line_num
            if not any(cell.strip() for cell in cells):
                report["blank_lines"] += 1
                continue
            report["total_rows"] += 1

            try:
                values, kinds = _clean_row(cells, positions, len(header), now)
            except RowRejected as rejected:
                report["rejected"][rejected.reason] += 1
                _add_problem(report, line, _raw_episode_id(cells, positions), rejected.reason, value=rejected.value)
                continue
            for kind in kinds:
                report["normalised"][kind] += 1

            episode_id = values["episode_id"]
            fingerprint = hash(tuple(values[field] for field in COMPARED_FIELDS))
            if episode_id in seen:
                first_line, first_fingerprint = seen[episode_id]
                report["skipped"]["duplicate_in_file"] += 1
                if fingerprint != first_fingerprint:
                    _add_problem(report, line, episode_id, "conflict_in_file", value=f"differs from line {first_line}")
                continue
            seen[episode_id] = (line, fingerprint)

            chunk.append((line, values))
            if len(chunk) >= chunk_size:
                _save_chunk(db, run.id, chunk, report)
                chunk = []
        if chunk:
            _save_chunk(db, run.id, chunk, report)
    except Exception as error:
        # Chunks committed before the failure stay in the database. That is safe:
        # importing the same file again skips them as already_exists and finishes the job.
        db.rollback()
        report["status"] = "failed"
        report["error"] = f"{type(error).__name__}: {error}"
        _finish(db, run, report, started)
        if isinstance(error, csv.Error):
            # Broken CSV syntax (an unclosed quote, a NUL byte): the file's fault, not the server's.
            raise ImportFileError(422, f"Unreadable CSV at line {reader.line_num}: {error}") from error
        raise

    report["status"] = "finished"
    _finish(db, run, report, started)
    return run.id, report


def _hash_and_check(file: BinaryIO) -> str:
    """First pass: the file's SHA-256. Refuses an empty file or one that is not valid UTF-8.

    Reads 1 MB at a time, so a large file is never held in memory, and checks
    the whole file before anything is saved: a bad byte near the end cannot
    leave half an import behind.
    """
    sha256 = hashlib.sha256()
    utf8 = codecs.getincrementaldecoder("utf-8")()
    size = 0
    try:
        while block := file.read(READ_BLOCK_BYTES):
            sha256.update(block)
            utf8.decode(block)
            size += len(block)
        utf8.decode(b"", final=True)
    except UnicodeDecodeError:
        raise ImportFileError(422, "The file is not valid UTF-8 text") from None
    if size == 0:
        raise ImportFileError(422, "The file is empty")
    file.seek(0)
    return sha256.hexdigest()


def _column_positions(header: list[str]) -> dict[str, int]:
    """{column name: index}. Names are trimmed and case-insensitive; extra columns are ignored."""
    names = [name.strip().lower() for name in header]
    missing = [column for column in REQUIRED_COLUMNS if column not in names]
    if missing:
        raise ImportFileError(422, f"Missing required columns: {', '.join(missing)}")
    return {column: names.index(column) for column in REQUIRED_COLUMNS}


def _clean_row(cells: list[str], positions: dict[str, int], width: int, now: datetime) -> tuple[dict, list[str]]:
    """Validate and normalise one row. Returns (episode values, normalisation kinds). Raises RowRejected.

    The checks run in the order of docs/import-rules.md; the first problem found wins.
    """
    if len(cells) != width:
        raise RowRejected("malformed_row", f"{len(cells)} columns instead of {width}")
    raw = {column: cells[position] for column, position in positions.items()}
    for column in REQUIRED_COLUMNS:
        if not raw[column].strip():
            raise RowRejected(MISSING_REASONS[column], raw[column])

    robot_id = raw["robot_id"].strip().lower()
    if robot_id not in KNOWN_ROBOTS:
        raise RowRejected("unknown_robot", raw["robot_id"])
    recorded_at, timestamp_kind = _parse_timestamp(raw["recorded_at"], now)
    duration_seconds, duration_changed = _parse_duration(raw["duration_seconds"])
    quality = raw["quality"].strip().lower()
    if quality not in QUALITIES:
        raise RowRejected("invalid_quality", raw["quality"])

    values = {
        "episode_id": raw["episode_id"].strip().upper(),
        "robot_id": robot_id,
        "task_name": " ".join(raw["task_name"].split()).lower(),
        "recorded_at": recorded_at,
        "duration_seconds": duration_seconds,
        "operator_name": _clean_operator_name(raw["operator_name"]),
        "quality": quality,
    }
    kinds = [
        column
        for column in ("episode_id", "robot_id", "task_name", "quality", "operator_name")
        if values[column] != raw[column]
    ]
    if duration_changed:
        kinds.append("duration_seconds")
    if timestamp_kind:
        kinds.append(timestamp_kind)
    return values, kinds


def _parse_timestamp(value: str, now: datetime) -> tuple[datetime, str | None]:
    text = value.strip()
    for timestamp_format, kind in TIMESTAMP_FORMATS:
        try:
            parsed = datetime.strptime(text, timestamp_format).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if parsed > now + timedelta(days=1):
            raise RowRejected("future_timestamp", value)
        if kind is None and text != value:
            kind = "recorded_at"  # the normal format, but with spaces around it
        return parsed, kind
    raise RowRejected("invalid_timestamp", value)


def _parse_duration(value: str) -> tuple[int, bool]:
    """(seconds, whether the text had to be normalised). Whole numbers, or N.0 / N.00."""
    text = value.strip()
    match = re.fullmatch(r"(\d+)(\.0+)?", text)
    if match is None:
        raise RowRejected("invalid_duration", value)
    seconds = int(match.group(1))
    if seconds <= 0:
        raise RowRejected("invalid_duration", value)
    if seconds > MAX_DURATION_SECONDS:
        raise RowRejected("duration_too_long", value)
    return seconds, text != value or match.group(2) is not None


def _clean_operator_name(value: str) -> str:
    name = " ".join(value.split())
    # All lower or all upper case looks like a typing accident ("DIANE");
    # mixed case ("McDonald") was typed on purpose, so it is kept.
    if name.islower() or name.isupper():
        name = name.title()
    return name


def _raw_episode_id(cells: list[str], positions: dict[str, int]) -> str | None:
    """The id of a rejected row, for the report, if the row has one."""
    position = positions["episode_id"]
    if position < len(cells) and cells[position].strip():
        return cells[position].strip().upper()
    return None


def _save_chunk(db: Session, run_id: int, chunk: list[tuple[int, dict]], report: dict) -> None:
    """Insert the chunk's new episodes and commit. Existing episodes are skipped, never overwritten."""
    rows_by_id = {values["episode_id"]: (line, values) for line, values in chunk}

    # One query for the whole chunk: which of these ids exist already?
    existing = _existing_episodes(db, rows_by_id.keys())
    _skip_existing(existing, rows_by_id, report)
    new_rows = [values for episode_id, (_, values) in rows_by_id.items() if episode_id not in existing]
    # Sorted, so every import locks new ids in the same order: two imports of
    # overlapping files wait for each other instead of deadlocking.
    new_rows.sort(key=lambda values: values["episode_id"])

    if new_rows:
        # ON CONFLICT DO NOTHING: if another import inserted one of these ids
        # after our lookup, Postgres skips it instead of failing the whole chunk.
        # The rows are passed as a list of parameters: SQLAlchemy reuses one
        # compiled statement and sends them in multi-row batches (twice as fast
        # as building a 2000-row VALUES statement for every chunk).
        statement = (
            insert(Episode).on_conflict_do_nothing(index_elements=["episode_id"]).returning(Episode.episode_id)
        )
        inserted = set(db.scalars(statement, [{**values, "import_run_id": run_id} for values in new_rows]))
        report["imported"] += len(inserted)  # what was really inserted, not what was attempted
        lost_to_another_import = {values["episode_id"] for values in new_rows} - inserted
        if lost_to_another_import:
            _skip_existing(_existing_episodes(db, lost_to_another_import), rows_by_id, report)

    db.commit()


def _existing_episodes(db: Session, episode_ids) -> dict[str, Episode]:
    query = select(Episode).where(Episode.episode_id.in_(list(episode_ids)))
    return {episode.episode_id: episode for episode in db.scalars(query)}


def _skip_existing(existing: dict[str, Episode], rows_by_id: dict, report: dict) -> None:
    """Count rows whose id is already stored: already_exists if identical, conflict if not."""
    for episode_id, episode in existing.items():
        line, values = rows_by_id[episode_id]
        differences = {
            field: {"file": _for_json(values[field]), "database": _for_json(_stored_value(episode, field))}
            for field in COMPARED_FIELDS
            if values[field] != _stored_value(episode, field)
        }
        if differences:
            report["skipped"]["conflict"] += 1
            _add_problem(report, line, episode_id, "conflict", fields=differences)
        else:
            report["skipped"]["already_exists"] += 1


def _stored_value(episode: Episode, field: str):
    """A stored value, ready to compare with a parsed one.

    Timestamps come back in the database session's time zone. They are converted
    to UTC, like the parsed ones, because Python never treats a local time in a
    repeated hour (when daylight saving ends) as equal to a time in another zone.
    """
    value = getattr(episode, field)
    return value.astimezone(timezone.utc) if isinstance(value, datetime) else value


def _for_json(value):
    return value.isoformat() if isinstance(value, datetime) else value


def _empty_report(file_name: str, file_sha256: str, previous_runs: list[int]) -> dict:
    return {
        "status": "running",
        "file_name": file_name,
        "file_sha256": file_sha256,
        "total_rows": 0,
        "imported": 0,
        "skipped": dict.fromkeys(SKIP_REASONS, 0),
        "rejected": dict.fromkeys(REJECT_REASONS, 0),
        "normalised": dict.fromkeys(NORMALISED_KINDS, 0),
        "blank_lines": 0,
        "problems": [],
        "problems_truncated": False,
        "previous_runs_with_same_file": previous_runs,
        "duration_ms": 0,
    }


def _add_problem(report: dict, line: int, episode_id: str | None, reason: str, value=None, fields=None) -> None:
    """Keep the first MAX_PROBLEMS problems; the counts always include all of them."""
    if len(report["problems"]) >= MAX_PROBLEMS:
        report["problems_truncated"] = True
        return
    problem = {"line": line, "episode_id": episode_id, "reason": reason}
    if value is not None:
        problem["value"] = value
    if fields is not None:
        problem["fields"] = fields
    report["problems"].append(problem)


def _finish(db: Session, run: ImportRun, report: dict, started: float) -> None:
    report["duration_ms"] = round((time.perf_counter() - started) * 1000)
    run.finished_at = datetime.now(timezone.utc)
    run.report = report
    db.commit()
