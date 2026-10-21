from datetime import date
from pathlib import Path

import pytest

from opensansad.db.models import DocType, Source
from opensansad.ingest.sources import eparlib
from opensansad.ingest.sources.base import LayoutChanged
from opensansad.ingest.store import normalize_url

FIX = Path(__file__).parent / "fixtures"
QUERY = '"Arbitration and Conciliation (Amendment) Bill"'


def search():
    url = eparlib.search_url(QUERY)
    return eparlib.parse_search(url, (FIX / "elibrary_search_arbitration_bill.json").read_bytes())


def test_search_url() -> None:
    url = eparlib.search_url(QUERY)
    assert url == ("https://elibrary.sansad.in/server/api/discover/search/objects?query="
                   "%22Arbitration%20and%20Conciliation%20%28Amendment%29%20Bill%22"
                   "&scope=03c67f26-e547-4c6f-914d-e4b443281a8c&page=0&size=10")
    assert url == normalize_url(url)


def test_search_results() -> None:
    items, total = search()
    assert total == 29 and len(items) == 10
    item = next(i for i in items if i.date == date(2015, 12, 16) and "Statutory" in i.title)
    assert item.uuid == "67076016-fcc0-400e-93d5-9bbbb8e87cba"
    assert (item.lok_sabha, item.session) == (16, "VI")
    assert item.members == ("D.V. Sadananda Gowda", "N. K. Premachandran")
    assert item.debate_type == "STATUTORY RESOLUTION AND GOVERNMENT BILL"
    intro = next(i for i in items if i.title.startswith("Introduction of"))
    assert intro.title == "Introduction of the Arbitration and Conciliation (Amendment) Bill, 2021."


def test_bundles_give_the_original_pdf_only() -> None:
    items, _ = search()
    item = next(i for i in items if i.uuid.startswith("67076016"))
    url = eparlib.bundles_url(item.uuid)
    refs = eparlib.parse_bundles(url, (FIX / "elibrary_bundles_67076016.json").read_bytes(), item)
    assert len(refs) == 1  # TEXT and THUMBNAIL bundles skipped
    ref = refs[0]
    assert ref.url == ("https://elibrary.sansad.in/server/api/core/bitstreams/"
                       "c7efc397-699e-4af7-8482-3b27e93ded2f/content")
    assert (ref.doc_type, ref.source) == (DocType.DEBATE, Source.EPARLIB)
    assert ref.title.startswith("Lok Sabha debate, 16 Dec 2015: Discussion on the motion")
    assert ref.found_on == "https://elibrary.sansad.in/handle/123456789/336356"


def test_layout_change_is_loud() -> None:
    with pytest.raises(LayoutChanged):
        eparlib.parse_search("u", b'{"error": "x"}')
    items, _ = search()
    with pytest.raises(LayoutChanged):
        eparlib.parse_bundles("u", b"[]", items[0])


class _Recorder:
    def record(self, url, status_code, error) -> None:
        pass


def test_search_reports_partial_results(tmp_path) -> None:
    import httpx

    from opensansad.ingest.cache import Cache
    from opensansad.ingest.ratelimit import MemoryGate
    from opensansad.ingest.store import RawStore

    page = (FIX / "elibrary_search_arbitration_bill.json").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=page, headers={"content-type": "application/json"})

    cache = Cache(RawStore(tmp_path), MemoryGate(sleep=lambda s: None), _Recorder(),
                  user_agent="OpenSansad/test (contact: test@example.org)",
                  transport=httpx.MockTransport(handler), sleep=lambda s: None)
    items, total = eparlib.EparlibAdapter().search(cache, QUERY, size=10, max_pages=2)
    assert total == 29 and len(items) == 20  # capped at 2 pages: visibly partial
