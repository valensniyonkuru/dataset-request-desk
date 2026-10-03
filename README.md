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

## Run the tests

```sh
docker compose run --rm api pytest
```
