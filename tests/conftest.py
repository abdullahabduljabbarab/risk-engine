import os

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.database import get_db
from app.main import app

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://risk:risk@localhost:5434/risk_test",
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ensure_database() -> None:
    """Create the test database if it does not exist, so the suite is
    self-bootstrapping against a running PostgreSQL server."""
    url = make_url(TEST_DATABASE_URL)
    server = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"),
            {"n": url.database},
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    server.dispose()


def _migrate() -> None:
    """Build the schema from the Alembic migrations, so tests run against the
    migrated schema rather than ORM metadata (ADR-014). This is what makes the
    ORM and the migrations exercised together."""
    cfg = Config(os.path.join(_ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(_ROOT, "migrations"))
    cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.upgrade(cfg, "head")


_ensure_database()

engine = create_engine(TEST_DATABASE_URL)
TestSession = sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def setup_db():
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS outbox_events CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS account_state CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS risk_observations CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS risk_decisions CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS alembic_version CASCADE"))
    _migrate()
    yield


@pytest.fixture
def db():
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    def override_db():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
