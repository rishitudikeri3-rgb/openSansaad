"""Celery tasks for ingestion: the manifest's entries in parallel, one task per entry.

The work itself is persist.py's (`ingest_entry`, `reextract`, `summarize`), so the command
line and the workers run the same code. Tasks are idempotent: re-running the manifest is
how an interrupted or partly failed run resumes (a dead worker's task is re-delivered,
since task_acks_late is on).

No Celery-level retries: the cache already retries transient failures with backoff, and
retrying on top would multiply requests to rate-limited sites. A blocked domain shows up
as an "error" result in the report; run the manifest again once the block has expired.

    make worker                                         # in one terminal
    uv run python -m opensansad.tasks.ingest_tasks manifest [--no-ocr]
    uv run python -m opensansad.tasks.ingest_tasks stale
"""

import argparse
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from celery import chord
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy.orm import Session, sessionmaker

from opensansad.db.models import Document
from opensansad.extract.pdf import OcrFn, default_ocr
from opensansad.ingest.cache import Cache
from opensansad.ingest.locks import EntryLock, LockTimeout, MemoryLock
from opensansad.ingest.manifest import Manifest, load_manifest
from opensansad.ingest.persist import (
    EntryResult,
    Report,
    ensure_acts,
    ingest_entry,
    print_report,
    reextract,
    stale,
    summarize,
)
from opensansad.ingest.store import RawStore, normalize_url, url_hash
from opensansad.tasks.celery_app import app

ENTRY_TIME_LIMIT = 30 * 60  # seconds (soft: reported as an error); OCR of long scans is slow
HARD_TIME_LIMIT = ENTRY_TIME_LIMIT + 5 * 60  # killed, for a task stuck where the soft limit
# cannot reach it. Both stay below the broker's visibility_timeout (celery_app.py), so a
# running task is never re-delivered to a second worker.


@dataclass
class Context:
    """What the tasks work with. Built once per worker process (lazily, after the fork),
    or set by tests."""

    store: RawStore
    sessions: sessionmaker[Session]
    manifest_path: Path
    ocr: OcrFn | None = default_ocr
    # One task per entry at a time: a re-delivered or doubly-enqueued task waits, then
    # finds the row up to date (instead of extracting twice and colliding on insert).
    lock: EntryLock = field(default_factory=MemoryLock)
    # Only workers fetch: the process that merely enqueues never builds a Cache.
    make_cache: Callable[[], Cache | None] = lambda: None
    _cache: list = field(default_factory=list, init=False, repr=False)  # [] until built

    @property
    def cache(self) -> Cache | None:
        if not self._cache:
            self._cache.append(self.make_cache())
        return self._cache[0]

    def manifest(self) -> Manifest:
        return load_manifest(self.manifest_path)  # re-read: picks up edits between runs


def _entry_key(url: str) -> str:
    return f"ingest:{url_hash(url)}"


_context: Context | None = None


def context() -> Context:
    global _context
    if _context is None:
        from redis import Redis

        from opensansad.config import get_settings
        from opensansad.db.session import get_sessionmaker
        from opensansad.ingest.locks import RedisLock
        from opensansad.ingest.manifest import MANIFEST_NAME

        s = get_settings()
        lock = RedisLock(Redis.from_url(s.redis_url), prefix="opensansad:ingest-lock",
                         ttl=HARD_TIME_LIMIT, wait=ENTRY_TIME_LIMIT)
        _context = Context(RawStore(s.raw_dir), get_sessionmaker(), s.data_dir / MANIFEST_NAME,
                           lock=lock, make_cache=Cache.from_settings)
    return _context


def set_context(ctx: Context | None) -> None:
    global _context
    _context = ctx


@app.task(name="opensansad.ingest_entry", soft_time_limit=ENTRY_TIME_LIMIT,
          time_limit=HARD_TIME_LIMIT)
def ingest_entry_task(url: str, ocr: bool = True) -> dict:
    ctx = context()
    url = normalize_url(url)
    entry = next((e for e in ctx.manifest().documents if e.url == url), None)
    if entry is None:
        return asdict(EntryResult(url, "error", detail="not in manifest"))
    try:
        with ctx.lock.hold(_entry_key(url)), ctx.sessions() as session:
            result = ingest_entry(entry, session, ctx.store, cache=ctx.cache,
                                  ocr=ctx.ocr if ocr else None)
    except (SoftTimeLimitExceeded, LockTimeout) as e:
        result = EntryResult(url, "error", detail=_not_finished(e))
    return asdict(result)


@app.task(name="opensansad.reextract", soft_time_limit=ENTRY_TIME_LIMIT,
          time_limit=HARD_TIME_LIMIT)
def reextract_task(doc_id: int, ocr: bool = True) -> dict:
    ctx = context()
    with ctx.sessions() as session:
        doc = session.get(Document, doc_id)
        url = doc.url if doc else f"documents.id={doc_id}"
    try:
        with ctx.lock.hold(_entry_key(url)), ctx.sessions() as session:
            result = reextract(doc_id, ctx.manifest(), session, ctx.store,
                               ocr=ctx.ocr if ocr else None)
    except (SoftTimeLimitExceeded, LockTimeout) as e:
        result = EntryResult(url, "error", doc_id, detail=_not_finished(e))
    return asdict(result)


def _not_finished(e: Exception) -> str:
    if isinstance(e, LockTimeout):
        return f"another task held this entry for over {ENTRY_TIME_LIMIT}s"
    return f"over {ENTRY_TIME_LIMIT}s: not finished"


@app.task(name="opensansad.summarize")
def summarize_task(results: list[dict]) -> dict:
    ctx = context()
    with ctx.sessions() as session:
        report = summarize(session, ctx.manifest(), [EntryResult(**r) for r in results],
                           ctx.store)
    return asdict(report)


def enqueue_manifest(*, ocr: bool = True):
    """Create the acts here (once, before any parallel entry), then one task per entry;
    the summary runs when all have finished. Returns the summary's AsyncResult."""
    ctx = context()
    manifest = ctx.manifest()
    with ctx.sessions() as session:
        ensure_acts(session, manifest)
    return _fan_out([ingest_entry_task.s(e.url, ocr) for e in manifest.documents])


def enqueue_stale(*, ocr: bool = True):
    ctx = context()
    with ctx.sessions() as session:
        ids = [d.id for d in stale(session)]
    return _fan_out([reextract_task.s(i, ocr) for i in ids])


def _fan_out(header: list):
    # A chord with an empty header never calls its body reliably: summarize directly.
    return chord(header)(summarize_task.s()) if header else summarize_task.delay([])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m opensansad.tasks.ingest_tasks",
                                     description="Run ingestion on the Celery workers.")
    parser.add_argument("what", choices=["manifest", "stale"])
    parser.add_argument("--no-ocr", action="store_true", help="skip OCR (scans get no text)")
    parser.add_argument("--timeout", type=float, default=3 * 3600,
                        help="seconds to wait for the workers (default 3h)")
    args = parser.parse_args(argv)

    enqueue = enqueue_manifest if args.what == "manifest" else enqueue_stale
    result = enqueue(ocr=not args.no_ocr)
    print(f"queued; waiting for workers (summary task {result.id})", file=sys.stderr)
    report = Report.from_dict(result.get(timeout=args.timeout))
    print_report(report)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
