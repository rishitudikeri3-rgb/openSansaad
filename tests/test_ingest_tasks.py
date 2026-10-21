from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from opensansad.db.base import Base
from opensansad.db.models import Acquisition, Document
from opensansad.ingest.persist import Report
from opensansad.ingest.store import RawStore
from opensansad.tasks import ingest_tasks
from opensansad.tasks.celery_app import app

FIX = Path(__file__).parent / "fixtures"
GAZETTE_URL = "https://egazette.gov.in/WriteReadData/2021/225832.pdf"
MISSING_URL = "https://egazette.gov.in/WriteReadData/2020/222941.pdf"

MANIFEST = f'''
[[act]]
id = "26/1996"
short_title = "The Arbitration and Conciliation Act, 1996"

[[document]]
url = "{GAZETTE_URL}"
source = "egazette"
doc_type = "amending"
title = "The Arbitration and Conciliation (Amendment) Act, 2021"
act = "26/1996"
number = "3/2021"

[[document]]
url = "{MISSING_URL}"
source = "egazette"
doc_type = "ordinance"
title = "The Arbitration and Conciliation (Amendment) Ordinance, 2020"
act = "26/1996"
number = "14/2020"
'''


@pytest.fixture
def ctx(tmp_path: Path):
    data = tmp_path / "data"
    store = RawStore(data / "raw")
    store.write(GAZETTE_URL, "egazette", [(FIX / "egazette_act3_2021_p1.pdf").read_bytes()],
                status_code=200, content_type="application/pdf",
                acquisition=Acquisition.FETCHED)
    (data / "manifest.toml").write_text(MANIFEST)
    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    Base.metadata.create_all(engine)
    context = ingest_tasks.Context(store, sessionmaker(engine, expire_on_commit=False),
                                   data / "manifest.toml", ocr=None)
    ingest_tasks.set_context(context)
    eager = app.conf.task_always_eager, app.conf.task_eager_propagates
    app.conf.task_always_eager = app.conf.task_eager_propagates = True
    yield context
    app.conf.task_always_eager, app.conf.task_eager_propagates = eager
    ingest_tasks.set_context(None)


def test_manifest_fans_out_and_summarizes(ctx) -> None:
    report = Report.from_dict(ingest_tasks.enqueue_manifest().get())
    by_url = {r.url: r for r in report.results}
    assert by_url[GAZETTE_URL].status == "added" and by_url[GAZETTE_URL].extracted
    assert by_url[MISSING_URL].status == "missing" and not report.ok

    again = Report.from_dict(ingest_tasks.enqueue_manifest().get())
    assert {r.url: r.status for r in again.results}[GAZETTE_URL] == "unchanged"


def test_entry_task_refuses_url_outside_manifest(ctx) -> None:
    result = ingest_tasks.ingest_entry_task.delay("https://example.org/x.pdf").get()
    assert result["status"] == "error" and result["detail"] == "not in manifest"


def test_stale_rows_are_re_extracted(ctx) -> None:
    ingest_tasks.enqueue_manifest().get()
    with ctx.sessions() as s:
        doc = s.scalars(select(Document)).one()
        doc.extractor_version = "1"
        s.commit()
    report = Report.from_dict(ingest_tasks.enqueue_stale().get())
    assert [(r.status, r.extracted) for r in report.results] == [("updated", True)]
    assert report.stale == []
    assert Report.from_dict(ingest_tasks.enqueue_stale().get()).results == []  # nothing to do


def test_stale_row_outside_manifest_is_not_guessed(ctx) -> None:
    ingest_tasks.enqueue_manifest().get()
    ctx.manifest_path.write_text(MANIFEST.split("[[document]]")[0])  # entries removed
    with ctx.sessions() as s:
        s.scalars(select(Document)).one().extractor_version = "1"
        s.commit()
    report = Report.from_dict(ingest_tasks.enqueue_stale().get())
    (result,) = report.results
    assert result.status == "error" and "not in manifest" in result.detail
    assert [u for _, u in report.not_in_manifest] == [GAZETTE_URL]


PRS_URL = "https://prsindia.org/files/bills_acts/bills_parliament/2019/bill.pdf"
PRS_BILL = f'''
[[act]]
id = "26/1996"
short_title = "The Arbitration and Conciliation Act, 1996"

[[document]]
url = "{PRS_URL}"
source = "prs"
doc_type = "bill"
title = "The Arbitration and Conciliation (Amendment) Bill, 2019"
act = "26/1996"
'''


def _put(store: RawStore, url: str, source: str, fixture: str) -> None:
    store.write(url, source, [(FIX / fixture).read_bytes()], status_code=200,
                content_type="application/pdf", acquisition=Acquisition.FETCHED)


def test_reextracting_old_version_does_not_supersede_the_new(ctx) -> None:
    ctx.manifest_path.write_text(PRS_BILL)
    _put(ctx.store, PRS_URL, "prs", "prs_arbitration_bill_2019.pdf")
    ingest_tasks.enqueue_manifest().get()
    _put(ctx.store, PRS_URL, "prs", "gazette_arbitration_act_2019_p1-3.pdf")  # new bytes
    ingest_tasks.enqueue_manifest().get()
    with ctx.sessions() as s:
        old, new = s.scalars(select(Document).order_by(Document.id)).all()
        old.extractor_version = "1"
        s.commit()
    report = Report.from_dict(ingest_tasks.enqueue_stale().get())
    assert [r.doc_id for r in report.results] == [old.id]
    assert report.superseded == [(old.id, PRS_URL)]


def test_entry_held_by_another_task_is_reported(ctx) -> None:
    import threading

    from opensansad.ingest.locks import MemoryLock

    ctx.lock = MemoryLock(wait=0.05)
    with ctx.sessions() as s:
        ingest_tasks.ensure_acts(s, ctx.manifest())
    out = []
    with ctx.lock.hold(ingest_tasks._entry_key(GAZETTE_URL)):
        t = threading.Thread(
            target=lambda: out.append(ingest_tasks.ingest_entry_task.delay(GAZETTE_URL).get()))
        t.start()
        t.join()
    assert out[0]["status"] == "error" and "another task held this entry" in out[0]["detail"]
    assert ingest_tasks.ingest_entry_task.delay(GAZETTE_URL).get()["status"] == "added"


def test_cache_is_built_lazily_once(ctx) -> None:
    built = []
    ctx.make_cache = lambda: built.append(1)  # returns None: tasks read the store only
    assert built == []
    ingest_tasks.enqueue_manifest().get()
    assert len(built) == 1  # built on first use inside a task, then reused
