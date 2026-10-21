from datetime import date
from pathlib import Path

import httpx
import pytest

from opensansad.db.models import DocType, Source
from opensansad.extract.html import extract_html
from opensansad.ingest.cache import Cache
from opensansad.ingest.ratelimit import MemoryGate
from opensansad.ingest.sources.base import LayoutChanged
from opensansad.ingest.sources.prs import PrsAdapter, StatusEvent, bill_url, classify
from opensansad.ingest.store import RawStore

FIX = Path(__file__).parent / "fixtures"
PAGE = "https://prsindia.org/billtrack/the-arbitration-and-conciliation-amendment-bill-{}"
FILES = "https://prsindia.org/files/bills_acts/bills_parliament/"


def page(year: int) -> bytes:
    return (FIX / f"prs_billtrack_arbitration_{year}.html").read_bytes()


def parse(year: int):
    return PrsAdapter().parse_bill(PAGE.format(year), page(year))


def test_bill_2019_metadata_and_timeline() -> None:
    bill = parse(2019)
    assert bill.url == PAGE.format(2019)
    assert bill.title == "The Arbitration and Conciliation (Amendment) Bill, 2019"
    assert bill.ministry == "Law and Justice"
    assert bill.events == (
        StatusEvent("Introduced", "Rajya Sabha", date(2019, 7, 15)),
        StatusEvent("Passed", "Rajya Sabha", date(2019, 7, 18)),
        StatusEvent("Passed", "Lok Sabha", date(2019, 8, 1)),
    )


def test_bill_2019_documents() -> None:
    docs = {d.url: d for d in parse(2019).docs}
    act = docs[FILES + "2019/Arbitration%20and%20Conciliation%20(Amendment)%20Act,%202019.pdf"]
    assert act.doc_type is DocType.AMENDING
    assert act.source is Source.PRS and act.found_on == PAGE.format(2019)
    bill = docs[FILES + "2019/THE%20ARBITRATION%20AND%20CONCILIATION%20(AMENDMENT)"
                "%20BILL,%202019.pdf"]
    assert bill.doc_type is DocType.BILL
    assert bill.title == "The Arbitration and Conciliation (Amendment) Bill, 2019: Bill Text"
    summary = docs["https://prsindia.org/billtrack/prs-products/prs-bill-summary-3272"]
    assert summary.doc_type is DocType.EXTERNAL_REPORT  # PRS analysis, never a primary source


def test_bill_2015_hindi_copy_and_act_under_external_links() -> None:
    docs = parse(2015).docs
    by_type = {(d.doc_type, d.lang) for d in docs}
    assert (DocType.BILL, "hi") in by_type and (DocType.BILL, "en") in by_type
    act = next(d for d in docs if d.doc_type is DocType.AMENDING)
    assert act.url == FILES + "2015/Arbitration_and_Conciliation_Act,_2015_1.pdf"


def test_lapsed_bill_2018_has_no_act() -> None:
    bill = parse(2018)
    assert [e.stage for e in bill.events] == ["Introduced", "Passed"]  # passed LS only
    assert not any(d.doc_type is DocType.AMENDING for d in bill.docs)
    assert any(d.doc_type is DocType.BILL for d in bill.docs)


def test_urls_are_already_cache_keys() -> None:
    from opensansad.ingest.store import normalize_url

    for year in (2015, 2018, 2019):
        for d in parse(year).docs:
            assert d.url == normalize_url(d.url) and " " not in d.url


@pytest.mark.parametrize("label, heading, expected", [
    ("Bill Text", "Original Text", DocType.BILL),
    ("बिल कॉपी", "Original Text", DocType.BILL),
    ("The Arbitration and Conciliation (Amendment) Act, 2021", "Original Text", DocType.AMENDING),
    ("The Lokpal and Lokayuktas Act, 2013", "Original Text", DocType.PRINCIPAL),
    ("Standing Committee Report", "Committee Reports", DocType.COMMITTEE_REPORT),
    ("Report of the Select Committee", "Committee Reports", DocType.COMMITTEE_REPORT),
    ("The Arbitration and Conciliation (Amendment) Ordinance, 2020", "External Links",
     DocType.ORDINANCE),
    ("PRS Bill Summary", "PRS Products", DocType.EXTERNAL_REPORT),
    ("Amendments circulated", "Original Text", None),  # unknown: kept, not guessed
])
def test_classify(label, heading, expected) -> None:
    assert classify(label, heading) is expected


@pytest.mark.parametrize("year", [2015, 2018, 2019, 2021])
def test_bill_url_matches_canonical_slugs(year) -> None:
    title = f"The Arbitration and Conciliation (Amendment) Bill, {year}"
    assert bill_url(title) == PAGE.format(year)


def test_layout_change_is_loud() -> None:
    with pytest.raises(LayoutChanged):
        PrsAdapter().parse_bill("https://prsindia.org/billtrack/x", b"<html><body>Moved</body>")


def test_content_selector_drops_site_chrome() -> None:
    text = extract_html(page(2019), selector=PrsAdapter.html_selector).pages[0].en
    assert text.startswith("The Arbitration and Conciliation (Amendment) Bill, 2019")
    assert "FIND YOUR MP" not in text and "Switch to Hindi" not in text


class _Recorder:
    def record(self, url, status_code, error) -> None:
        pass


def test_discover_by_title_goes_through_cache(tmp_path) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /admin/\n")
        return httpx.Response(200, content=page(2019), headers={"content-type": "text/html"})

    cache = Cache(RawStore(tmp_path), MemoryGate(sleep=lambda s: None), _Recorder(),
                  user_agent="OpenSansad/test (contact: test@example.org)",
                  transport=httpx.MockTransport(handler), sleep=lambda s: None)
    adapter = PrsAdapter()
    bill = adapter.discover(cache, "The Arbitration and Conciliation (Amendment) Bill, 2019")
    assert calls == ["https://prsindia.org/robots.txt", PAGE.format(2019)]
    assert len(bill.events) == 3

    adapter.discover(cache, PAGE.format(2019))  # second time: served from the raw store
    assert len(calls) == 2
