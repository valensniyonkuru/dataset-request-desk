#!/bin/sh
# Apply database migrations, then run the container command
# (uvicorn by default, or e.g. "pytest" with docker compose run).
set -e

alembic upgrade head

exec "$@"
