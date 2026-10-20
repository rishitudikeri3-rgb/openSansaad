"""Raw document store: append-only, versioned files under data/raw/{source}/{url_hash}/.

No network access here. cache.py is the only writer.
"""

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from opensansad.db.models import Acquisition, DocFormat

_DEFAULT_PORTS = {"http": 80, "https": 443}
_EXT = {DocFormat.PDF: "pdf", DocFormat.HTML: "html", DocFormat.OTHER: "bin"}


def normalize_url(url: str) -> str:
    """Canonical form used for hashing: lowercase scheme/host, no default port, no fragment."""
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    if parts.port and parts.port != _DEFAULT_PORTS.get(scheme):
        host = f"{host}:{parts.port}"
    return urlunsplit((scheme, host, parts.path or "/", parts.query, ""))


def url_hash(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode()).hexdigest()[:24]


def sniff_format(head: bytes, content_type: str | None = None) -> DocFormat:
    """Decide format from the bytes first, falling back to the server's content type."""
    stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if stripped.startswith(b"%pdf"):
        return DocFormat.PDF
    if stripped.startswith((b"<!doctype html", b"<html")):
        return DocFormat.HTML
    ct = (content_type or "").lower()
    if "pdf" in ct:
        return DocFormat.PDF
    if "html" in ct:
        return DocFormat.HTML
    return DocFormat.OTHER


_ERROR_PAGE = re.compile(
    rb"access denied|forbidden|request rejected|captcha|not authori[sz]ed|error 40[34]"
)


def looks_like_error_page(data: bytes) -> bool:
    """Heuristic for a saved WAF / error page masquerading as a document."""
    if sniff_format(data[:1024]) is not DocFormat.HTML or len(data) > 20_000:
        return False
    title = re.search(rb"<title[^>]*>(.*?)</title>", data, re.I | re.S)
    text = (title.group(1) if title else data).lower()
    return bool(_ERROR_PAGE.search(text))


@dataclass(frozen=True)
class Version:
    file: str  # filename within the entry directory
    sha256: str
    fetched_at: str  # ISO 8601 UTC
    status_code: int | None
    content_type: str | None
    format: str
    acquisition: str
    original_filename: str | None = None
    final_url: str | None = None  # where the bytes actually came from, if redirected


@dataclass(frozen=True)
class StoredDoc:
    url: str  # the URL that was requested (the cache key)
    source: str
    path: Path
    sha256: str
    fetched_at: datetime
    status_code: int | None
    content_type: str | None
    format: DocFormat
    acquisition: Acquisition
    from_cache: bool
    final_url: str | None = None


class RawStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def entry_dir(self, url: str, source: str) -> Path:
        return self.root / source / url_hash(url)

    def _meta_path(self, url: str, source: str) -> Path:
        return self.entry_dir(url, source) / "meta.json"

    def _read_meta(self, url: str, source: str) -> dict:
        p = self._meta_path(url, source)
        if p.exists():
            return json.loads(p.read_text())
        return {"url": normalize_url(url), "versions": []}

    def _write_meta(self, url: str, source: str, meta: dict) -> None:
        p = self._meta_path(url, source)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, indent=2))
        os.replace(tmp, p)

    def versions(self, url: str, source: str) -> list[Version]:
        return [Version(**v) for v in self._read_meta(url, source)["versions"]]

    def latest(self, url: str, source: str) -> StoredDoc | None:
        versions = self.versions(url, source)
        if not versions:
            return None
        return self._to_doc(url, source, versions[-1], from_cache=True)

    def _to_doc(self, url: str, source: str, v: Version, *, from_cache: bool) -> StoredDoc:
        return StoredDoc(
            url=normalize_url(url),
            source=source,
            path=self.entry_dir(url, source) / v.file,
            sha256=v.sha256,
            fetched_at=datetime.fromisoformat(v.fetched_at),
            status_code=v.status_code,
            content_type=v.content_type,
            format=DocFormat(v.format),
            acquisition=Acquisition(v.acquisition),
            from_cache=from_cache,
            final_url=v.final_url,
        )

    def write(
        self,
        url: str,
        source: str,
        chunks: Iterable[bytes],
        *,
        status_code: int | None,
        content_type: str | None,
        acquisition: Acquisition,
        original_filename: str | None = None,
        final_url: str | None = None,
        now: datetime | None = None,
    ) -> StoredDoc:
        """Stream bytes into a new version. Never overwrites; identical bytes reuse the old file."""
        entry = self.entry_dir(url, source)
        entry.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        head = b""
        fd, tmp_name = tempfile.mkstemp(dir=entry, suffix=".part")
        try:
            with os.fdopen(fd, "wb") as f:
                for chunk in chunks:
                    if len(head) < 1024:
                        head += chunk[: 1024 - len(head)]
                    digest.update(chunk)
                    f.write(chunk)
            sha = digest.hexdigest()

            meta = self._read_meta(url, source)
            for existing in meta["versions"]:
                if existing["sha256"] == sha:
                    os.unlink(tmp_name)
                    return self._to_doc(url, source, Version(**existing), from_cache=False)

            fmt = sniff_format(head, content_type)
            ts = now or datetime.now(UTC)
            name = f"{ts.strftime('%Y%m%dT%H%M%S%fZ')}_{sha[:12]}.{_EXT[fmt]}"
            os.replace(tmp_name, entry / name)
        except BaseException:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise

        version = Version(
            file=name,
            sha256=sha,
            fetched_at=ts.isoformat(),
            status_code=status_code,
            content_type=content_type,
            format=fmt.value,
            acquisition=acquisition.value,
            original_filename=original_filename,
            final_url=final_url,
        )
        meta["versions"].append(asdict(version))
        self._write_meta(url, source, meta)
        return self._to_doc(url, source, version, from_cache=False)

    def import_file(self, url: str, source: str, path: Path) -> StoredDoc:
        """Copy (never move) a hand-downloaded file into the store as a manual acquisition."""
        path = Path(path).expanduser()

        def chunks() -> Iterable[bytes]:
            with path.open("rb") as f:
                while block := f.read(1 << 16):
                    yield block

        return self.write(
            url,
            source,
            chunks(),
            status_code=None,
            content_type=None,
            acquisition=Acquisition.MANUAL,
            original_filename=path.name,
        )
