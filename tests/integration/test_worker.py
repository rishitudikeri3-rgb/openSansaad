"""A real Celery worker (in a thread) on the docker Redis broker and result backend."""

from pathlib import Path

import pytest
from celery.contrib.testing.worker import start_worker
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from opensansad.db.base import Base
from opensansad.db.models import Acquisition
from opensansad.ingest.persist import Report
from opensansad.ingest.store import RawStore
from opensansad.tasks import ingest_tasks
from opensansad.tasks.celery_app import app

pytestmark = pytest.mark.integration

FIX = Path(__file__).parents[1] / "fixtures"
URL = "https://egazette.gov.in/WriteReadData/2021/225832.pdf"


def test_worker_runs_manifest(tmp_path: Path) -> None:
    data = tmp_path / "data"
    store = RawStore(data / "raw")
    store.write(URL, "egazette", [(FIX / "egazette_act3_2021_p1.pdf").read_bytes()],
                status_code=200, content_type="application/pdf",
                acquisition=Acquisition.FETCHED)
    (data / "manifest.toml").write_text(f'''
[[act]]
id = "26/1996"
short_title = "The Arbitration and Conciliation Act, 1996"

[[document]]
url = "{URL}"
source = "egazette"
doc_type = "amending"
title = "The Arbitration and Conciliation (Amendment) Act, 2021"
act = "26/1996"
number = "3/2021"
''')
    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    Base.metadata.create_all(engine)
    ingest_tasks.set_context(ingest_tasks.Context(
        store, sessionmaker(engine, expire_on_commit=False), data / "manifest.toml",
        ocr=None))
    try:
        with start_worker(app, pool="solo", perform_ping_check=False, shutdown_timeout=10):
            report = Report.from_dict(ingest_tasks.enqueue_manifest().get(timeout=60))
    finally:
        ingest_tasks.set_context(None)
    assert report.ok and [(r.status, r.extracted) for r in report.results] == [("added", True)]
