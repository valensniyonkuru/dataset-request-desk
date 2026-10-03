"""Assigning episodes to a request, removing them, and listing a request's episodes.

Assign, unassign and the in_progress -> delivered transition all lock the same
request row first (SELECT ... FOR UPDATE). So, for one request, they run one
after another, never interleaved, and the delivery gate ("at least
episodes_requested episodes assigned") cannot be bypassed.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api_docs import NOT_LOGGED_IN, error_responses
from app.auth import require_roles
from app.db import get_db
from app.models import Assignment, Episode, Request, User
from app.routers.episodes import EPISODE_COLUMNS
from app.schemas import AssignedEpisodePage, AssignIn, AssignOut
from app.workflow import ASSIGNMENTS_OPEN_STATUS

router = APIRouter(prefix="/requests", tags=["assignments"])

# From the brief: only good or usable episodes can be assigned.
ASSIGNABLE_QUALITIES = ("good", "usable")

NOT_OPEN = f"Episodes can only be assigned while the request is {ASSIGNMENTS_OPEN_STATUS}"
REQUEST_NOT_FOUND = "No request with this id, or (for a client) a request that belongs to another client."


def _lock_open_request(db: Session, request_id: int) -> Request:
    """Lock the request row until the transaction ends. 404 if missing, 409 if not open for assignments."""
    dataset_request = db.scalar(select(Request).where(Request.id == request_id).with_for_update())
    if dataset_request is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if dataset_request.status != ASSIGNMENTS_OPEN_STATUS:
        raise HTTPException(status_code=409, detail=NOT_OPEN)
    return dataset_request


def _already_assigned(db: Session, episode_ids: list[str]) -> list[str]:
    """'EP-1 (request 7)' for each of these episodes that is assigned to any request."""
    rows = db.execute(
        select(Assignment.episode_id, Assignment.request_id)
        .where(Assignment.episode_id.in_(episode_ids))
        .order_by(Assignment.episode_id)
    )
    return [f"{episode_id} (request {request_id})" for episode_id, request_id in rows]


def _assigned_count(db: Session, request_id: int) -> int:
    return db.scalar(select(func.count()).select_from(Assignment).where(Assignment.request_id == request_id))


@router.post(
    "/{request_id}/assignments",
    response_model=AssignOut,
    status_code=201,
    summary="Assign episodes to a request",
    description=(
        "**Roles:** operator, admin. All or nothing: if any episode fails a check, none is assigned. "
        "The request must be `in_progress`; every episode must exist, be `good` or `usable`, and not be "
        "assigned yet; and the request may not end up with more than `episodes_requested` episodes.\n\n"
        "**422**: unknown episode ids (listed), an empty list, more than 500 ids, or the same id twice."
    ),
    responses=error_responses(
        {
            401: NOT_LOGGED_IN,
            403: "Clients cannot assign episodes.",
            404: "No request with this id.",
            409: "The request is not in_progress, an episode is bad quality or already assigned (the ids and "
            "the holding request are listed), or the request would get more than episodes_requested.",
        }
    ),
)
def assign_episodes(
    request_id: int,
    body: AssignIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("operator", "admin")),
):
    dataset_request = _lock_open_request(db, request_id)

    episodes = {
        episode_id: quality
        for episode_id, quality in db.execute(
            select(Episode.episode_id, Episode.quality).where(Episode.episode_id.in_(body.episode_ids))
        )
    }
    unknown = [episode_id for episode_id in body.episode_ids if episode_id not in episodes]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown episode ids: {', '.join(unknown)}")

    wrong_quality = [
        f"{episode_id} ({episodes[episode_id]})"
        for episode_id in body.episode_ids
        if episodes[episode_id] not in ASSIGNABLE_QUALITIES
    ]
    if wrong_quality:
        raise HTTPException(
            status_code=409, detail=f"Only good or usable episodes can be assigned: {', '.join(wrong_quality)}"
        )

    taken = _already_assigned(db, body.episode_ids)
    if taken:
        raise HTTPException(status_code=409, detail=f"Already assigned: {', '.join(taken)}")

    new_total = _assigned_count(db, dataset_request.id) + len(body.episode_ids)
    if new_total > dataset_request.episodes_requested:
        raise HTTPException(
            status_code=409,
            detail=f"Too many episodes: would be {new_total} of {dataset_request.episodes_requested}",
        )

    db.add_all(
        Assignment(request_id=dataset_request.id, episode_id=episode_id, assigned_by=user.id)
        for episode_id in body.episode_ids
    )
    try:
        db.commit()
    except IntegrityError as error:
        # Another transaction assigned one of these episodes (to another request)
        # after our check above and committed first: the UNIQUE constraint on
        # assignments.episode_id stopped the second assignment. Nothing from this
        # batch is saved. Answer like the check above, never with a 500.
        db.rollback()
        if "uq_assignments_episode_id" not in str(error.orig):
            raise
        taken = _already_assigned(db, body.episode_ids)
        raise HTTPException(status_code=409, detail=f"Already assigned: {', '.join(taken)}") from None

    return {
        "request_id": dataset_request.id,
        "assigned_count": _assigned_count(db, dataset_request.id),
        "assigned_episode_ids": body.episode_ids,
    }


@router.delete(
    "/{request_id}/assignments/{episode_id}",
    status_code=204,
    summary="Remove an episode from a request",
    description=(
        "**Roles:** operator, admin. Only while the request is `in_progress`. The episode becomes "
        "available for other requests.\n\n**422**: `request_id` is not a whole number."
    ),
    responses=error_responses(
        {
            401: NOT_LOGGED_IN,
            403: "Clients cannot remove episodes.",
            404: "No request with this id, or this episode is not assigned to this request.",
            409: "The request is not in_progress.",
        }
    ),
)
def unassign_episode(
    request_id: int,
    episode_id: str,
    db: Session = Depends(get_db),
    _user: User = Depends(require_roles("operator", "admin")),
) -> Response:
    # Same lock and status rule as assigning. Without them, an unassign could run
    # at the same moment as the in_progress -> delivered check, after it counted
    # the episodes, and leave a delivered request with too few episodes.
    _lock_open_request(db, request_id)

    assignment = db.scalar(
        select(Assignment).where(Assignment.request_id == request_id, Assignment.episode_id == episode_id)
    )
    if assignment is None:
        raise HTTPException(status_code=404, detail="This episode is not assigned to this request")

    db.delete(assignment)
    db.commit()
    return Response(status_code=204)


@router.get(
    "/{request_id}/episodes",
    response_model=AssignedEpisodePage,
    summary="List a request's episodes",
    description=(
        "**Roles:** client (own requests only), operator, admin. The episodes assigned to the request, "
        "in the order they were assigned.\n\n"
        "**422**: `request_id` is not a whole number, `limit` outside 1 to 200, or a negative `offset`."
    ),
    responses=error_responses({401: NOT_LOGGED_IN, 404: REQUEST_NOT_FOUND}),
)
def list_request_episodes(
    request_id: int,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("client", "operator", "admin")),
):
    visible = select(Request.id).where(Request.id == request_id)
    if user.role == "client":
        visible = visible.where(Request.client_id == user.id)  # 404, not 403, for another client's request
    if db.scalar(visible) is None:
        raise HTTPException(status_code=404, detail="Request not found")

    query = (
        select(*EPISODE_COLUMNS, Assignment.assigned_at)
        .join(Assignment, Assignment.episode_id == Episode.episode_id)
        .where(Assignment.request_id == request_id)
    )
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    page = query.order_by(Assignment.assigned_at, Episode.episode_id).limit(limit).offset(offset)
    items = [dict(row) for row in db.execute(page).mappings()]
    return {"items": items, "total": total, "limit": limit, "offset": offset}
