"""Manifest entries -> `acts` / `documents` rows, with extracted text.

For each [[document]] in the manifest: find its bytes in the raw store (fetching through
the cache only when one is given), add or update its `documents` row keyed on
(url, sha256), and (re-)extract its text when the text is missing, changed on disk, or made
by an older extractor. Running it twice changes nothing.

Gazettes also have their masthead read (`egazette.meta_from_extraction`, on the extraction
just made) into `documents.meta`: every e-Gazette file, and amending acts / ordinances from
other sources (PRS hosts Gazette copies; a copy with no masthead is left alone). The
masthead is the document's own word on its kind and number: if it disagrees with the
manifest the entry is refused, never silently resolved either way.

The report also lists what needs a decision by hand: rows the manifest no longer describes,
rows superseded by newer bytes at the same URL, and identical bytes stored at several URLs.
Nothing is deleted automatically.

Paths in the DB are relative to the data directory (the raw store's parent): raw/..., text/...

    uv run python -m opensansad.ingest.persist [--offline] [--no-ocr]
"""

import argparse
import hashlib
import json
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from opensansad.db.models import Act, DocFormat, DocType, Document, Source
from opensansad.extract.document import EXTRACTOR_VERSION, Extraction
from opensansad.extract.pdf import OcrFn, default_ocr
from opensansad.extract.pipeline import extract_file, write_extraction
from opensansad.ingest.cache import Cache
from opensansad.ingest.manifest import DocEntry, Manifest, ManifestError
from opensansad.ingest.manual import read_log
from opensansad.ingest.sources.base import LayoutChanged
from opensansad.ingest.store import RawStore, StoredDoc

# Doc types that other sources (PRS) publish as copies of the Gazette.
_GAZETTE_COPIES = (DocType.AMENDING, DocType.ORDINANCE)

Status = Literal["added", "updated", "unchanged", "missing", "error"]


class EntryRefused(ValueError):
    """The entry cannot be persisted as written: the manifest needs fixing."""


@dataclass
class EntryResult:
    url: str
    status: Status
    doc_id: int | None = None
    extracted: bool = False
    detail: str | None = None


@dataclass
class Report:
    results: list[EntryResult] = field(default_factory=list)
    scanned: list[tuple[int, str]] = field(default_factory=list)  # (doc id, url): any OCR'd page
    stale: list[tuple[int, str]] = field(default_factory=list)  # rows not on EXTRACTOR_VERSION
    # Rows the manifest no longer describes: never deleted automatically.
    not_in_manifest: list[tuple[int, str]] = field(default_factory=list)
    superseded: list[tuple[int, str]] = field(default_factory=list)  # not the latest bytes
    # sha256 -> urls, for bytes stored at more than one URL (e.g. PRS copy of a Gazette)
    identical: dict[str, list[str]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(r.status not in ("missing", "error") for r in self.results)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.results:
            out[r.status] = out.get(r.status, 0) + 1
        out["extracted"] = sum(r.extracted for r in self.results)
        return out


def ensure_act(session: Session, number: int, year: int, short_title: str) -> Act:
    act = session.scalars(select(Act).where(Act.act_number == number, Act.year == year)).first()
    if act is None:
        act = Act(short_title=short_title, act_number=number, year=year)
        session.add(act)
    elif act.short_title != short_title:
        act.short_title = short_title
    session.flush()
    return act


def ensure_document(session: Session, entry: DocEntry, stored: StoredDoc, act: Act | None,
                    data_dir: Path) -> tuple[Document, Status]:
    """The row for exactly these bytes, created or brought in line with the manifest."""
    values = dict(
        act_id=act.id if act else None,
        doc_type=entry.doc_type,
        source=entry.source,
        title=entry.title,
        fetched_at=stored.fetched_at,
        acquisition=stored.acquisition,
        authority=entry.authority,
        format=stored.format,
        raw_path=_rel(stored.path, data_dir),
        amending_act_number=entry.number[0] if entry.number else None,
        amending_year=entry.number[1] if entry.number else None,
    )
    doc = session.scalars(select(Document).where(
        Document.url == stored.url, Document.sha256 == stored.sha256)).first()
    if doc is None:
        doc = Document(url=stored.url, sha256=stored.sha256, **values)
        session.add(doc)
        session.flush()
        return doc, "added"
    changed = False
    for key, value in values.items():
        if not _same(getattr(doc, key), value):
            setattr(doc, key, value)
            changed = True
    session.flush()
    return doc, "updated" if changed else "unchanged"


def needs_extraction(doc: Document, data_dir: Path) -> bool:
    if doc.format is DocFormat.OTHER:
        return False  # no extractor (e.g. errata .doc files)
    if doc.extractor_version != EXTRACTOR_VERSION or not doc.text_path or not doc.text_sha256:
        return True
    text = data_dir / doc.text_path
    return not text.exists() or _sha256(text) != doc.text_sha256


def extract_document(doc: Document, entry: DocEntry, data_dir: Path, *,
                     ocr: OcrFn | None = default_ocr,
                     extract: Callable[..., Extraction] = extract_file) -> None:
    """Write text + sidecar for the row's raw file and record them on the row."""
    raw = data_dir / doc.raw_path
    ex = extract(raw, doc.format, html_selector=entry.html_selector, ocr=ocr)
    en_path = write_extraction(ex, raw, data_dir / "raw", data_dir / "text")
    sidecar = json.loads(en_path.with_name(en_path.name.removesuffix(".en.txt") + ".json")
                         .read_text())
    doc.text_path = _rel(en_path, data_dir)
    doc.text_sha256 = sidecar["en_sha256"]
    doc.extractor_version = sidecar["extractor_version"]
    doc.is_scanned = ex.is_scanned
    doc.ocr_confidence = ex.ocr_confidence
    doc.meta = None
    if doc.format is DocFormat.PDF and (doc.source is Source.EGAZETTE or
                                        doc.doc_type in _GAZETTE_COPIES):
        from opensansad.ingest.sources.egazette import meta_from_extraction

        try:
            meta = meta_from_extraction(ex, doc.url)
        except LayoutChanged:
            if doc.source is Source.EGAZETTE:
                raise
            return  # a copy without the Gazette masthead: nothing to check
        doc.meta = _jsonable(asdict(meta)) | {"doc_type": meta.doc_type.value}


def check_masthead(doc: Document, entry: DocEntry) -> None:
    """Refuse an entry whose kind or number disagrees with the document's own masthead."""
    meta = doc.meta
    if not meta or not entry.masthead_check:
        return
    problems = []
    if meta["doc_type"] != entry.doc_type.value:
        problems.append(f"masthead says {meta['doc_type']} ({meta['kind']}), "
                        f"manifest says {entry.doc_type.value}")
    for label, printed, stated in (
        ("", (meta.get("number"), meta.get("year")), entry.number),
        ("corrects ", (meta.get("corrects_number"), meta.get("corrects_year")), entry.corrects),
    ):
        if printed[0] and printed != stated:
            said = "/".join(map(str, stated)) if stated else "nothing"
            problems.append(f"masthead says {label}No. {printed[0]} of {printed[1]}, "
                            f"manifest says {label or 'number '}{said}")
    if problems:
        hint = " (masthead read by OCR: if misread, set masthead_check = false with a note)" \
            if meta.get("from_ocr") else ""
        raise EntryRefused("; ".join(problems) + hint)


def find_stored(store: RawStore, entry: DocEntry, cache: Cache | None,
                manual_log: Path) -> StoredDoc | None:
    """The stored bytes for an entry: the latest version, fetched if a cache is given."""
    if entry.manual:
        stored = store.latest(entry.url, entry.source.value)
        if stored is None:
            return None
        logged = {r.sha256 for r in read_log(manual_log) if r.url == entry.url}
        if stored.sha256 not in logged:
            raise EntryRefused(f"latest stored version {stored.sha256[:12]} is not in the "
                               "manual import log")
        return stored
    if cache is not None:
        return cache.get(entry.url, source=entry.source.value)
    return store.latest(entry.url, entry.source.value)


def ingest(manifest: Manifest, session: Session, store: RawStore, *,
           cache: Cache | None = None, manual_log: Path | None = None,
           ocr: OcrFn | None = default_ocr,
           extract: Callable[..., Extraction] = extract_file) -> Report:
    """Persist every manifest entry; one transaction per entry, so a refusal stays local."""
    from opensansad.ingest.manual import LOG_NAME

    if store.root.name != "raw":
        raise ValueError(f"raw store must be DATA_DIR/raw, got {store.root}")
    data_dir = store.root.parent
    manual_log = manual_log or data_dir / LOG_NAME
    acts = {key: ensure_act(session, a.number, a.year, a.short_title)
            for key, a in manifest.acts.items()}
    session.commit()

    report = Report()
    latest: dict[str, str] = {}  # url -> sha256 of the version persisted this run
    for entry in manifest.documents:
        try:
            stored = find_stored(store, entry, cache, manual_log)
            if stored is None:
                report.results.append(EntryResult(entry.url, "missing", detail="not in raw store"))
                continue
            doc, status = ensure_document(session, entry, stored,
                                          acts.get(entry.act) if entry.act else None, data_dir)
            extracted = needs_extraction(doc, data_dir)
            if extracted:
                extract_document(doc, entry, data_dir, ocr=ocr, extract=extract)
                status = "updated" if status == "unchanged" else status
            check_masthead(doc, entry)
            session.commit()
            report.results.append(EntryResult(entry.url, status, doc.id, extracted))
            latest[entry.url] = stored.sha256
        except Exception as e:  # noqa: BLE001 - one bad file must not stop the run
            session.rollback()
            report.results.append(EntryResult(entry.url, "error",
                                              detail=f"{type(e).__name__}: {e}"))

    report.scanned = [(d.id, d.url) for d in session.scalars(
        select(Document).where(Document.ocr_confidence.is_not(None)).order_by(Document.id))]
    report.stale = [(d.id, d.url) for d in stale(session)]
    in_manifest = {e.url for e in manifest.documents}
    by_sha: dict[str, list[str]] = {}
    for d in session.scalars(select(Document).order_by(Document.id)):
        if d.url not in in_manifest:
            report.not_in_manifest.append((d.id, d.url))
        elif d.url in latest and d.sha256 != latest[d.url]:
            report.superseded.append((d.id, d.url))
        by_sha.setdefault(d.sha256, []).append(d.url)
    report.identical = {sha: urls for sha, urls in by_sha.items() if len(urls) > 1}
    return report


def stale(session: Session) -> list[Document]:
    """Extractable rows whose text is missing or from another extractor version."""
    return list(session.scalars(select(Document).where(
        Document.format != DocFormat.OTHER,
        or_(Document.extractor_version.is_(None),
            Document.extractor_version != EXTRACTOR_VERSION),
    ).order_by(Document.id)))


def _rel(path: Path, data_dir: Path) -> str:
    path, data_dir = Path(path).resolve(), Path(data_dir).resolve()
    return str(path.relative_to(data_dir)) if path.is_relative_to(data_dir) else str(path)


def _same(a, b) -> bool:
    """Equality that treats a naive datetime as UTC (SQLite drops the timezone)."""
    if isinstance(a, datetime) and isinstance(b, datetime):
        a, b = (d if d.tzinfo else d.replace(tzinfo=UTC) for d in (a, b))
    return a == b


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonable(value):
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value.isoformat() if isinstance(value, date) else value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m opensansad.ingest.persist",
                                     description="Load the manifest into the database.")
    parser.add_argument("--manifest", type=Path, help="default: DATA_DIR/manifest.toml")
    parser.add_argument("--offline", action="store_true",
                        help="use only files already in the raw store; never fetch")
    parser.add_argument("--no-ocr", action="store_true", help="skip OCR (scans get no text)")
    args = parser.parse_args(argv)

    from opensansad.config import get_settings
    from opensansad.db.session import get_sessionmaker
    from opensansad.ingest.manifest import MANIFEST_NAME, load_manifest

    settings = get_settings()
    try:
        manifest = load_manifest(args.manifest or settings.data_dir / MANIFEST_NAME)
    except ManifestError as e:
        print(e, file=sys.stderr)
        return 2
    store = RawStore(settings.raw_dir)
    cache = None if args.offline else Cache.from_settings()
    try:
        with get_sessionmaker()() as session:
            report = ingest(manifest, session, store, cache=cache,
                            ocr=None if args.no_ocr else default_ocr)
    finally:
        if cache:
            cache.close()

    for r in report.results:
        mark = "  (extracted)" if r.extracted else ""
        print(f"{r.status:9} {r.doc_id or '-':>4}  {r.url}{mark}")
        if r.detail:
            print(f"{'':15}{r.detail}")
    print("\n" + ", ".join(f"{k} {v}" for k, v in report.counts().items()))
    print(f"scanned (any page OCR'd): {len(report.scanned)}; stale rows: {len(report.stale)}")
    for label, rows in (("stale", report.stale), ("not in manifest", report.not_in_manifest),
                        ("superseded", report.superseded)):
        for doc_id, url in rows:
            print(f"  {label} {doc_id}: {url}")
    for sha, urls in report.identical.items():
        print(f"  identical bytes {sha[:12]}: " + "\n      ".join([""] + urls))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
