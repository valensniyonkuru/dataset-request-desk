"""Guard test: every route must require a logged-in user, unless it is explicitly public.

If someone adds an endpoint and forgets the auth dependency, this test fails.
"""

from fastapi.routing import iter_route_contexts

from app.auth import get_current_user
from app.main import app

PUBLIC_ROUTES = {
    "/",  # only redirects to /docs
    "/health",
    "/auth/login",
    "/auth/logout",
    # FastAPI's generated API docs.
    app.openapi_url,
    app.docs_url,
    app.swagger_ui_oauth2_redirect_url,
    app.redoc_url,
}


def dependency_calls(dependant) -> set:
    """Every function in a route's dependency tree, including nested dependencies."""
    calls = set()
    for sub_dependant in dependant.dependencies:
        calls.add(sub_dependant.call)
        calls |= dependency_calls(sub_dependant)
    return calls


def all_routes():
    # iter_route_contexts also lists the routes of included routers, with their
    # full path and the dependencies added by include_router().
    return list(iter_route_contexts(app.routes))


def test_every_route_requires_a_logged_in_user():
    unprotected = []
    for route in all_routes():
        if route.path in PUBLIC_ROUTES:
            continue
        # Plain Starlette routes and mounts have no dependant, so they count as unprotected.
        dependant = getattr(route, "dependant", None)
        if dependant is None or get_current_user not in dependency_calls(dependant):
            unprotected.append(f"{sorted(route.methods or [])} {route.path}")

    assert unprotected == [], f"Routes without get_current_user: {unprotected}"


def test_public_routes_list_has_no_stale_entries():
    # Keeps the allow-list honest: every entry must be a real route.
    assert PUBLIC_ROUTES <= {route.path for route in all_routes()}
