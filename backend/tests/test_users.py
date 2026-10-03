import pytest

NEW_CLIENT = {
    "email": "New.Client@Example.com",
    "name": "New Client",
    "role": "client",
    "organisation": "Gamma Robotics",
    "password": "new-client-password",
}


def every_users_endpoint(target_id: int) -> list[tuple[str, str, dict | None]]:
    """(method, path, json body) for each /users endpoint, with valid bodies."""
    return [
        ("GET", "/users", None),
        ("POST", "/users", NEW_CLIENT),
        ("PATCH", f"/users/{target_id}", {"name": "Renamed"}),
    ]


# --- who may call /users ---


@pytest.mark.parametrize("role", ["client", "operator"])
def test_clients_and_operators_get_403_on_every_users_endpoint(client, create_user, auth_headers, role):
    user = create_user(f"{role}@example.com", role=role)

    for method, path, body in every_users_endpoint(user.id):
        response = client.request(method, path, json=body, headers=auth_headers(user))
        assert response.status_code == 403, f"{method} {path}"


def test_unauthenticated_requests_get_401_on_every_users_endpoint(client, create_user):
    user = create_user("client@example.com")

    for method, path, body in every_users_endpoint(user.id):
        response = client.request(method, path, json=body)
        assert response.status_code == 401, f"{method} {path}"


# --- what admins can do ---


def test_admin_can_list_users_ordered_by_id(client, create_user, auth_headers):
    admin = create_user("admin@example.com", role="admin")
    create_user("ops@example.com", role="operator")
    create_user("client@example.com")

    response = client.get("/users", headers=auth_headers(admin))

    assert response.status_code == 200
    users = response.json()
    assert [user["email"] for user in users] == ["admin@example.com", "ops@example.com", "client@example.com"]
    assert [user["id"] for user in users] == sorted(user["id"] for user in users)
    assert all(user["is_active"] is True for user in users)


def test_admin_can_create_a_user_who_can_then_log_in(client, create_user, auth_headers):
    admin = create_user("admin@example.com", role="admin")

    response = client.post("/users", json=NEW_CLIENT, headers=auth_headers(admin))

    assert response.status_code == 201
    created = response.json()
    assert created["email"] == "new.client@example.com"  # stored lowercase
    assert created["role"] == "client"
    assert created["organisation"] == "Gamma Robotics"
    assert created["is_active"] is True

    login = client.post("/auth/login", json={"email": "new.client@example.com", "password": "new-client-password"})
    assert login.status_code == 200


def test_admin_can_update_a_user(client, create_user, auth_headers):
    admin = create_user("admin@example.com", role="admin")
    user = create_user("client@example.com", password="old-password")

    response = client.patch(
        f"/users/{user.id}",
        json={"name": "Renamed", "role": "operator", "organisation": None, "password": "brand-new-password"},
        headers=auth_headers(admin),
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"
    assert response.json()["role"] == "operator"
    assert response.json()["organisation"] is None
    old_login = client.post("/auth/login", json={"email": "client@example.com", "password": "old-password"})
    new_login = client.post("/auth/login", json={"email": "client@example.com", "password": "brand-new-password"})
    assert old_login.status_code == 401
    assert new_login.status_code == 200


def test_admin_can_deactivate_a_user(client, create_user, auth_headers):
    admin = create_user("admin@example.com", role="admin")
    user = create_user("client@example.com")

    response = client.patch(f"/users/{user.id}", json={"is_active": False}, headers=auth_headers(admin))

    assert response.status_code == 200
    assert response.json()["is_active"] is False


def test_duplicate_email_gives_409(client, create_user, auth_headers):
    admin = create_user("admin@example.com", role="admin")
    create_user("new.client@example.com")

    # NEW_CLIENT has the same email in a different case.
    response = client.post("/users", json=NEW_CLIENT, headers=auth_headers(admin))

    assert response.status_code == 409


@pytest.mark.parametrize(
    "change",
    [
        {"password": "short"},  # under 8 characters
        {"role": "superuser"},  # not a known role
        {"email": "not-an-email"},
        {"organisation": None},  # clients need an organisation
        {"password_hash": "anything"},  # unknown fields are rejected
    ],
)
def test_invalid_new_user_gives_422(client, create_user, auth_headers, change):
    admin = create_user("admin@example.com", role="admin")

    response = client.post("/users", json=NEW_CLIENT | change, headers=auth_headers(admin))

    assert response.status_code == 422


@pytest.mark.parametrize(
    "change",
    [
        {"password": "short"},
        {"name": None},  # only organisation may be null
        {"organisation": None},  # the target is a client, who needs one
    ],
)
def test_invalid_update_gives_422(client, create_user, auth_headers, change):
    admin = create_user("admin@example.com", role="admin")
    user = create_user("client@example.com")

    response = client.patch(f"/users/{user.id}", json=change, headers=auth_headers(admin))

    assert response.status_code == 422


def test_updating_an_unknown_user_gives_404(client, create_user, auth_headers):
    admin = create_user("admin@example.com", role="admin")

    response = client.patch("/users/999999", json={"name": "Nobody"}, headers=auth_headers(admin))

    assert response.status_code == 404


def test_no_response_contains_a_password_hash(client, create_user, auth_headers):
    admin = create_user("admin@example.com", role="admin", password="admin-password")
    headers = auth_headers(admin)

    responses = [
        client.post("/auth/login", json={"email": "admin@example.com", "password": "admin-password"}),
        client.get("/auth/me", headers=headers),
        client.post("/users", json=NEW_CLIENT, headers=headers),
        client.get("/users", headers=headers),
    ]
    responses.append(client.patch(f"/users/{responses[2].json()['id']}", json={"name": "X"}, headers=headers))

    for response in responses:
        assert response.status_code in (200, 201), response.request.url
        assert "password_hash" not in response.text
        assert "$argon2" not in response.text


# --- admin guard rails ---


def test_admin_cannot_deactivate_themselves(client, create_user, auth_headers):
    admin = create_user("admin@example.com", role="admin")
    create_user("admin2@example.com", role="admin")  # so the last-admin rule is not what stops it

    response = client.patch(f"/users/{admin.id}", json={"is_active": False}, headers=auth_headers(admin))

    assert response.status_code == 409
    assert response.json()["detail"] == "You cannot demote or deactivate yourself"


def test_admin_cannot_demote_themselves(client, create_user, auth_headers):
    admin = create_user("admin@example.com", role="admin")
    create_user("admin2@example.com", role="admin")

    response = client.patch(f"/users/{admin.id}", json={"role": "operator"}, headers=auth_headers(admin))

    assert response.status_code == 409
    assert response.json()["detail"] == "You cannot demote or deactivate yourself"


@pytest.mark.parametrize("change", [{"role": "operator"}, {"is_active": False}])
def test_last_active_admin_cannot_be_demoted_or_deactivated(client, create_user, auth_headers, change):
    admin = create_user("admin@example.com", role="admin")
    create_user("old-admin@example.com", role="admin", is_active=False)  # inactive admins do not count

    response = client.patch(f"/users/{admin.id}", json=change, headers=auth_headers(admin))

    assert response.status_code == 409
    assert response.json()["detail"] == "The last active admin cannot be demoted or deactivated"


def test_admin_can_demote_another_admin_but_not_the_last_one(client, create_user, auth_headers):
    admin = create_user("admin@example.com", role="admin")
    other_admin = create_user("admin2@example.com", role="admin")

    demote_other = client.patch(f"/users/{other_admin.id}", json={"role": "operator"}, headers=auth_headers(admin))
    demote_self = client.patch(f"/users/{admin.id}", json={"role": "operator"}, headers=auth_headers(admin))

    assert demote_other.status_code == 200
    assert demote_self.status_code == 409
    assert demote_self.json()["detail"] == "The last active admin cannot be demoted or deactivated"
