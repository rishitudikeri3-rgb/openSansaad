"""Parliament Digital Library, now at elibrary.sansad.in (DSpace 7; the old eparlib.sansad.in
address is unreachable). Lok Sabha debates are indexed item by item: one item per
discussion, with date, Lok Sabha number, session and members, and a PDF.

DSpace's public REST API is used, as the Lok Sabha debates page itself does:

    /server/api/discover/search/objects?query=...&scope=<collection>   search
    /server/api/core/items/<uuid>/bundles?embed=bitstreams             an item's files

An item's TEXT bundle (the server's own text extraction) is ignored: text comes from our
extractor, so every span is reproducible from the raw PDF.
"""

import json
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote

from opensansad.db.models import DocType, Source
from opensansad.ingest.cache import Cache
from opensansad.ingest.sources.base import DocRef, LayoutChanged, SourceAdapter, absolute

BASE_URL = "https://elibrary.sansad.in"
# Collections the Lok Sabha debates page searches (checked 2026-10-21).
LS_DEBATES_PART2 = "03c67f26-e547-4c6f-914d-e4b443281a8c"  # Part 2 (other than Q&A)
LS_DEBATES_TEXT = "b2770639-c57b-4ba3-a9f1-57761c60d195"  # Lok Sabha Debates (Text)


def search_url(query: str, *, scope: str = LS_DEBATES_PART2, page: int = 0,
               size: int = 10) -> str:
    return absolute(BASE_URL, f"/server/api/discover/search/objects?query={quote(query)}"
                              f"&scope={scope}&page={page}&size={size}")


def bundles_url(item_uuid: str) -> str:
    return f"{BASE_URL}/server/api/core/items/{item_uuid}/bundles?embed=bitstreams"


@dataclass(frozen=True)
class DebateItem:
    uuid: str
    handle: str
    title: str
    date: date | None
    lok_sabha: int | None
    session: str | None  # as catalogued: Roman numerals ("VI")
    members: tuple[str, ...]
    debate_type: str | None  # "STATUTORY RESOLUTION AND GOVERNMENT BILL", ...
    collection_type: str | None  # "Part 2(Other than Questions And Answers)"


def _first(md: dict, key: str) -> str | None:
    values = md.get(key) or []
    return values[0]["value"].strip() if values and values[0].get("value") else None


def parse_search(url: str, payload: bytes) -> tuple[list[DebateItem], int]:
    """Items on one page of search results, plus the total number of hits."""
    try:
        result = json.loads(payload)["_embedded"]["searchResult"]
        total = result["page"]["totalElements"]
        objects = result.get("_embedded", {}).get("objects", [])
        items = []
        for o in objects:
            it = o["_embedded"]["indexableObject"]
            if it.get("type") != "item":
                continue
            md = it.get("metadata", {})
            issued = _first(md, "dc.date.issued")
            ls = _first(md, "dc.identifier.loksabhanumber")
            items.append(DebateItem(
                uuid=it["uuid"],
                handle=it["handle"],
                title=" ".join((_first(md, "dc.title") or it["name"]).split()),
                date=date.fromisoformat(issued) if issued else None,
                lok_sabha=int(ls) if ls and ls.isdigit() else None,
                session=_first(md, "dc.identifier.sessionnumber"),
                members=tuple(v["value"] for v in md.get("dc.contributor.members", [])),
                debate_type=_first(md, "dc.type.debate"),
                collection_type=_first(md, "dc.type"),
            ))
    except (ValueError, KeyError, TypeError) as e:
        raise LayoutChanged(f"{url}: not a DSpace search response ({e})") from e
    return items, total


def parse_bundles(url: str, payload: bytes, item: DebateItem) -> list[DocRef]:
    """The item's original PDF(s) as debate documents."""
    try:
        bundles = json.loads(payload)["_embedded"]["bundles"]
        refs = []
        for b in bundles:
            if b["name"] != "ORIGINAL":
                continue
            for bs in b["_embedded"]["bitstreams"]["_embedded"]["bitstreams"]:
                when = f", {item.date:%d %b %Y}" if item.date else ""
                refs.append(DocRef(
                    url=absolute(url, bs["_links"]["content"]["href"]),
                    source=Source.EPARLIB,
                    doc_type=DocType.DEBATE,
                    title=f"Lok Sabha debate{when}: {item.title}",
                    found_on=f"{BASE_URL}/handle/{item.handle}",
                ))
    except (ValueError, KeyError, TypeError) as e:
        raise LayoutChanged(f"{url}: not a DSpace bundles response ({e})") from e
    return refs


class EparlibAdapter(SourceAdapter):
    source = Source.EPARLIB

    def search(self, cache: Cache, query: str, *, scope: str = LS_DEBATES_PART2,
               size: int = 50, max_pages: int = 10) -> tuple[list[DebateItem], int]:
        """Search results across pages, plus the total number of hits.

        Bounded by max_pages (a vague query can match thousands): when the items returned
        are fewer than the total, the caller knows the list is partial.
        """
        items: list[DebateItem] = []
        total = 0
        for page in range(max_pages):
            url = search_url(query, scope=scope, page=page, size=size)
            page_bytes = self.fetch(cache, url, listing=True).path.read_bytes()
            batch, total = parse_search(url, page_bytes)
            items += batch
            if not batch or len(items) >= total:
                break
        return items, total

    def files(self, cache: Cache, item: DebateItem) -> list[DocRef]:
        url = bundles_url(item.uuid)
        payload = self.fetch(cache, url, listing=True).path.read_bytes()
        return parse_bundles(url, payload, item)
