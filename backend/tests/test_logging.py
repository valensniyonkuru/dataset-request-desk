import logging


def request_log_lines(caplog) -> list[dict]:
    """The structured fields of each "request" log line written by the middleware."""
    return [record.fields for record in caplog.records if record.name == "app" and record.getMessage() == "request"]


def test_request_log_contains_the_user_id(client, create_user, auth_headers, caplog):
    user = create_user("ops@example.com", role="operator")

    with caplog.at_level(logging.INFO, logger="app"):
        client.get("/auth/me", headers=auth_headers(user))
        client.get("/auth/me")  # anonymous

    authenticated, anonymous = request_log_lines(caplog)
    assert authenticated["path"] == "/auth/me"
    assert authenticated["status"] == 200
    assert authenticated["user_id"] == user.id
    assert anonymous["status"] == 401
    assert anonymous["user_id"] is None
