#!/bin/sh
# Apply database migrations, optionally seed the demo users, then run the
# container command (uvicorn by default, or e.g. "pytest" with docker compose run).
set -e

alembic upgrade head

# Idempotent: creates missing seed users and updates existing ones.
if [ "$RUN_SEED" = "1" ]; then
    python -m app.seed
fi

exec "$@"
