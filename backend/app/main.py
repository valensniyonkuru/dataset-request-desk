import logging
import time

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api_docs import DESCRIPTION, TAGS
from app.db import get_db
from app.logging_config import setup_logging
from app.routers import analytics, assignments, auth, episodes, imports, requests, users

setup_logging()
logger = logging.getLogger("app")

app = FastAPI(title="Dataset Request Desk API", description=DESCRIPTION, version="0.1.0", openapi_tags=TAGS)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(requests.router)
app.include_router(episodes.router)
app.include_router(assignments.router)
app.include_router(imports.router)
app.include_router(analytics.router)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Write one JSON log line per HTTP request."""
    start = time.perf_counter()
    status = 500  # stays 500 if the handler raises an unhandled exception
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "request",
            extra={
                "fields": {
                    "method": request.method,
                    "path": request.url.path,
                    "status": status,
                    "duration_ms": round(duration_ms, 2),
                    # Set by get_current_user (or by login); null for anonymous requests.
                    "user_id": getattr(request.state, "user_id", None),
                }
            },
        )


@app.get("/", include_in_schema=False)
def root():
    """Opening the API's base URL in a browser lands on the interactive docs."""
    return RedirectResponse("/docs", status_code=307)


@app.get(
    "/health",
    tags=["health"],
    summary="Health check",
    description="**Public.** Returns `ok` when the API is up and can run a query on the database.",
    responses={
        503: {
            "description": "The database is unreachable.",
            "content": {"application/json": {"example": {"status": "unavailable"}}},
        }
    },
)
def health(db: Session = Depends(get_db)):
    """Liveness and database check. Returns 503 if the database is unreachable."""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.warning("health check failed: database unreachable", extra={"fields": {"error": str(exc)}})
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return {"status": "ok"}
