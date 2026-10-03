from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import get_db
from app.main import app

client = TestClient(app)


def test_health_ok():
    # Uses the real database from DATABASE_URL.
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_returns_503_when_database_is_down():
    # Port 1 has nothing listening, so connecting fails like a real outage.
    dead_engine = create_engine(
        "postgresql+psycopg://nobody:nothing@127.0.0.1:1/none",
        connect_args={"connect_timeout": 1},
    )
    DeadSession = sessionmaker(bind=dead_engine)

    def get_dead_db():
        with DeadSession() as session:
            yield session

    app.dependency_overrides[get_db] = get_dead_db
    try:
        response = client.get("/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}
