"""Dataset requests and their status workflow.

Clients only ever see their own requests. For another client's request they get
404, exactly like a missing id, so they cannot even learn that it exists.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import require_roles
from app.db import get_db
from app.models import Assignment, Request, RequestStatusHistory, User
from app.schemas import RequestCreate, RequestDetailOut, RequestOut
from app.workflow import INITIAL_STATUS, Status, available_transitions

router = APIRouter(prefix="/requests", tags=["requests"])

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


@router.post("", response_model=RequestDetailOut, status_code=201)
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


@router.get("", response_model=list[RequestOut])
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


@router.get("/{request_id}", response_model=RequestDetailOut)
def get_request(
    request_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_roles("client", "operator", "admin")),
):
    return _request_detail(db, request_id, user)
