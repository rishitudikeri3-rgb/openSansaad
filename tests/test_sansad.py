import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from opensansad.db.models import DocType, Source
from opensansad.ingest.cache import Cache
from opensansad.ingest.ratelimit import MemoryGate
from opensansad.ingest.sources import sansad
from opensansad.ingest.sources.base import LayoutChanged
from opensansad.ingest.store import RawStore, normalize_url

FIX = Path(__file__).parent / "fixtures"
BILLS = (FIX / "sansad_getbills_arbitration.json").read_bytes()


def bills():
    found, rows, total = sansad.parse_bills(sansad.bills_url("Arbitration"), BILLS)
    assert rows == total  # one page holds every row
    return {(b.number, b.year): b for b in found}, total


def test_bills_url_is_the_sites_own_query() -> None:
    url = sansad.bills_url("Arbitration")
    assert url == (
        "https://sansad.in/api_rs/legislation/getBills?loksabha=&sessionNo=&billName="
        "Arbitration&house=&ministryName=&billType=&billCategory=&billStatus="
        "&introductionDateFrom=&introductionDateTo=&passedInLsDateFrom=&passedInLsDateTo="
        "&passedInRsDateFrom=&passedInRsDateTo=&page=1&size=50&locale=en"
        "&sortOn=billIntroducedDate&sortBy=desc")
    assert url == normalize_url(url)
    assert "billName=The%20Arbitration" in sansad.bills_url("The Arbitration")


def test_bills_url_encodes_reserved_characters() -> None:
    from urllib.parse import parse_qs, urlsplit

    url = sansad.bills_url("Banking & Finance #2", ministryName="LAW AND JUSTICE")
    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    assert query["billName"] == ["Banking & Finance #2"] and parts.fragment == ""
    assert query["ministryName"] == ["LAW AND JUSTICE"] and query["page"] == ["1"]
    assert url == normalize_url(url)
    with pytest.raises(ValueError):
        sansad.bills_url("x", bilName="typo")


def test_duplicate_rows_are_merged() -> None:
    found, total = bills()
    assert total == 13 and len(found) == 11  # 2021 and NDIAC 2022 appear twice
    assert found[("16", 2021)].status == "Assented"  # furthest of "Passed" / "Assented"


def test_bill_2015_record() -> None:
    b = bills()[0][("252", 2015)]
    assert b.title == "The Arbitration and Conciliation (Amendment) Bill, 2015"
    assert (b.house, b.bill_type, b.ministry) == ("Lok Sabha", "Government", "LAW AND JUSTICE")
    assert (b.introduced, b.passed_ls, b.passed_rs, b.assented) == (
        date(2015, 12, 3), date(2015, 12, 17), date(2015, 12, 23), date(2015, 12, 31))
    assert (b.act_number, b.act_year) == (3, 2016)  # "3 " in the API, Act of the next year
    labels = {d.title.split(": ")[-1]: d for d in b.docs}
    assert set(labels) == {"as introduced", "as passed by Lok Sabha",
                           "as passed by both Houses", "errata", "Gazette copy of the Act"}
    assert labels["as passed by Lok Sabha"].url == (
        "https://sansad.in/getFile/BillsTexts/LSBillTexts/PassedLoksabha/252C_2015_LS_Eng.pdf"
        "?source=legislation")
    assert labels["errata"].lang == "hi"  # .../Hindi/Errata/...
    assert labels["Gazette copy of the Act"].doc_type is DocType.AMENDING
    assert labels["as passed by both Houses"].url.endswith(
        "Arbitration-3%20of%2016.pdf?source=legislation")  # spaces encoded as cache keys are
    assert all(d.source is Source.SANSAD for d in b.docs)


def test_rajya_sabha_and_historical_bills() -> None:
    found = bills()[0]
    b2019 = found[("XXI", 2019)]
    assert b2019.house == "Rajya Sabha" and b2019.passed_rs == date(2019, 7, 18)
    b1995 = found[("XXX", 1995)]  # the original 1996 Act's Bill, introduced in 1995
    assert (b1995.act_number, b1995.act_year, b1995.assented) == (26, 1996, date(1996, 8, 16))
    assert [d.title.split(": ")[-1] for d in b1995.docs] == ["as introduced"]
    assert found[("LXXV", 2003)].status == "Withdrawn" and not found[("LXXV", 2003)].docs


def test_principal_act_gazette_copy() -> None:
    ndiac = bills()[0][("127", 2019)]  # New Delhi International Arbitration Centre Bill
    gazette = next(d for d in ndiac.docs if d.title.endswith("Gazette copy of the Act"))
    assert gazette.doc_type is DocType.PRINCIPAL


def test_bills_layout_change_is_loud() -> None:
    with pytest.raises(LayoutChanged):
        sansad.parse_bills("u", b'{"error": "moved"}')
    with pytest.raises(LayoutChanged):
        sansad.parse_bills("u", b"<html>maintenance</html>")


def test_list_of_business() -> None:
    url = sansad.lob_url(date(2021, 2, 12))
    assert url == ("https://sansad.in/api_ls/business/listOfBusinessUrls?lobDay=12&lobMonth=2"
                   "&lobYear=2021&locale=en")
    refs = sansad.parse_lob(url, (FIX / "sansad_lob_2021-02-12.json").read_bytes())
    assert [r.title for r in refs] == ["Lok Sabha List Of Business, 12/02/2021",
                                       "Lok Sabha Revised List of Business, 12/02/2021"]
    assert {r.doc_type for r in refs} == {DocType.LIST_OF_BUSINESS}
    assert sansad.parse_lob(url, b"[]") == []  # no sitting that day
    with pytest.raises(LayoutChanged):
        sansad.parse_lob(url, b'{"message": "error"}')


def test_ls_sessions() -> None:
    data = json.loads((FIX / "sansad_ls_sessions.json").read_text())
    sessions = {(s.lok_sabha, s.session_no): s for s in sansad.parse_ls_sessions_data(data)}
    winter_2015 = sessions[(16, 6)]
    assert winter_2015.periods == ((date(2015, 11, 26), date(2015, 12, 23)),)
    assert date(2015, 12, 16) in winter_2015.sitting_dates  # the 2015 Bill debate
    assert len(sessions[(13, 3)].periods) == 2  # Budget session sat in two parts


def test_ls_sessions_from_page_data() -> None:
    data = (FIX / "sansad_ls_sessions.json").read_text()
    html = ('<html><script id="__NEXT_DATA__" type="application/json">'
            f'{{"props": {{"pageProps": {{"lokSabhaAndSession": {data}}}}}}}</script></html>')
    assert len(sansad.parse_ls_sessions(sansad.AGENDA_URL, html.encode())) == 84
    with pytest.raises(LayoutChanged):
        sansad.parse_ls_sessions(sansad.AGENDA_URL, b"<html>new design</html>")


def test_rajya_sabha_debates() -> None:
    url = sansad.rs_debate_url(249, date(2019, 7, 18))
    assert url == "https://sansad.in/api_rs/debate/date-wise-debate?sessionNo=249&date=18/07/2019"
    refs = {r.title.split(": ")[-1]: r for r in sansad.parse_rs_debates(
        url, (FIX / "sansad_rs_datewise_249_2019-07-18.json").read_bytes())}
    assert refs["English version"].doc_type is DocType.DEBATE and refs["English version"].url == (
        "https://cms.rajyasabha.nic.in/UploadedFiles/Debates/OfficialDebatesDatewise/English/249/"
        "E18.07.2019.pdf")
    assert refs["Hindi version"].lang == "hi" and refs["floor version (as spoken)"].lang == "mixed"
    written = refs["Part-1WrittenAnswerstoStarredandUnstarredQuestions"]
    assert written.doc_type is None  # questions: kept, not classified
    assert refs["Hindi version"].url.endswith("Debate%2018072019-%20H.pdf")


class _Recorder:
    def record(self, url, status_code, error) -> None:
        pass


def test_bills_follows_pages_through_cache(tmp_path) -> None:
    records = json.loads(BILLS)["records"]
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        page = int(request.url.params["page"])
        body = {"_metadata": {"totalElements": len(records)},
                "records": records[(page - 1) * 5: page * 5]}
        return httpx.Response(200, json=body)

    cache = Cache(RawStore(tmp_path), MemoryGate(sleep=lambda s: None), _Recorder(),
                  user_agent="OpenSansad/test (contact: test@example.org)",
                  transport=httpx.MockTransport(handler), sleep=lambda s: None)
    found = sansad.SansadAdapter().bills(cache, "Arbitration", size=5)
    assert len(calls) == 1 + 3  # robots + pages 1..3 (13 rows, 5 per page)
    assert len(found) == 11  # duplicates merged across pages too


def test_conflicting_duplicate_rows_are_logged(caplog) -> None:
    data = json.loads(BILLS)
    first = data["records"][0]
    data["records"].append(first | {"ministryName": "Some Other Ministry"})
    with caplog.at_level("WARNING", logger="opensansad.ingest.sources.sansad"):
        sansad.parse_bills("u", json.dumps(data).encode())
    (message,) = [r.getMessage() for r in caplog.records]  # status lags are INFO only
    assert "disagree on ministry" in message and "Some Other Ministry" in message
