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

Frontend (type check, unit tests with mocked API calls, production build):

```sh
cd frontend && npm ci && npm run typecheck && npm test && npm run build
```

The tests use a separate database (`TEST_DATABASE_URL`, or the `DATABASE_URL`
database name plus `_test`). It is dropped, recreated and migrated at the start
of every run, and each test's changes are rolled back.

## Analytics with 5 million episodes

`GET /analytics` runs three SQL queries, whatever the data size; all counting,
grouping and the median happen in PostgreSQL. Measured on 2 000 000 generated
episodes (details, plans and method in [docs/analytics-scale.md](docs/analytics-scale.md)):
about 150 ms per query for a 30-day range (using the index on `recorded_at`)
and 175 to 270 ms for a full year (a parallel sequential scan, the right plan
when the range covers the whole table). A migration adds statistics on the
UTC-day expression, without which Postgres misjudged the number of groups and
spilled a sort to disk (that query was 2.5 times slower at 2M).

At 5 million episodes, extrapolating from those measurements, each query
should take roughly half a second for the widest range: slower, but still
working with the current design. Beyond that, in order: a BRIN index on
`recorded_at` if episodes arrive in time order, a pre-aggregated daily
table maintained by the import, monthly partitioning, and only then a cache.
