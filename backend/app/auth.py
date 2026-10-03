"""Authentication (who are you?) and authorisation (what may you do?).

Every protected route depends on get_current_user, directly or through
require_roles. That is the one place where access is decided.
"""

from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import User

ALGORITHM = "HS256"


def create_access_token(user: User) -> str:
    """A signed token that only says who the user is.

    The role is deliberately NOT in the token: it is read from the database on
    every request, so a role change applies immediately.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_MINUTES),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def _token_from_request(request: Request) -> str | None:
    """The session cookie, or else an "Authorization: Bearer <token>" header (for curl and tests)."""
    token = request.cookies.get(settings.COOKIE_NAME)
    if token:
        return token
    scheme, _, credentials = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() == "bearer" and credentials:
        return credentials
    return None


def _not_authenticated() -> HTTPException:
    return HTTPException(status_code=401, detail="Not authenticated", headers={"WWW-Authenticate": "Bearer"})


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = _token_from_request(request)
    if token is None:
        raise _not_authenticated()

    try:
        # Checks the signature and expiry; the algorithm list stops "alg: none" tricks.
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[ALGORITHM], options={"require": ["sub", "iat", "exp"]}
        )
        user_id = int(payload["sub"])
    except (jwt.InvalidTokenError, ValueError):
        raise _not_authenticated() from None

    # Loaded on every request, so deactivation and role changes apply immediately.
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise _not_authenticated()

    # Picked up by the request-logging middleware in main.py.
    request.state.user_id = user.id
    return user


def require_roles(*roles: str):
    """Dependency that allows only the given roles. Admin is not implied: list it explicitly."""

    def check_role(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Your role is not allowed to do this")
        return user

    return check_role
