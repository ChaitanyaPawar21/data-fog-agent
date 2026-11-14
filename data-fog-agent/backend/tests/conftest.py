import os
import sys
from pathlib import Path

# Always point tests at the test database, even if DATABASE_URL is set to the dev one,
# because every test wipes the tables.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://app:app@localhost:5432/datafog_test"
)
os.environ.setdefault("JWT_SECRET", "test-secret-that-is-long-enough-for-hs256")
os.environ.setdefault("CONFIG_ENCRYPTION_KEY", "Fhb4K3ZQhS0n2b2c5VJd3u0tq4W7O2bW8x3mJvYpZ1k=")
# Tests never call the real embeddings API.
os.environ["EMBEDDING_API_KEY"] = ""
os.environ["COOKIE_SECURE"] = "false"

import pytest  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from alembic import command  # noqa: E402
from app.create_admin import create_admin  # noqa: E402
from app.db import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.rate_limit import client as rate_limit_redis  # noqa: E402

# demo/ sits next to backend/ in the repo. In Docker, /app/tests -> /demo works the same way.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "demo"))
import generate  # noqa: E402

ALEMBIC_CONFIG = Config(str(Path(__file__).parent.parent / "alembic.ini"))


@pytest.fixture(scope="session", autouse=True)
def migrated_database():
    command.upgrade(ALEMBIC_CONFIG, "head")


@pytest.fixture(autouse=True)
def empty_tables():
    yield
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE orgs RESTART IDENTITY CASCADE"))
    # Tests log in far more often than a person would, so rate limits reset between tests.
    # Only rate keys are deleted: the same Redis holds the dev job queue.
    for key in rate_limit_redis.scan_iter("rate:*"):
        rate_limit_redis.delete(key)


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def admin():
    return create_admin("Chaiwala Co.", "admin@chaiwala.co", "Admin", "correct-password")


@pytest.fixture(scope="session")
def demo(tmp_path_factory):
    """Builds the demo company once per test run. Returns (output_dir, ground_truth).

    The demo databases and files stay for the whole run, but app rows are wiped after
    every test, so use `demo_org` to get the users, groups and sources back.
    """
    root = tmp_path_factory.mktemp("demo")
    ground_truth = generate.generate(root / "output", root / "ground_truth.yaml")
    return root / "output", ground_truth


@pytest.fixture
def demo_org(demo):
    """The demo org's users and groups, plus its sources. Returns the source ids."""
    output_dir, _ = demo
    generate.create_users_and_groups()
    return generate.register_demo_sources(output_dir)


def log_in(client, email, password=generate.DEMO_PASSWORD) -> dict:
    """Logs in and returns the Authorization header to send with requests."""
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def synced_demo(demo_org):
    """The demo org with every source synced into the catalogue. Returns the source ids."""
    from app.sync import sync_source

    for source_id in demo_org:
        sync_source(source_id)
    return demo_org
