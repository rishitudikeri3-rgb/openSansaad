"""Source adapters: per-site knowledge of where documents are and what a page lists.

Adapters never write to the database and reach the network only through the Cache.
`parse` is pure (page bytes in, references out), so it is tested offline on saved pages;
fetching and persisting are the job of the ingestion tasks (step 3e).

What an adapter does NOT decide: which act a document belongs to, or an amending act's
number. A listing page cannot be trusted for those ("... (Amendment) Act, 2015" is Act 3
of 2016); they come from the ingestion manifest and from the document text itself.
"""

from abc import ABC
from dataclasses import dataclass
from datetime import timedelta
from typing import ClassVar
from urllib.parse import urljoin

from opensansad.db.models import DocType, Source
from opensansad.ingest.cache import Cache
from opensansad.ingest.store import StoredDoc, normalize_url


class LayoutChanged(ValueError):
    """A page no longer has the structure the adapter was written against."""


@dataclass(frozen=True)
class DocRef:
    """A document a page points to."""

    url: str  # absolute, normalized
    source: Source
    doc_type: DocType | None  # None: link seen but not classified (kept, never guessed)
    title: str
    lang: str = "en"  # "en" | "hi" | "mixed" (Rajya Sabha floor version: as spoken)
    found_on: str | None = None  # page the link came from (provenance)


class SourceAdapter(ABC):
    source: ClassVar[Source]
    html_selector: ClassVar[str | None] = None  # main content, for extract_html

    def parse(self, url: str, html: bytes) -> list[DocRef]:
        """References to documents listed on a page. Sources fetched by ID (e-Gazette)
        have no listing pages and do not implement it."""
        raise NotImplementedError(f"{type(self).__name__} has no listing pages")

    def __init__(self, listing_max_age: timedelta | None = None) -> None:
        # How old a stored listing page may be before it is re-fetched. None: never
        # (fine for one-off ingestion; the Session Monitor needs fresh listings).
        self.listing_max_age = listing_max_age

    @classmethod
    def from_settings(cls) -> "SourceAdapter":
        """An adapter that refreshes listings older than LISTING_TTL_HOURS."""
        from opensansad.config import get_settings

        return cls(listing_max_age=get_settings().listing_max_age)

    def fetch(self, cache: Cache, url: str, *, listing: bool = False) -> StoredDoc:
        """A document (fetched once, never refreshed) or a listing page (refreshed when
        older than listing_max_age)."""
        return cache.get(url, source=self.source.value,
                         max_age=self.listing_max_age if listing else None)


def absolute(base: str, href: str) -> str:
    return normalize_url(urljoin(base, href.strip()))
