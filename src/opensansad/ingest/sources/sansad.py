"""Digital Sansad (sansad.in): bills, Lists of Business, session calendar, Rajya Sabha debates.

sansad.in is a Next.js site whose pages load their data from public JSON APIs (no login;
the site's own code marks them API_AUTH: false). The adapter calls those APIs with exactly
the query string the site sends, so cache keys are stable and requests look like the
site's own. Endpoints were read from the page bundles on 2026-10-21 (docs/recon.md):

    /api_rs/legislation/getBills?...           bills of both Houses, with file links
    /api_ls/business/listOfBusinessUrls?...    a day's List of Business (+ revised)
    /api_rs/debate/date-wise-debate?...        a day's Rajya Sabha debate PDFs
    /ls/business/agenda (page data)            Lok Sabha sessions and sitting dates

Lok Sabha debates are on elibrary.sansad.in (see eparlib.py).
"""

import json
import logging
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from urllib.parse import quote

from opensansad.db.models import DocType, Source
from opensansad.ingest.cache import Cache
from opensansad.ingest.sources.base import DocRef, LayoutChanged, SourceAdapter, absolute

log = logging.getLogger(__name__)

BASE_URL = "https://sansad.in"

# The getBills query string, in the site's order. Every parameter is always sent.
_BILL_PARAMS = ("loksabha", "sessionNo", "billName", "house", "ministryName", "billType",
                "billCategory", "billStatus", "introductionDateFrom", "introductionDateTo",
                "passedInLsDateFrom", "passedInLsDateTo", "passedInRsDateFrom",
                "passedInRsDateTo")


def bills_url(bill_name: str = "", *, page: int = 1, size: int = 50, locale: str = "en",
              **filters: str) -> str:
    unknown = set(filters) - set(_BILL_PARAMS)
    if unknown:
        raise ValueError(f"unknown getBills filters: {sorted(unknown)}")
    values = {k: filters.get(k, "") for k in _BILL_PARAMS} | {"billName": bill_name}
    # Values are percent-encoded: bill titles contain "&" ("... Banking & ...").
    query = "&".join(f"{k}={quote(str(values[k]), safe='')}" for k in _BILL_PARAMS)
    return absolute(BASE_URL, f"/api_rs/legislation/getBills?{query}&page={page}&size={size}"
                              f"&locale={locale}&sortOn=billIntroducedDate&sortBy=desc")


def lob_url(day: date, locale: str = "en") -> str:
    return (f"{BASE_URL}/api_ls/business/listOfBusinessUrls?lobDay={day.day}"
            f"&lobMonth={day.month}&lobYear={day.year}&locale={locale}")


def rs_debate_url(session_no: int, day: date) -> str:
    return (f"{BASE_URL}/api_rs/debate/date-wise-debate?sessionNo={session_no}"
            f"&date={day:%d/%m/%Y}")


AGENDA_URL = f"{BASE_URL}/ls/business/agenda"

# ---- bills --------------------------------------------------------------------------

# Status order for merging duplicate rows: the API returns one row per status reached
# ("Passed" and "Assented" for the same bill). Terminal outcomes outrank everything.
_STATUS_RANK = {"Pending": 0, "Introduced": 0, "Passed": 1, "Assented": 2,
                "Lapsed": 3, "Withdrawn": 3, "Negatived": 3}

# file field -> (label, doc_type); doc_type None = kept, not classified
_BILL_FILES: dict[str, tuple[str, DocType | None]] = {
    "billIntroducedFile": ("as introduced", DocType.BILL),
    "billPassedInLSFile": ("as passed by Lok Sabha", DocType.BILL),
    "billPassedInRSFile": ("as passed by Rajya Sabha", DocType.BILL),
    "billPassedInBothHousesFile": ("as passed by both Houses", DocType.BILL),
    "errataFile": ("errata", DocType.BILL),
    "reportFile": ("committee report", DocType.COMMITTEE_REPORT),
    "billGazettedFile": ("Gazette copy of the Act", None),  # set from the act below
    "billSynopsisFile": ("synopsis", None),
}


@dataclass(frozen=True)
class SansadBill:
    number: str  # "252", or Roman for Rajya Sabha bills ("XXI")
    year: int
    house: str  # house of introduction
    title: str
    ministry: str | None
    bill_type: str | None  # "Government" / "Private Member"
    category: str | None
    status: str
    introduced: date | None
    passed_ls: date | None
    passed_rs: date | None
    referred_to_committee: date | None
    report_presented: date | None
    assented: date | None
    act_number: int | None
    act_year: int | None
    docs: tuple[DocRef, ...] = field(default=())


def _api_date(value: str | None) -> date | None:
    """'2021-02-04 00:00:00.0' or '11/03/2021'."""
    if not value or not value.strip():
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise LayoutChanged(f"unexpected date format: {value!r}")


def _int(value) -> int | None:
    return int(str(value).strip()) if value is not None and str(value).strip() else None


def _lang(url: str) -> str:
    return "hi" if re.search(r"/hindi/|_hin\b|_hin\.|-h\.pdf", url, re.I) else "en"


def _bill(record: dict, found_on: str) -> SansadBill:
    title = " ".join(record["billName"].split())
    act_number, act_year = _int(record.get("actNo")), _int(record.get("actYear"))
    docs = []
    for key, (label, doc_type) in _BILL_FILES.items():
        if not record.get(key):
            continue
        if key == "billGazettedFile" and act_number:
            doc_type = DocType.AMENDING if "(amendment)" in title.lower() else DocType.PRINCIPAL
        url = absolute(found_on, record[key])
        docs.append(DocRef(url=url, source=Source.SANSAD, doc_type=doc_type,
                           title=f"{title}: {label}", lang=_lang(url), found_on=found_on))
    return SansadBill(
        number=str(record["billNumber"]).strip(),
        year=int(record["billYear"]),
        house=record.get("billIntroducedInHouse") or "",
        title=title,
        ministry=record.get("ministryName"),
        bill_type=record.get("billType"),
        category=record.get("billCategory"),
        status=record.get("status") or "",
        introduced=_api_date(record.get("billIntroducedDate")),
        passed_ls=_api_date(record.get("billPassedInLSDate")),
        passed_rs=_api_date(record.get("billPassedInRSDate")),
        referred_to_committee=_api_date(record.get("referredToCommitteeDate")),
        report_presented=_api_date(record.get("reportPresentedDate")),
        assented=_api_date(record.get("billAssentedDate")),
        act_number=act_number,
        act_year=act_year,
        docs=tuple(docs),
    )


def _merge(a: SansadBill, b: SansadBill) -> SansadBill:
    """Two rows for one bill: keep every known value and the furthest status.

    Fields where both rows have different values are logged and the first row's value is
    kept. A lagging status is routine (one row still "Passed" after assent) and has a rule,
    so it is logged at INFO; any other disagreement at WARNING.
    """
    status = max((a.status, b.status), key=lambda s: _STATUS_RANK.get(s, -1))
    conflicts = [f for f in a.__dataclass_fields__ if f != "docs"
                 and getattr(a, f) is not None and getattr(b, f) is not None
                 and getattr(a, f) != getattr(b, f)]
    for level, fields in ((logging.INFO, [f for f in conflicts if f == "status"]),
                          (logging.WARNING, [f for f in conflicts if f != "status"])):
        if fields:
            log.log(level, "getBills rows for bill %s/%s (%s) disagree on %s: %s",
                    a.number, a.year, a.house, ", ".join(fields),
                    "; ".join(f"{f}={getattr(a, f)!r} vs {getattr(b, f)!r}" for f in fields))
    merged = {f: getattr(a, f) if getattr(a, f) is not None else getattr(b, f)
              for f in a.__dataclass_fields__}
    seen = {d.url for d in a.docs}
    docs = a.docs + tuple(d for d in b.docs if d.url not in seen)
    return replace(SansadBill(**merged), status=status, docs=docs)


def parse_bills(url: str, payload: bytes) -> tuple[list[SansadBill], int, int]:
    """Bills on one page of getBills results, duplicates merged; plus the number of rows on
    the page and the API's total row count (rows, not bills: duplicates count)."""
    try:
        data = json.loads(payload)
        records, total = data["records"], data["_metadata"]["totalElements"]
    except (ValueError, KeyError, TypeError) as e:
        raise LayoutChanged(f"{url}: not a getBills response ({e})") from e
    bills: dict[tuple, SansadBill] = {}
    for record in records:
        bill = _bill(record, url)
        key = (bill.number, bill.year, bill.house)
        bills[key] = _merge(bills[key], bill) if key in bills else bill
    return list(bills.values()), len(records), total


# ---- Lists of Business ---------------------------------------------------------------


def parse_lob(url: str, payload: bytes) -> list[DocRef]:
    """A day's List of Business ('N') and any revised list ('R'). No sitting -> []."""
    try:
        items = json.loads(payload)
    except ValueError as e:
        raise LayoutChanged(f"{url}: not JSON ({e})") from e
    if not isinstance(items, list):
        raise LayoutChanged(f"{url}: expected a list of List-of-Business entries")
    refs = []
    for item in items:
        if not item.get("url"):
            continue
        refs.append(DocRef(url=absolute(url, item["url"]), source=Source.SANSAD,
                           doc_type=DocType.LIST_OF_BUSINESS,
                           title=f"Lok Sabha {item.get('name', 'List of Business')}, "
                                 f"{item.get('date', '')}".strip(", "),
                           found_on=url))
    return refs


# ---- Lok Sabha session calendar ------------------------------------------------------


@dataclass(frozen=True)
class LsSession:
    lok_sabha: int
    session_no: int
    periods: tuple[tuple[date, date], ...]  # a session can sit in parts (Budget sessions)
    sitting_dates: tuple[date, ...]


def _dmy(value: str) -> date:
    return datetime.strptime(value.strip(), "%d/%m/%Y").date()


def parse_ls_sessions_data(data: list[dict]) -> list[LsSession]:
    sessions = []
    try:
        for ls in data:
            for s in ls["sessions"]:
                periods = []
                for period in s.get("sessionPeriod") or []:
                    start, end = period.split(" to ")
                    periods.append((_dmy(start), _dmy(end)))
                sessions.append(LsSession(
                    lok_sabha=int(ls["loksabha"]),
                    session_no=int(s["sessionNo"]),
                    periods=tuple(periods),
                    sitting_dates=tuple(_dmy(d) for d in s.get("dates") or []),
                ))
    except (KeyError, TypeError, ValueError) as e:
        raise LayoutChanged(f"Lok Sabha session data changed shape ({e})") from e
    return sessions


_NEXT_DATA = re.compile(rb'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def parse_ls_sessions(url: str, html: bytes) -> list[LsSession]:
    """Sessions embedded in the agenda page's Next.js data (props.pageProps.lokSabhaAndSession)."""
    m = _NEXT_DATA.search(html)
    try:
        data = json.loads(m.group(1))["props"]["pageProps"]["lokSabhaAndSession"]
    except (AttributeError, ValueError, KeyError, TypeError) as e:
        raise LayoutChanged(f"{url}: no lokSabhaAndSession in __NEXT_DATA__") from e
    return parse_ls_sessions_data(data)


# ---- Rajya Sabha debates -------------------------------------------------------------

# fileVersion -> (doc_type, lang, label). "Floor" is the record as spoken (each member in
# their own language); "English" / "Hindi" are the translated versions.
_RS_VERSIONS: dict[str, tuple[DocType | None, str, str]] = {
    "English": (DocType.DEBATE, "en", "English version"),
    "Hindi": (DocType.DEBATE, "hi", "Hindi version"),
    "Floor": (DocType.DEBATE, "mixed", "floor version (as spoken)"),
}


def parse_rs_debates(url: str, payload: bytes) -> list[DocRef]:
    try:
        days = json.loads(payload)
        refs = []
        for day in days:
            for f in day["fileDetail"]:
                version = f["fileVersion"]
                doc_type, lang, label = _RS_VERSIONS.get(version, (None, "en", version))
                refs.append(DocRef(url=absolute(url, f["fileUrl"]), source=Source.SANSAD,
                                   doc_type=doc_type,
                                   title=f"Rajya Sabha debate, {day['date']} "
                                         f"(session {day['sessionNumber']}): {label}",
                                   lang=lang, found_on=url))
    except (ValueError, KeyError, TypeError) as e:
        raise LayoutChanged(f"{url}: not a date-wise-debate response ({e})") from e
    return refs


# ---- adapter -------------------------------------------------------------------------


class SansadAdapter(SourceAdapter):
    source = Source.SANSAD

    def _get(self, cache: Cache, url: str) -> bytes:
        return self.fetch(cache, url, listing=True).path.read_bytes()

    def bills(self, cache: Cache, bill_name: str, *, size: int = 50) -> list[SansadBill]:
        """All bills matching a name, following pages until the API's total is reached."""
        found: dict[tuple, SansadBill] = {}
        page, seen_rows = 1, 0
        while True:
            url = bills_url(bill_name, page=page, size=size)
            bills, rows, total = parse_bills(url, self._get(cache, url))
            seen_rows += rows
            for b in bills:
                key = (b.number, b.year, b.house)
                found[key] = _merge(found[key], b) if key in found else b
            if not bills or seen_rows >= total:
                return list(found.values())
            page += 1

    def lists_of_business(self, cache: Cache, day: date) -> list[DocRef]:
        url = lob_url(day)
        return parse_lob(url, self._get(cache, url))

    def ls_sessions(self, cache: Cache) -> list[LsSession]:
        return parse_ls_sessions(AGENDA_URL, self._get(cache, AGENDA_URL))

    def rs_debates(self, cache: Cache, session_no: int, day: date) -> list[DocRef]:
        url = rs_debate_url(session_no, day)
        return parse_rs_debates(url, self._get(cache, url))
