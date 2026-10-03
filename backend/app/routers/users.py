"""User management. Admin only. There is no DELETE: users are deactivated instead."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api_docs import NOT_LOGGED_IN, error_responses
from app.auth import require_roles
from app.db import get_db
from app.models import User
from app.schemas import UserAdminOut, UserCreate, UserUpdate
from app.security import hash_password

router = APIRouter(prefix="/users", tags=["users"])


@router.get(
    "",
    response_model=list[UserAdminOut],
    summary="List users",
    description="**Roles:** admin. All users, active and deactivated, ordered by id.",
    responses=error_responses({401: NOT_LOGGED_IN, 403: "Logged in, but not an admin."}),
)
def list_users(db: Session = Depends(get_db), _admin: User = Depends(require_roles("admin"))):
    return db.scalars(select(User).order_by(User.id)).all()


@router.post(
    "",
    response_model=UserAdminOut,
    status_code=201,
    summary="Create a user",
    description=(
        "**Roles:** admin. The email is stored lowercase.\n\n"
        "**422**: invalid email, unknown role, password under 8 characters, a client without an "
        "organisation, or an unknown field."
    ),
    responses=error_responses(
        {401: NOT_LOGGED_IN, 403: "Logged in, but not an admin.", 409: "A user with this email already exists."}
    ),
)
def create_user(body: UserCreate, db: Session = Depends(get_db), _admin: User = Depends(require_roles("admin"))):
    user = User(
        email=body.email,
        name=body.name,
        role=body.role,
        organisation=body.organisation,
        password_hash=hash_password(body.password),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        # The UNIQUE constraint is the real duplicate check: it also covers
        # two admins creating the same email at the same moment.
        db.rollback()
        if "uq_users_email" in str(exc.orig):
            raise HTTPException(status_code=409, detail="A user with this email already exists") from None
        raise
    return user


@router.patch(
    "/{user_id}",
    response_model=UserAdminOut,
    summary="Update a user",
    description=(
        "**Roles:** admin. Partial update: only the fields sent are changed. Set `is_active` to false to "
        "deactivate a user (there is no delete).\n\n"
        "**422**: invalid values, `null` for a field other than `organisation`, or a client left without "
        "an organisation."
    ),
    responses=error_responses(
        {
            401: NOT_LOGGED_IN,
            403: "Logged in, but not an admin.",
            404: "No user with this id.",
            409: "You cannot demote or deactivate yourself, and the last active admin cannot be "
            "demoted or deactivated.",
        }
    ),
)
def update_user(
    user_id: int,
    body: UserUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_roles("admin")),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    changes = body.model_dump(exclude_unset=True)

    # Validate everything before changing anything.
    if changes.get("role", user.role) == "client" and not changes.get("organisation", user.organisation):
        raise HTTPException(status_code=422, detail="organisation is required for clients")

    if _removes_an_active_admin(user, changes):
        # Lock the active admin rows until this transaction ends, so two admins
        # demoting each other at the same moment cannot leave zero admins.
        active_admin_ids = db.scalars(
            select(User.id).where(User.role == "admin", User.is_active.is_(True)).order_by(User.id).with_for_update()
        ).all()
        if len(active_admin_ids) <= 1:
            raise HTTPException(status_code=409, detail="The last active admin cannot be demoted or deactivated")
        if user.id == admin.id:
            raise HTTPException(status_code=409, detail="You cannot demote or deactivate yourself")

    for field in ("name", "role", "organisation", "is_active"):
        if field in changes:
            setattr(user, field, changes[field])
    if "password" in changes:
        user.password_hash = hash_password(changes["password"])

    db.commit()
    return user


def _removes_an_active_admin(user: User, changes: dict) -> bool:
    """True if the change would demote or deactivate a currently active admin."""
    if user.role != "admin" or not user.is_active:
        return False
    return changes.get("role", "admin") != "admin" or changes.get("is_active", True) is False
