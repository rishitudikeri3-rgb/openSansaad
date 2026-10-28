"""Migration checks against a throwaway database, never the dev database."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine, create_engine, inspect, make_url, text
from sqlalchemy.exc import IntegrityError

import opensansad.db.models  # noqa: F401
from opensansad.config import get_settings
from opensansad.db.base import Base

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_DB = "opensansad_migration_test"


@pytest.fixture(scope="module")
def test_url() -> Iterator[str]:
    main_url = make_url(get_settings().database_url)
    admin = create_engine(main_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB}"))
        conn.execute(text(f"CREATE DATABASE {TEST_DB}"))
    url = main_url.set(database=TEST_DB).render_as_string(hide_password=False)
    yield url
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)"))
    admin.dispose()


@pytest.fixture(scope="module")
def alembic_cfg(test_url: str) -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "src/opensansad/db/migrations"))
    cfg.set_main_option("sqlalchemy.url", test_url)
    return cfg


@pytest.fixture(scope="module")
def engine(test_url: str, alembic_cfg: Config) -> Iterator[Engine]:
    command.upgrade(alembic_cfg, "head")
    eng = create_engine(test_url)
    yield eng
    eng.dispose()


def test_tables_created(engine: Engine) -> None:
    assert {"acts", "documents", "fetch_log", "clauses", "amendment_ops",
            "alembic_version"} <= set(
        inspect(engine).get_table_names()
    )


def test_migration_matches_models(engine: Engine) -> None:
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], f"models and migrations have drifted: {diff}"


def test_check_constraint_enforced(engine: Engine) -> None:
    with engine.connect() as conn, pytest.raises(IntegrityError):
        conn.execute(text(
            "INSERT INTO documents (doc_type, source, url, sha256, fetched_at, acquisition,"
            " format, raw_path) VALUES ('opinion_piece', 'prs', 'u', 's', now(), 'fetched',"
            " 'pdf', 'p')"
        ))


def test_downgrade_then_upgrade(engine: Engine, alembic_cfg: Config) -> None:
    command.downgrade(alembic_cfg, "base")
    assert "documents" not in inspect(engine).get_table_names()
    command.upgrade(alembic_cfg, "head")
    assert "documents" in inspect(engine).get_table_names()


def test_ingest_round_trip_on_postgres(engine: Engine, tmp_path: Path) -> None:
    """JSONB meta and timezone-aware fetched_at survive Postgres; a re-run changes nothing."""
    from sqlalchemy.orm import Session

    from opensansad.db.models import Acquisition
    from opensansad.ingest.manifest import load_manifest
    from opensansad.ingest.persist import ingest
    from opensansad.ingest.store import RawStore

    url = "https://egazette.gov.in/WriteReadData/2021/225832.pdf"
    store = RawStore(tmp_path / "raw")
    store.write(url, "egazette", [(REPO_ROOT / "tests/fixtures/egazette_act3_2021_p1.pdf")
                                  .read_bytes()],
                status_code=200, content_type="application/pdf",
                acquisition=Acquisition.FETCHED)
    (tmp_path / "manifest.toml").write_text(f'''
[[act]]
id = "26/1996"
short_title = "The Arbitration and Conciliation Act, 1996"

[[document]]
url = "{url}"
source = "egazette"
doc_type = "amending"
title = "The Arbitration and Conciliation (Amendment) Act, 2021"
act = "26/1996"
number = "3/2021"
''')
    manifest = load_manifest(tmp_path / "manifest.toml")
    with Session(engine) as session:
        first = ingest(manifest, session, store, ocr=None)
        second = ingest(manifest, session, store, ocr=None)
    assert first.counts() == {"added": 1, "extracted": 1}
    assert second.counts() == {"unchanged": 1, "extracted": 0}
    with engine.connect() as conn:
        assert conn.execute(text(
            "SELECT meta->>'number', jsonb_typeof(meta) FROM documents")).one() == ("3", "object")
