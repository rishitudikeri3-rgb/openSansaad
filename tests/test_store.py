from datetime import UTC, datetime
from pathlib import Path

from opensansad.db.models import Acquisition, DocFormat
from opensansad.ingest.store import (
    RawStore,
    looks_like_error_page,
    normalize_url,
    sniff_format,
    url_hash,
)

PDF = b"%PDF-1.7\n...document bytes..."


def test_normalize_url() -> None:
    assert normalize_url("HTTPS://PRSIndia.org:443/billtrack#top") == "https://prsindia.org/billtrack"
    assert normalize_url("http://example.org:8080") == "http://example.org:8080/"
    assert normalize_url("https://x.org/a?b=1&a=2") == "https://x.org/a?b=1&a=2"  # query kept as-is


def test_normalize_url_percent_encoding() -> None:
    raw = "https://prsindia.org/files/2021/Arbitration and Conciliation (Amendment) Act, 2021.pdf"
    enc = "https://prsindia.org/files/2021/Arbitration%20and%20Conciliation%20(Amendment)%20Act,%202021.pdf"
    assert normalize_url(raw) == normalize_url(enc) == enc
    # unreserved %7e decoded; reserved %2c only uppercased
    assert normalize_url("https://x.org/%7euser/%2c") == "https://x.org/~user/%2C"
    assert normalize_url("https://x.org/a%2Fb") == "https://x.org/a%2Fb"  # reserved stays encoded
    assert normalize_url("https://x.org/a%28b%29") != normalize_url("https://x.org/a(b)")
    assert normalize_url("https://x.org/हिंदी.pdf") == "https://x.org/%E0%A4%B9%E0%A4%BF%E0%A4%82%E0%A4%A6%E0%A5%80.pdf"
    assert normalize_url("https://x.org/s?q=a b&x=%41") == "https://x.org/s?q=a%20b&x=A"
    for url in [raw, enc, "https://x.org/%7euser/%2c"]:
        assert normalize_url(normalize_url(url)) == normalize_url(url)  # idempotent
    assert url_hash("https://X.org/a#frag") == url_hash("https://x.org/a")


def test_sniff_format() -> None:
    assert sniff_format(PDF) is DocFormat.PDF
    assert sniff_format(b"\xef\xbb\xbf  <!DOCTYPE html><html>") is DocFormat.HTML
    assert sniff_format(b"garbage", "application/pdf") is DocFormat.PDF
    assert sniff_format(b"garbage", "text/plain") is DocFormat.OTHER


def test_write_versions_never_overwrite(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    url = "https://prsindia.org/files/a.pdf"
    v1 = store.write(url, "prs", [PDF], status_code=200, content_type="application/pdf",
                     acquisition=Acquisition.FETCHED, now=datetime(2026, 1, 1, tzinfo=UTC))
    v2 = store.write(url, "prs", [PDF + b"changed"], status_code=200, content_type=None,
                     acquisition=Acquisition.FETCHED, now=datetime(2026, 2, 1, tzinfo=UTC))

    assert v1.path != v2.path
    assert v1.path.read_bytes() == PDF  # old version untouched
    assert v1.path.suffix == ".pdf"
    assert [v.sha256 for v in store.versions(url, "prs")] == [v1.sha256, v2.sha256]
    assert store.latest(url, "prs").sha256 == v2.sha256
    assert store.latest(url, "prs").from_cache


def test_identical_bytes_reuse_existing_version(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    url = "https://prsindia.org/files/a.pdf"
    a = store.write(url, "prs", [PDF], status_code=200, content_type=None,
                    acquisition=Acquisition.FETCHED)
    b = store.write(url, "prs", [PDF[:5], PDF[5:]], status_code=200, content_type=None,
                    acquisition=Acquisition.FETCHED)
    assert a.path == b.path
    assert len(store.versions(url, "prs")) == 1
    assert not list(a.path.parent.glob("*.part"))


def test_failed_stream_leaves_no_partial_file(tmp_path: Path) -> None:
    store = RawStore(tmp_path)

    def broken():
        yield PDF
        raise ConnectionError("dropped")

    url = "https://x.org/a.pdf"
    try:
        store.write(url, "prs", broken(), status_code=200, content_type=None,
                    acquisition=Acquisition.FETCHED)
    except ConnectionError:
        pass
    entry = store.entry_dir(url, "prs")
    assert list(entry.iterdir()) == []
    assert store.latest(url, "prs") is None


def test_import_file_copies_and_marks_manual(tmp_path: Path) -> None:
    src = tmp_path / "Downloads" / "a199626.pdf"
    src.parent.mkdir()
    src.write_bytes(PDF)
    store = RawStore(tmp_path / "raw")
    doc = store.import_file("https://www.indiacode.nic.in/x.pdf", "indiacode", src)

    assert src.exists()  # copied, not moved
    assert doc.acquisition is Acquisition.MANUAL
    assert doc.format is DocFormat.PDF
    assert store.versions(doc.url, "indiacode")[0].original_filename == "a199626.pdf"


def test_error_page_detection() -> None:
    assert looks_like_error_page(b"<html><title>403 Forbidden</title><body>nope</body></html>")
    assert looks_like_error_page(b"<!DOCTYPE html><title>Access Denied</title>")
    assert not looks_like_error_page(PDF)
    assert not looks_like_error_page(b"<html><title>The Arbitration Act</title></html>")
    big = b"<html><title>Access Denied?</title>" + b"x" * 30_000
    assert not looks_like_error_page(big)  # real pages are large


def test_identical_refetch_updates_last_seen_only(tmp_path: Path) -> None:
    store = RawStore(tmp_path)
    url = "https://prsindia.org/robots.txt"
    t1, t2 = datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 3, 1, tzinfo=UTC)
    first = store.write(url, "_robots", [b"x"], status_code=200, content_type=None,
                        acquisition=Acquisition.FETCHED, now=t1)
    again = store.write(url, "_robots", [b"x"], status_code=200, content_type=None,
                        acquisition=Acquisition.FETCHED, now=t2)
    assert again.path == first.path and again.fetched_at == t1
    assert store.latest(url, "_robots").last_confirmed == t2
