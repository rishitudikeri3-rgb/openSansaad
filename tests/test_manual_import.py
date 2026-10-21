import json
from pathlib import Path

import httpx
import pytest

from opensansad.db.models import Acquisition, Authority, DocType, Source
from opensansad.ingest.cache import Cache, NotADocument
from opensansad.ingest.manual import ImportConflict, import_manual, read_log
from opensansad.ingest.ratelimit import MemoryGate
from opensansad.ingest.store import RawStore

URL = "https://www.legislative.gov.in/static/uploads/2025/07/note (s.11A).pdf"
KEY = "https://www.legislative.gov.in/static/uploads/2025/07/note%20(s.11A).pdf"


class _Recorder:
    def record(self, url, status_code, error) -> None:
        pass


def _no_network(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"manual import must not touch the network: {request.url}")


@pytest.fixture
def cache(tmp_path: Path) -> Cache:
    return Cache(RawStore(tmp_path / "raw"), MemoryGate(sleep=lambda s: None), _Recorder(),
                 user_agent="OpenSansad/test (contact: test@example.org)",
                 transport=httpx.MockTransport(_no_network), sleep=lambda s: None)


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    f = inbox / "4a626088.pdf"
    f.write_bytes(b"%PDF-1.4 fourth schedule note")
    return f


def run(cache, log, file, **kw):
    args = dict(url=URL, source=Source.OTHER, doc_type=DocType.NOTIFICATION,
                title="Fourth Schedule amendment note")
    return import_manual(cache, log, file, **(args | kw))


def test_import_logs_provenance(cache, pdf, tmp_path) -> None:
    log = tmp_path / "manual_imports.jsonl"
    doc, record, new = run(cache, log, pdf, note="found via legislative.gov.in search")
    assert new and doc.acquisition is Acquisition.MANUAL
    assert pdf.exists()  # copied, never moved
    assert record.url == KEY  # same normalization as the cache key
    lines = [json.loads(ln) for ln in log.read_text().splitlines()]
    assert lines == [{
        "url": KEY, "sha256": doc.sha256, "imported_at": record.imported_at,
        "source": "other", "doc_type": "notification",
        "title": "Fourth Schedule amendment note", "authority": "primary",
        "original_filename": "4a626088.pdf", "note": "found via legislative.gov.in search",
    }]
    assert cache.get(URL, source="other").sha256 == doc.sha256  # served from the store


def test_reimport_is_idempotent(cache, pdf, tmp_path) -> None:
    log = tmp_path / "manual_imports.jsonl"
    _, first, _ = run(cache, log, pdf)
    _, again, new = run(cache, log, pdf, note="a different note does not conflict")
    assert not new and again == first
    assert len(read_log(log)) == 1
    assert len(cache.store.versions(KEY, "other")) == 1


def test_new_bytes_for_same_url_are_a_new_entry(cache, pdf, tmp_path) -> None:
    log = tmp_path / "manual_imports.jsonl"
    run(cache, log, pdf)
    pdf.write_bytes(b"%PDF-1.4 revised note")
    _, _, new = run(cache, log, pdf)
    assert new and len({r.sha256 for r in read_log(log)}) == 2


def test_conflicting_metadata_is_refused(cache, pdf, tmp_path) -> None:
    log = tmp_path / "manual_imports.jsonl"
    run(cache, log, pdf)
    with pytest.raises(ImportConflict):
        run(cache, log, pdf, authority=Authority.SECONDARY)
    assert len(read_log(log)) == 1


def test_refused_import_leaves_no_raw_file(cache, pdf, tmp_path) -> None:
    log = tmp_path / "manual_imports.jsonl"
    run(cache, log, pdf, url="https://example.org/a.pdf")
    other = tmp_path / "inbox" / "same-bytes.pdf"
    other.write_bytes(pdf.read_bytes())
    log.write_text(log.read_text().replace("https://example.org/a.pdf", KEY))  # pre-logged
    with pytest.raises(ImportConflict):
        run(cache, log, other, title="A different title")
    assert cache.store.versions(KEY, "other") == []


def test_bytes_already_fetched_by_the_cache_are_refused(cache, pdf, tmp_path) -> None:
    from opensansad.db.models import Acquisition as Acq

    cache.store.write(KEY, "other", [pdf.read_bytes()], status_code=200,
                      content_type="application/pdf", acquisition=Acq.FETCHED)
    log = tmp_path / "manual_imports.jsonl"
    with pytest.raises(ImportConflict, match="fetched automatically"):
        run(cache, log, pdf)
    assert not log.exists()


def test_secondary_copy(cache, tmp_path) -> None:
    f = tmp_path / "consolidated.html"
    f.write_text("<!doctype html><html><body>1. Short title...</body></html>")
    _, record, _ = run(cache, tmp_path / "log.jsonl", f, url="https://example.org/act-1996",
                       doc_type=DocType.PRINCIPAL, title="Consolidated text (secondary)",
                       authority=Authority.SECONDARY)
    assert record.authority == "secondary"


@pytest.mark.parametrize("content", [
    b"<html><head><title>Access Denied</title></head><body>WAF</body></html>",
    b"PK\x03\x04 a zip, not a document",
])
def test_non_documents_are_refused(cache, tmp_path, content) -> None:
    f = tmp_path / "saved.bin"
    f.write_bytes(content)
    log = tmp_path / "log.jsonl"
    with pytest.raises(NotADocument):
        run(cache, log, f)
    assert not log.exists()


@pytest.mark.parametrize("kw", [{"url": "4a626088.pdf"}, {"url": "file:///tmp/x.pdf"},
                                {"title": "  "}])
def test_bad_arguments(cache, pdf, tmp_path, kw) -> None:
    with pytest.raises(ValueError):
        run(cache, tmp_path / "log.jsonl", pdf, **kw)


def test_cli_parses_arguments(monkeypatch, pdf, tmp_path, cache, capsys) -> None:
    import opensansad.ingest.manual as manual

    monkeypatch.setattr(manual.Cache, "from_settings", classmethod(lambda cls: cache))

    class S:
        data_dir = tmp_path

    monkeypatch.setattr("opensansad.config.get_settings", lambda: S)
    manual.main([str(pdf), "--url", URL, "--source", "other", "--doc-type", "notification",
                 "--title", "Fourth Schedule amendment note", "--secondary"])
    assert read_log(tmp_path / "manual_imports.jsonl")[0].authority == "secondary"
    assert "logged" in capsys.readouterr().out
