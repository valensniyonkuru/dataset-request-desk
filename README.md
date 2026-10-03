# Dataset Request Desk

Internal platform for tracking dataset requests and robot episodes. The brief is in [docs/WORK_TASK.md](docs/WORK_TASK.md).

**Status: in progress.**

- Frontend: https://desk.code-stack.tech
- Backend: https://desk-api.code-stack.tech (`/health`, `/docs`)

## Run locally

```sh
docker compose up --build
```

- Frontend: http://localhost:8082
- Backend: http://localhost:8000/health

On start, the api container applies the database migrations and creates the
seed users below (`RUN_SEED=1`). Re-running is safe: existing users are updated,
not duplicated.

Import the seed episodes (safe to run again; the rules are in
[docs/import-rules.md](docs/import-rules.md)). Operators can also upload a CSV with `POST /imports`.

```sh
docker compose exec api python -m app.import_csv /app/seed/episodes.csv
```

## Seed accounts

From [seed/users.json](seed/users.json). Passwords are stored only as Argon2 hashes.

| Email | Role | Password |
|---|---|---|
| admin@example.com | admin | admin123 |
| ops1@example.com | operator | ops123 |
| ops2@example.com | operator | ops123 |
| client-a@example.com | client | client123 |
| client-b@example.com | client | client123 |

## Run the tests

```sh
docker compose run --rm api pytest
```

The tests use a separate database (`TEST_DATABASE_URL`, or the `DATABASE_URL`
database name plus `_test`). It is dropped, recreated and migrated at the start
of every run, and each test's changes are rolled back.
