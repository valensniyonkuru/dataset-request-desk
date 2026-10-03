from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    # Check a pooled connection is alive before using it, so a database
    # restart does not make the next request fail.
    pool_pre_ping=True,
    # Fail fast when the database is unreachable instead of hanging.
    connect_args={"connect_timeout": 5},
)

SessionLocal = sessionmaker(bind=engine)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed afterwards."""
    with SessionLocal() as session:
        yield session
