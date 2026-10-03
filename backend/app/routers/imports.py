"""Uploading an episodes CSV export, and reading the reports of past imports."""

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api_docs import NOT_LOGGED_IN, error_responses
from app.auth import require_roles
from app.config import MAX_IMPORT_FILE_BYTES
from app.db import get_db
from app.importer import ImportFileError, import_episodes
from app.models import ImportRun, User
from app.schemas import ImportResultOut, ImportRunOut, ImportRunPage

router = APIRouter(prefix="/imports", tags=["imports"])

OPERATORS_ONLY = "Clients cannot import episodes or read import reports."


@router.post(
    "",
    response_model=ImportResultOut,
    status_code=201,
    summary="Import an episodes CSV",
    description=(
        "**Roles:** operator, admin. Upload the recording system's CSV export as the multipart field `file`. "
        "Rows are normalised, rejected or skipped as described in `docs/import-rules.md`; existing episodes "
        "are never overwritten, so uploading the same file again is safe and imports nothing new. "
        "The report says what was imported, what was skipped and rejected, and why.\n\n"
        "**422**: the file is empty, not UTF-8, misses a required column (listed), or has broken CSV syntax."
    ),
    responses=error_responses({401: NOT_LOGGED_IN, 403: OPERATORS_ONLY, 413: "The file is larger than 20 MB."}),
)
def create_import(
    file: UploadFile = File(description="The CSV export, at most 20 MB."),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("operator", "admin")),
):
    # The upload is already received at this point; the frontend's nginx also
    # stops bodies over 20 MB before they reach the API.
    if file.size is not None and file.size > MAX_IMPORT_FILE_BYTES:
        raise HTTPException(status_code=413, detail="The file is larger than 20 MB")
    try:
        run_id, report = import_episodes(db, file.file, file_name=file.filename or "upload.csv", started_by=user.id)
    except ImportFileError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from None
    return {"import_run_id": run_id, "report": report}


@router.get(
    "",
    response_model=ImportRunPage,
    summary="List imports",
    description=(
        "**Roles:** operator, admin. Past imports, newest first, with their headline counts. "
        "`status` is `running` while an import has not finished yet.\n\n"
        "**422**: `limit` outside 1 to 200, or a negative `offset`."
    ),
    responses=error_responses({401: NOT_LOGGED_IN, 403: OPERATORS_ONLY}),
)
def list_imports(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles("operator", "admin")),
):
    total = db.scalar(select(func.count()).select_from(ImportRun))
    rows = db.execute(
        select(ImportRun, User.name)
        .outerjoin(User, User.id == ImportRun.started_by)
        .order_by(ImportRun.started_at.desc(), ImportRun.id.desc())
        .limit(limit)
        .offset(offset)
    )
    items = [_summary(run, started_by_name) for run, started_by_name in rows]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.get(
    "/{import_run_id}",
    response_model=ImportRunOut,
    summary="Get an import's report",
    description=(
        "**Roles:** operator, admin. The full stored report, including the problems found (the first 200).\n\n"
        "**422**: `import_run_id` is not a whole number."
    ),
    responses=error_responses({401: NOT_LOGGED_IN, 403: OPERATORS_ONLY, 404: "No import with this id."}),
)
def get_import(
    import_run_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles("operator", "admin")),
):
    row = db.execute(
        select(ImportRun, User.name)
        .outerjoin(User, User.id == ImportRun.started_by)
        .where(ImportRun.id == import_run_id)
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Import not found")
    run, started_by_name = row
    return {
        "id": run.id,
        "file_name": run.file_name,
        "file_sha256": run.file_sha256,
        "started_by_name": started_by_name,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "report": run.report,
    }


def _summary(run: ImportRun, started_by_name: str | None) -> dict:
    """One line of the import list: the headline counts from the stored report."""
    report = run.report
    return {
        "id": run.id,
        "file_name": run.file_name,
        "started_by_name": started_by_name,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "status": report["status"] if report else "running",
        "total_rows": report["total_rows"] if report else None,
        "imported": report["imported"] if report else None,
        "skipped": sum(report["skipped"].values()) if report else None,
        "rejected": sum(report["rejected"].values()) if report else None,
    }
