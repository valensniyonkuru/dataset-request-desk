import base64
import json
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import HTTPException

from app.auth import ALGORITHM, require_roles
from app.config import settings
from app.models import User

GENERIC_401 = {"detail": "Invalid email or password"}


def login(client, email: str, password: str):
    return client.post("/auth/login", json={"email": email, "password": password})


def cookie_attributes(set_cookie_header: str) -> set[str]:
    """'name=value; HttpOnly; Path=/' -> {'httponly', 'path=/'} (everything after the value)."""
    return {part.strip().lower() for part in set_cookie_header.split(";")[1:]}


def edit_token_payload(token: str, **claims) -> str:
    """Change claims in a token but keep the original signature, like an attacker would."""
    header, payload, signature = token.split(".")
    data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    data.update(claims)
    new_payload = base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()
    return f"{header}.{new_payload}.{signature}"


# --- login and logout ---


def test_login_returns_the_user_and_sets_an_httponly_cookie(client, create_user):
    user = create_user("client@example.com", password="client-password")

    response = login(client, "client@example.com", "client-password")

    assert response.status_code == 200
    assert response.json() == {
        "id": user.id,
        "email": "client@example.com",
        "name": "client",
        "role": "client",
        "organisation": "Test Org",
    }
    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith(f"{settings.COOKIE_NAME}=")
    attributes = cookie_attributes(set_cookie)
    assert {"httponly", "samesite=lax", "path=/"} <= attributes
    assert "secure" not in attributes  # tests run with ENV=test, over plain http


def test_cookie_is_secure_in_production(client, create_user, monkeypatch):
    monkeypatch.setattr(settings, "ENV", "production")
    create_user("client@example.com", password="client-password")

    response = login(client, "client@example.com", "client-password")

    assert "secure" in cookie_attributes(response.headers["set-cookie"])


def test_wrong_password_and_unknown_email_get_the_same_401(client, create_user):
    create_user("client@example.com", password="client-password")

    wrong_password = login(client, "client@example.com", "not-the-password")
    unknown_email = login(client, "nobody@example.com", "client-password")

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json() == GENERIC_401
    assert "set-cookie" not in wrong_password.headers


def test_inactive_user_cannot_log_in(client, create_user):
    create_user("client@example.com", password="client-password", is_active=False)

    response = login(client, "client@example.com", "client-password")

    assert response.status_code == 401
    assert response.json() == GENERIC_401


def test_login_email_is_case_insensitive(client, create_user):
    create_user("client@example.com", password="client-password")

    response = login(client, "  Client@Example.COM ", "client-password")

    assert response.status_code == 200


def test_login_rejects_non_json_bodies(client, create_user):
    # An HTML form on another site can only send form or text/plain bodies, never JSON.
    create_user("client@example.com", password="client-password")
    body = json.dumps({"email": "client@example.com", "password": "client-password"})

    as_text = client.post("/auth/login", content=body, headers={"Content-Type": "text/plain"})
    as_form = client.post("/auth/login", data={"email": "client@example.com", "password": "client-password"})

    assert as_text.status_code == 422
    assert as_form.status_code == 422


def test_logout_clears_the_cookie(client, create_user):
    create_user("client@example.com", password="client-password")
    login(client, "client@example.com", "client-password")
    assert client.get("/auth/me").status_code == 200

    response = client.post("/auth/logout")

    assert response.status_code == 204
    assert "max-age=0" in cookie_attributes(response.headers["set-cookie"])
    assert client.get("/auth/me").status_code == 401


# --- /auth/me and token validation ---


def test_me_works_with_the_cookie(client, create_user):
    create_user("client@example.com", password="client-password")
    login(client, "client@example.com", "client-password")

    response = client.get("/auth/me")

    assert response.status_code == 200
    assert response.json()["email"] == "client@example.com"


def test_me_works_with_a_bearer_token(client, create_user, auth_headers):
    user = create_user("ops@example.com", role="operator")

    response = client.get("/auth/me", headers=auth_headers(user))

    assert response.status_code == 200
    assert response.json()["role"] == "operator"


def test_me_without_credentials_is_401(client):
    response = client.get("/auth/me")

    assert response.status_code == 401


def test_me_with_a_tampered_token_is_401(client, create_user, auth_headers):
    client_user = create_user("client@example.com")
    admin = create_user("admin@example.com", role="admin")
    token = auth_headers(client_user)["Authorization"].removeprefix("Bearer ")

    # Pretend to be the admin by editing the user id inside the token.
    forged = edit_token_payload(token, sub=str(admin.id))
    response = client.get("/auth/me", headers={"Authorization": f"Bearer {forged}"})

    assert response.status_code == 401


def test_me_with_an_expired_token_is_401(client, create_user):
    user = create_user("client@example.com")
    an_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    expired = jwt.encode(
        {"sub": str(user.id), "iat": an_hour_ago, "exp": an_hour_ago + timedelta(minutes=5)},
        settings.SECRET_KEY,
        algorithm=ALGORITHM,
    )

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {expired}"})

    assert response.status_code == 401


# --- roles ---


def test_admin_is_not_implicitly_allowed():
    admin = User(role="admin")

    with pytest.raises(HTTPException) as error:
        require_roles("operator")(user=admin)

    assert error.value.status_code == 403
    assert require_roles("operator", "admin")(user=admin) is admin


# --- the user is loaded from the database on every request ---


def test_deactivation_takes_effect_on_the_next_request(client, create_user, auth_headers):
    admin = create_user("admin@example.com", role="admin")
    user = create_user("client@example.com")
    user_headers = auth_headers(user)  # a valid token, issued before the deactivation
    assert client.get("/auth/me", headers=user_headers).status_code == 200

    deactivate = client.patch(f"/users/{user.id}", json={"is_active": False}, headers=auth_headers(admin))
    assert deactivate.status_code == 200

    assert client.get("/auth/me", headers=user_headers).status_code == 401


def test_role_change_takes_effect_on_the_next_request(client, create_user, auth_headers):
    admin = create_user("admin@example.com", role="admin")
    operator = create_user("ops@example.com", role="operator")
    operator_headers = auth_headers(operator)  # the token does not contain the role
    assert client.get("/users", headers=operator_headers).status_code == 403

    promote = client.patch(f"/users/{operator.id}", json={"role": "admin"}, headers=auth_headers(admin))
    assert promote.status_code == 200

    assert client.get("/users", headers=operator_headers).status_code == 200
