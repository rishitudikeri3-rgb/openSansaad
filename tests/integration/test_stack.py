"""Checks against the live docker stack. Run with `make up && make test-integration`."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from redis import Redis
from sqlalchemy import text

from opensansad.config import get_settings
from opensansad.db.session import get_engine
from opensansad.graph.client import GraphClient
from opensansad.tasks.celery_app import app as celery_app

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_postgres_select_one() -> None:
    with get_engine().connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar_one() == 1


def test_neo4j_ping() -> None:
    with GraphClient.from_settings() as graph:
        assert graph.ping()


def test_redis_ping() -> None:
    assert Redis.from_url(get_settings().redis_url).ping()


def test_celery_broker_reachable() -> None:
    with celery_app.connection_for_write() as conn:
        conn.ensure_connection(max_retries=1)


def test_alembic_upgrade_head() -> None:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "src/opensansad/db/migrations"))
    command.upgrade(cfg, "head")
    with get_engine().connect() as conn:
        assert conn.execute(text("SELECT to_regclass('alembic_version')")).scalar_one() is not None
