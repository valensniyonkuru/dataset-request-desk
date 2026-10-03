"""Browsing recorded episodes, to find ones to assign to a request."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api_docs import NOT_LOGGED_IN, error_responses
from app.auth import require_roles
from app.db import get_db
from app.models import Assignment, Episode, User
from app.schemas import EpisodePage, Quality

router = APIRouter(prefix="/episodes", tags=["episodes"])

# Every episode column, in the order of the output models. Shared with the assignments router.
EPISODE_COLUMNS = (
    Episode.episode_id,
    Episode.robot_id,
    Episode.task_name,
    Episode.recorded_at,
    Episode.duration_seconds,
    Episode.operator_name,
    Episode.quality,
    Episode.import_run_id,
    Episode.created_at,
)


@router.get(
    "",
    response_model=EpisodePage,
    summary="List episodes",
    description=(
        "**Roles:** operator, admin. Filter by `task_name`, `quality`, `robot_id` (exact matches) and "
        "`assigned` (true: only assigned, false: only unassigned). `quality` can be given more than once "
        "for any of several values, e.g. `quality=good&quality=usable` for everything that can be assigned; "
        "without it, every quality is listed. Newest recording first. "
        "`total` counts every matching episode, not just this page.\n\n"
        "**422**: unknown `quality`, `assigned` not true/false, `limit` outside 1 to 200, or a negative `offset`."
    ),
    responses=error_responses({401: NOT_LOGGED_IN, 403: "Clients cannot browse episodes."}),
)
def list_episodes(
    task_name: str | None = None,
    # A list, so the parameter can be repeated: ?quality=good&quality=usable.
    quality: list[Quality] = Query(
        default=[],
        description="Repeat for several qualities. Leave out for all.",
        openapi_examples={
            "good or usable": {"summary": "Everything that can be assigned", "value": ["good", "usable"]},
            "only good": {"summary": "One quality", "value": ["good"]},
        },
    ),
    robot_id: str | None = None,
    assigned: bool | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles("operator", "admin")),
):
    # One query: LEFT JOIN, so unassigned episodes are kept with a null request id.
    query = select(*EPISODE_COLUMNS, Assignment.request_id.label("assigned_request_id")).outerjoin(
        Assignment, Assignment.episode_id == Episode.episode_id
    )
    if task_name is not None:
        query = query.where(Episode.task_name == task_name)
    if quality:  # an empty list means no filter
        query = query.where(Episode.quality.in_(quality))
    if robot_id is not None:
        query = query.where(Episode.robot_id == robot_id)
    if assigned is True:
        query = query.where(Assignment.request_id.is_not(None))
    elif assigned is False:
        query = query.where(Assignment.request_id.is_(None))

    total = db.scalar(select(func.count()).select_from(query.subquery()))
    page = query.order_by(Episode.recorded_at.desc(), Episode.episode_id).limit(limit).offset(offset)
    items = [dict(row) for row in db.execute(page).mappings()]
    return {"items": items, "total": total, "limit": limit, "offset": offset}
