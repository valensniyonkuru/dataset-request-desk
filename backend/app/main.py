import logging
import time

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import get_db
from app.logging_config import setup_logging

setup_logging()
logger = logging.getLogger("app")

app = FastAPI(title="Dataset Request Desk API")


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
                    # Set by authentication once it exists; null until then.
                    "user_id": getattr(request.state, "user_id", None),
                }
            },
        )


@app.get("/health")
def health(db: Session = Depends(get_db)):
    """Liveness and database check. Returns 503 if the database is unreachable."""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.warning("health check failed: database unreachable", extra={"fields": {"error": str(exc)}})
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return {"status": "ok"}
