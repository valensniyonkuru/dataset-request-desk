from contextlib import nullcontext
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app import import_csv
from app.config import MAX_IMPORT_FILE_BYTES, settings
from app.models import ImportRun

HEADER = "episode_id,robot_id,task_name,recorded_at,duration_seconds,operator_name,quality\n"
SMALL_CSV = (
    HEADER
    + "EP-1,arm-01,pick cup,2026-09-01T10:00:00,30,Aline,good\n"
    + "EP-1,arm-01,pick cup,2026-09-01T10:00:00,30,Aline,good\n"
    + "EP-2,arm-99,pick cup,2026-09-01T10:00:00,30,Aline,good\n"
).encode()


def upload(client, headers, content: bytes, file_name: str = "episodes.csv"):
    return client.post("/imports", files={"file": (file_name, content, "text/csv")}, headers=headers)


def run_count(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(ImportRun))


@pytest.fixture
def operator(create_user):
    return create_user("ops@example.com", role="operator")


# --- uploading ---


@pytest.mark.parametrize("role", ["operator", "admin"])
def test_operators_and_admins_can_import(client, create_user, auth_headers, role):
    user = create_user(f"{role}@example.com", role=role)

    response = upload(client, auth_headers(user), SMALL_CSV)

    assert response.status_code == 201
    report = response.json()["report"]
    assert report["status"] == "finished"
    assert (report["total_rows"], report["imported"]) == (3, 1)
    assert report["skipped"]["duplicate_in_file"] == 1
    assert report["rejected"]["unknown_robot"] == 1
    assert report["file_name"] == "episodes.csv"


def test_the_stored_report_can_be_read_back(client, operator, auth_headers):
    created = upload(client, auth_headers(operator), SMALL_CSV).json()

    response = client.get(f"/imports/{created['import_run_id']}", headers=auth_headers(operator))

    assert response.status_code == 200
    run = response.json()
    assert run["report"] == created["report"]
    assert run["started_by_name"] == operator.name
    assert run["finished_at"] is not None
    assert len(run["file_sha256"]) == 64


def test_uploading_the_same_file_again_imports_nothing_and_names_the_earlier_run(client, operator, auth_headers):
    first = upload(client, auth_headers(operator), SMALL_CSV).json()

    second = upload(client, auth_headers(operator), SMALL_CSV).json()["report"]

    assert second["imported"] == 0
    assert second["skipped"]["already_exists"] == 1
    assert second["previous_runs_with_same_file"] == [first["import_run_id"]]


def test_the_list_shows_headline_counts_newest_first_and_is_paginated(client, operator, auth_headers):
    first = upload(client, auth_headers(operator), SMALL_CSV, file_name="first.csv").json()
    second = upload(client, auth_headers(operator), SMALL_CSV, file_name="second.csv").json()
    headers = auth_headers(operator)

    page = client.get("/imports", headers=headers).json()

    assert page["total"] == 2
    assert [item["id"] for item in page["items"]] == [second["import_run_id"], first["import_run_id"]]
    newest = page["items"][0]
    assert newest["file_name"] == "second.csv"
    assert newest["started_by_name"] == operator.name
    assert newest["status"] == "finished"
    assert (newest["total_rows"], newest["imported"], newest["skipped"], newest["rejected"]) == (3, 0, 2, 1)
    second_page = client.get("/imports", params={"limit": 1, "offset": 1}, headers=headers).json()
    assert [item["id"] for item in second_page["items"]] == [first["import_run_id"]]


def test_a_file_over_20_mb_gives_413_and_saves_nothing(client, operator, auth_headers, db_session):
    too_big = SMALL_CSV + b"#" * (MAX_IMPORT_FILE_BYTES - len(SMALL_CSV) + 1)

    response = upload(client, auth_headers(operator), too_big)

    assert response.status_code == 413
    assert run_count(db_session) == 0


@pytest.mark.parametrize(
    "content, message",
    [
        (b"", "The file is empty"),
        (b"\xff\xfe not utf-8", "not valid UTF-8"),
        (b"episode_id,quality\nEP-1,good\n", "Missing required columns: robot_id, task_name"),
    ],
    ids=["empty", "not-utf8", "missing-columns"],
)
def test_a_refused_file_gives_422_and_saves_nothing(client, operator, auth_headers, db_session, content, message):
    response = upload(client, auth_headers(operator), content)

    assert response.status_code == 422
    assert message in response.json()["detail"]
    assert run_count(db_session) == 0


def test_a_request_without_a_file_gives_422(client, operator, auth_headers):
    response = client.post("/imports", headers=auth_headers(operator))

    assert response.status_code == 422


def test_an_unknown_import_gives_404(client, operator, auth_headers):
    assert client.get("/imports/999999", headers=auth_headers(operator)).status_code == 404


# --- who may use the import endpoints ---


def test_clients_get_403_on_every_import_endpoint(client, create_user, operator, auth_headers):
    run_id = upload(client, auth_headers(operator), SMALL_CSV).json()["import_run_id"]
    headers = auth_headers(create_user("client-a@example.com"))

    assert upload(client, headers, SMALL_CSV).status_code == 403
    assert client.get("/imports", headers=headers).status_code == 403
    assert client.get(f"/imports/{run_id}", headers=headers).status_code == 403


def test_every_import_endpoint_requires_login(client, operator, auth_headers):
    run_id = upload(client, auth_headers(operator), SMALL_CSV).json()["import_run_id"]

    assert client.post("/imports", files={"file": ("x.csv", SMALL_CSV, "text/csv")}).status_code == 401
    assert client.get("/imports").status_code == 401
    assert client.get(f"/imports/{run_id}").status_code == 401


# --- the command line ---


@pytest.fixture
def cli_uses_test_session(db_session, monkeypatch):
    """Make `python -m app.import_csv` use the test's rolled-back session."""
    monkeypatch.setattr(import_csv, "SessionLocal", lambda: nullcontext(db_session))


def test_the_cli_imports_the_seed_file_and_prints_the_counts(cli_uses_test_session, db_session, capsys):
    exit_code = import_csv.main(["import_csv", str(Path(settings.SEED_DIR) / "episodes.csv")])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "rows: 189, imported: 171, blank lines: 2" in output
    assert "line 185: malformed_row EP-90001 5 columns instead of 7" in output
    assert db_session.scalars(select(ImportRun)).one().started_by is None


def test_the_cli_reports_a_refused_file(cli_uses_test_session, tmp_path, capsys):
    bad_file = tmp_path / "bad.csv"
    bad_file.write_text("episode_id,quality\nEP-1,good\n")

    exit_code = import_csv.main(["import_csv", str(bad_file)])

    assert exit_code == 1
    assert "Missing required columns" in capsys.readouterr().err


def test_the_cli_needs_exactly_one_path(capsys):
    assert import_csv.main(["import_csv"]) == 2
    assert "Usage" in capsys.readouterr().err
