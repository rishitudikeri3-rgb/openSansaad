"""Manual imports: files downloaded by hand in a browser (India Code, legislative.gov.in, ...).

These cannot be re-fetched automatically, so every import is recorded in a committed log,
`data/manual_imports.jsonl`: one JSON object per line saying what the file is, where it
was downloaded from and its sha256. Anyone rebuilding the corpus downloads the same URLs
and checks the hashes. The bytes themselves go into the raw store via Cache.import_local.

    uv run python -m opensansad.ingest.manual FILE --url URL --source other \\
        --doc-type notification --title "..." [--secondary] [--note "..."]

Downloaded files are untrusted: they are copied, hashed and sniffed, never opened by
anything that executes content.
"""

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from opensansad.db.models import Acquisition, Authority, DocType, Source
from opensansad.ingest.cache import Cache
from opensansad.ingest.store import StoredDoc, normalize_url

LOG_NAME = "manual_imports.jsonl"


@dataclass(frozen=True)
class ManualImport:
    url: str  # normalized: the cache key
    sha256: str
    imported_at: str  # when the bytes first entered the raw store (ISO 8601 UTC)
    source: str
    doc_type: str
    title: str
    authority: str
    original_filename: str | None
    note: str | None = None


class ImportConflict(ValueError):
    """The same file was already logged with different metadata, or was fetched by the cache."""


def read_log(log_path: Path) -> list[ManualImport]:
    if not log_path.exists():
        return []
    with log_path.open(encoding="utf-8") as f:
        return [ManualImport(**json.loads(line)) for line in f if line.strip()]


def import_manual(
    cache: Cache,
    log_path: Path,
    file: Path,
    *,
    url: str,
    source: Source,
    doc_type: DocType,
    title: str,
    authority: Authority = Authority.PRIMARY,
    note: str | None = None,
) -> tuple[StoredDoc, ManualImport, bool]:
    """Import one file and log it. Returns (stored doc, log record, newly logged?).

    Re-importing the same bytes for the same URL is a no-op on the log; doing so with a
    different title / type / authority is an ImportConflict (fix the log by hand).
    """
    if urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).netloc:
        raise ValueError(f"need the absolute URL the file was downloaded from, got {url!r}")
    if not title.strip():
        raise ValueError("title is required")
    url = normalize_url(url)
    title = " ".join(title.split())
    sha = _sha256(file)
    # Every check runs before the bytes enter the raw store: a refused import leaves no trace.
    for v in cache.store.versions(url, source.value):
        if v.sha256 == sha and v.acquisition == Acquisition.FETCHED.value:
            raise ImportConflict(f"{url} ({sha[:12]}) was fetched automatically; "
                                 "no manual record needed")
    logged = next((r for r in read_log(log_path) if (r.url, r.sha256) == (url, sha)), None)
    if logged and _identity(logged) != (source.value, doc_type.value, title, authority.value):
        raise ImportConflict(f"{url} ({sha[:12]}) already logged as {_identity(logged)}")

    doc = cache.import_local(url, file, source=source.value)
    if logged:
        return doc, logged, False
    version = next(v for v in cache.store.versions(url, source.value) if v.sha256 == doc.sha256)
    record = ManualImport(
        url=url,
        sha256=doc.sha256,
        imported_at=version.fetched_at,
        source=source.value,
        doc_type=doc_type.value,
        title=title,
        authority=authority.value,
        original_filename=version.original_filename,
        note=" ".join(note.split()) if note else None,
    )

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
    return doc, record, True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).expanduser().open("rb") as f:
        while block := f.read(1 << 16):
            digest.update(block)
    return digest.hexdigest()


def _identity(r: ManualImport) -> tuple:
    return (r.source, r.doc_type, r.title, r.authority)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m opensansad.ingest.manual",
                                     description="Import a hand-downloaded file.")
    parser.add_argument("file", type=Path)
    parser.add_argument("--url", required=True, help="URL the file was downloaded from")
    parser.add_argument("--source", required=True, choices=[s.value for s in Source])
    parser.add_argument("--doc-type", required=True, choices=[d.value for d in DocType])
    parser.add_argument("--title", required=True)
    parser.add_argument("--secondary", action="store_true",
                        help="third-party compilation: never cited for a claim")
    parser.add_argument("--note", help="how the file was found, caveats")
    args = parser.parse_args(argv)

    from opensansad.config import get_settings

    log_path = get_settings().data_dir / LOG_NAME
    with Cache.from_settings() as cache:
        doc, record, new = import_manual(
            cache, log_path, args.file, url=args.url, source=Source(args.source),
            doc_type=DocType(args.doc_type), title=args.title,
            authority=Authority.SECONDARY if args.secondary else Authority.PRIMARY,
            note=args.note,
        )
    print(f"{'logged' if new else 'already logged'}: {record.url}\n"
          f"  sha256 {record.sha256}\n  raw    {doc.path}")


if __name__ == "__main__":
    main()
