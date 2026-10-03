"""The interactive docs load, list every route, and match the seed accounts and the input models."""

import json
from pathlib import Path

import pytest
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.schemas import AssignIn, LoginIn, RequestCreate, TransitionIn, UserCreate

client = TestClient(app)

PUBLIC_OPERATIONS = {("POST", "/auth/login"), ("POST", "/auth/logout"), ("GET", "/health")}


def documented_operations() -> dict[tuple[str, str], dict]:
    """{("GET", "/users"): <the OpenAPI operation>, ...} from /openapi.json."""
    paths = client.get("/openapi.json").json()["paths"]
    return {
        (method.upper(), path): operation
        for path, operations in paths.items()
        for method, operation in operations.items()
    }


def test_docs_page_loads():
    response = client.get("/docs")

    assert response.status_code == 200
    assert "swagger-ui" in response.text


def test_openapi_lists_every_route():
    response = client.get("/openapi.json")

    assert response.status_code == 200
    routes = {
        (method, route.path)
        for route in iter_route_contexts(app.routes)
        if getattr(route, "include_in_schema", False)
        for method in route.methods
    }
    assert ("POST", "/requests/{request_id}/transition") in routes  # sanity check: routers are included
    assert set(documented_operations()) == routes


def test_root_redirects_to_the_docs_and_is_hidden_from_them():
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/docs"
    assert "/" not in client.get("/openapi.json").json()["paths"]


def test_every_protected_operation_documents_401():
    for operation_key, operation in documented_operations().items():
        if operation_key not in PUBLIC_OPERATIONS:
            assert "401" in operation["responses"], operation_key


def test_every_operation_has_a_summary_description_and_tag():
    for operation_key, operation in documented_operations().items():
        assert operation.get("summary"), operation_key
        assert operation.get("description"), operation_key
        assert operation.get("tags"), operation_key


def test_description_lists_every_seed_account():
    seed_users = json.loads((Path(settings.SEED_DIR) / "users.json").read_text(encoding="utf-8"))

    for user in seed_users:
        assert f"| {user['email']} | {user['role']} | {user['password']} |" in app.description


@pytest.mark.parametrize("model", [LoginIn, UserCreate, RequestCreate, TransitionIn, AssignIn])
def test_examples_are_valid_input(model):
    # "Try it out" sends the example as is, so it must pass validation.
    [example] = model.model_config["json_schema_extra"]["examples"]

    model.model_validate(example)
