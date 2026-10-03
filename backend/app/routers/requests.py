"""Dataset requests and their status workflow.

Clients only ever see their own requests. For another client's request they get
404, exactly like a missing id, so they cannot even learn that it exists.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api_docs import NOT_LOGGED_IN, error_responses
from app.auth import require_roles
from app.db import get_db
from app.models import Assignment, Request, RequestStatusHistory, User
from app.schemas import RequestCreate, RequestDetailOut, RequestOut, TransitionIn
from app.workflow import INITIAL_STATUS, TRANSITIONS, Status, available_transitions

router = APIRouter(prefix="/requests", tags=["requests"])

REQUEST_NOT_FOUND = "No request with this id, or (for a client) a request that belongs to another client."

# Number of assigned episodes per request, counted by the database in one grouped query.
assigned_counts = (
    select(Assignment.request_id, func.count().label("assigned_count"))
    .group_by(Assignment.request_id)
    .subquery()
)


def _requests_query():
    """One SQL query for requests with their client's name and assigned episode count."""
    return (
        select(
            Request.id,
            Request.client_id,
            User.name.label("client_name"),
            Request.task_name,
            Request.episodes_requested,
            Request.deadline,
            Request.notes,
            Request.status,
            # Requests without any assignment have no row in the subquery.
            func.coalesce(assigned_counts.c.assigned_count, 0).label("assigned_count"),
            Request.created_at,
            Request.updated_at,
        )
        .join(User, User.id == Request.client_id)
        .outerjoin(assigned_counts, assigned_counts.c.request_id == Request.id)
    )


def _request_detail(db: Session, request_id: int, user: User) -> dict:
    """The request with its history and the status changes this user may make. 404 if not visible."""
    query = _requests_query().where(Request.id == request_id)
    if user.role == "client":
        query = query.where(Request.client_id == user.id)
    row = db.execute(query).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Request not found")

    history = db.execute(
        select(
            RequestStatusHistory.from_status,
            RequestStatusHistory.to_status,
            User.name.label("changed_by_name"),
            RequestStatusHistory.changed_at,
        )
        .join(User, User.id == RequestStatusHistory.changed_by)
        .where(RequestStatusHistory.request_id == request_id)
        # id breaks ties: rows written in the same transaction share the same now().
        .order_by(RequestStatusHistory.changed_at, RequestStatusHistory.id)
    ).mappings()

    return {
        **row,
        "history": [dict(entry) for entry in history],
        "available_transitions": available_transitions(row["status"], user.role),
    }


@router.post(
    "",
    response_model=RequestDetailOut,
    status_code=201,
    summary="Create a request",
    description=(
        "**Roles:** client. The request belongs to the logged-in client and starts as `submitted`.\n\n"
        "**422**: task name empty or over 100 characters, `episodes_requested` not a whole number from 1 "
        "to 100000, a deadline in the past, notes over 2000 characters, or an unknown field "
        "(such as `client_id` or `status`)."
    ),
    responses=error_responses({401: NOT_LOGGED_IN, 403: "Only clients create requests."}),
)
def create_request(
    body: RequestCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("client")),
):
    dataset_request = Request(
        client_id=user.id,  # always the logged-in client, never taken from the body
        task_name=body.task_name,
        episodes_requested=body.episodes_requested,
        deadline=body.deadline,
        notes=body.notes,
        status=INITIAL_STATUS,
    )
    db.add(dataset_request)
    db.flush()  # sends the INSERT, so dataset_request.id is known for the history row
    db.add(
        RequestStatusHistory(
            request_id=dataset_request.id, from_status=None, to_status=INITIAL_STATUS, changed_by=user.id
        )
    )
    db.commit()  # the request and its first history row are saved together, or not at all
    return _request_detail(db, dataset_request.id, user)


@router.get(
    "",
    response_model=list[RequestOut],
    summary="List requests",
    description=(
        "**Roles:** client, operator, admin. Clients see only their own requests (and `client_id` is "
        "ignored for them); operators and admins see all. Newest first.\n\n"
        "**422**: unknown `status`, `limit` outside 1 to 200, or a negative `offset`."
    ),
    responses=error_responses({401: NOT_LOGGED_IN}),
)
def list_requests(
    status: Status | None = None,
    task_name: str | None = None,
    client_id: int | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("client", "operator", "admin")),
):
    query = _requests_query()
    if user.role == "client":
        query = query.where(Request.client_id == user.id)  # the client_id filter is ignored for clients
    elif client_id is not None:
        query = query.where(Request.client_id == client_id)
    if status is not None:
        query = query.where(Request.status == status)
    if task_name is not None:
        query = query.where(Request.task_name == task_name)

    # Newest first. id breaks ties, so pages never overlap or skip a row.
    query = query.order_by(Request.created_at.desc(), Request.id.desc()).limit(limit).offset(offset)
    return [dict(row) for row in db.execute(query).mappings()]


@router.get(
    "/{request_id}",
    response_model=RequestDetailOut,
    summary="Get one request",
    description=(
        "**Roles:** client (own requests only), operator, admin. Includes the status history, oldest first, "
        "and `available_transitions`: the status changes the current user may make.\n\n"
        "**422**: `request_id` is not a whole number."
    ),
    responses=error_responses({401: NOT_LOGGED_IN, 404: REQUEST_NOT_FOUND}),
)
def get_request(
    request_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("client", "operator", "admin")),
):
    return _request_detail(db, request_id, user)


@router.post(
    "/{request_id}/transition",
    response_model=RequestDetailOut,
    summary="Change a request's status",
    description=(
        "**Roles:** client, operator, admin, each only for the steps they own. Operators and admins move "
        "a request to `in_progress` and `delivered`; the owning client accepts or rejects a delivery. "
        "Every change is recorded in the history.\n\n"
        "**422**: `to_status` is not one of `submitted`, `in_progress`, `delivered`, `accepted`, `rejected`."
    ),
    responses=error_responses(
        {
            401: NOT_LOGGED_IN,
            403: "The change exists, but your role may not make it.",
            404: REQUEST_NOT_FOUND,
            409: "Not a valid change from the current status, or fewer episodes assigned than requested.",
        }
    ),
)
def transition_request(
    request_id: int,
    body: TransitionIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("client", "operator", "admin")),
):
    """The only code path that changes a request's status."""
    # FOR UPDATE locks the row until this transaction ends. A second transition
    # on the same request (e.g. a double click on accept and reject) waits here,
    # then reads the new status and fails the checks below.
    query = select(Request).where(Request.id == request_id).with_for_update()
    if user.role == "client":
        query = query.where(Request.client_id == user.id)
    dataset_request = db.scalar(query)

    # The checks run in this order, so a client never learns about another client's request.
    if dataset_request is None:
        raise HTTPException(status_code=404, detail="Request not found")

    from_status = dataset_request.status
    allowed_roles = TRANSITIONS.get((from_status, body.to_status))
    if allowed_roles is None:
        raise HTTPException(status_code=409, detail=f"Cannot move from {from_status} to {body.to_status}")
    if user.role not in allowed_roles:
        raise HTTPException(status_code=403, detail="Your role cannot make this status change")

    if body.to_status == "delivered":
        assigned = db.scalar(
            select(func.count()).select_from(Assignment).where(Assignment.request_id == dataset_request.id)
        )
        if assigned < dataset_request.episodes_requested:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot deliver: {assigned} of {dataset_request.episodes_requested} episodes assigned",
            )

    dataset_request.status = body.to_status  # updated_at is refreshed by the model's onupdate
    db.add(
        RequestStatusHistory(
            request_id=dataset_request.id, from_status=from_status, to_status=body.to_status, changed_by=user.id
        )
    )
    db.commit()  # the status change and its history row are saved together, or not at all
    return _request_detail(db, dataset_request.id, user)
