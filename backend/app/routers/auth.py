from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import create_access_token, get_current_user
from app.config import settings
from app.db import get_db
from app.models import User
from app.schemas import LoginIn, UserOut
from app.security import hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

# Verified against when the email is unknown, so that a login attempt takes
# about as long whether or not the account exists.
DUMMY_PASSWORD_HASH = hash_password("dummy password, never matches a real login")


def _cookie_settings() -> dict:
    # Secure (HTTPS only) in production. Locally the app runs on plain http.
    return {"httponly": True, "samesite": "lax", "path": "/", "secure": settings.ENV == "production"}


@router.post("/login", response_model=UserOut)
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))

    # Always run exactly one argon2 verification, even for an unknown email.
    password_ok = verify_password(body.password, user.password_hash if user else DUMMY_PASSWORD_HASH)

    # Same message for every failure, so it does not reveal which emails exist.
    if user is None or not password_ok or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    response.set_cookie(
        settings.COOKIE_NAME,
        create_access_token(user),
        max_age=settings.ACCESS_TOKEN_MINUTES * 60,
        **_cookie_settings(),
    )
    request.state.user_id = user.id  # so the log line for the login shows who logged in
    return user


@router.post("/logout", status_code=204)
def logout() -> Response:
    # No auth needed: clearing a cookie is harmless, and it must work with an expired token too.
    response = Response(status_code=204)
    response.delete_cookie(settings.COOKIE_NAME, **_cookie_settings())
    return response


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user
