"""Text for the interactive API docs at /docs. Nothing here changes behaviour."""

from app.schemas import ErrorOut

DESCRIPTION = """
Internal platform for dataset requests: a client asks for robot episodes,
operators fulfil the request, and the client accepts or rejects the delivery.

## Logging in on this page

1. Open **POST /auth/login**, click **Try it out**, use one of the demo accounts below and click **Execute**.
2. The browser now keeps the session cookie (HttpOnly), so every other **Try it out** on this page runs as that user.
3. To switch user, call **POST /auth/logout**, then log in again.

## Roles

- **client**: creates requests, sees only their own, accepts or rejects a delivered request.
- **operator**: sees all requests and moves them through the workflow.
- **admin**: what an operator can do, plus user management. Accepting or rejecting stays with the client.

## Demo accounts

Demo credentials from `seed/users.json`, created when the API starts. Not for real use.

| Email | Role | Password |
|---|---|---|
| admin@example.com | admin | admin123 |
| ops1@example.com | operator | ops123 |
| ops2@example.com | operator | ops123 |
| client-a@example.com | client | client123 |
| client-b@example.com | client | client123 |
"""

TAGS = [
    {"name": "auth", "description": "Log in, log out, and see who you are logged in as."},
    {"name": "users", "description": "User management (admin only). Users are deactivated, never deleted."},
    {"name": "requests", "description": "Dataset requests and their status workflow."},
    {"name": "episodes", "description": "Recorded robot episodes, to find ones to assign (operators and admins)."},
    {
        "name": "assignments",
        "description": "Assigning episodes to a request (only while it is in_progress), and a request's episodes.",
    },
    {"name": "health", "description": "Liveness and database check, for monitoring."},
]

NOT_LOGGED_IN = "Not logged in, the session expired, or the user was deactivated."


def error_responses(errors: dict[int, str]) -> dict:
    """{401: "when it happens"} -> the format of `responses=`, so each code is listed in /docs.

    422 is not passed in here: FastAPI documents it automatically for every
    endpoint that takes input, with the exact shape of the validation errors.
    Listing it ourselves would replace that schema.
    """
    return {code: {"model": ErrorOut, "description": when} for code, when in errors.items()}
