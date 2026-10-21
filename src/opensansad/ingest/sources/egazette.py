"""e-Gazette of India (egazette.gov.in): the authoritative notified text.

The site's search is a stateful ASP.NET form, so there are no listing pages to parse.
Documents are fetched directly by ID instead:

    https://egazette.gov.in/WriteReadData/{year}/{file_id}.pdf

`file_id` is the last part of the Gazette ID printed on the masthead
("CG-DL-E-04112020-222941" -> 2020/222941). Older Gazettes (before ~late 2019) carry no
Gazette ID; the search results then show the same number in brackets.

What a Gazette says about itself (part, issue, ministry, act number, assent date, S.O.
number, what a corrigendum corrects) is read from its first pages by `parse_gazette`.
Fields read from OCR'd text are flagged (`from_ocr`): a scan can turn "PART II" into
"PART I", so such values are leads to verify, not facts.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from opensansad.db.models import DocType, Source
from opensansad.extract.document import Extraction
from opensansad.ingest.cache import Cache
from opensansad.ingest.sources.base import DocRef, LayoutChanged, SourceAdapter

BASE_URL = "https://egazette.gov.in"
HEAD_PAGES = 2  # a Hindi cover page can push the English masthead to page 2

Kind = Literal["act", "ordinance", "notification", "corrigendum"]

_GAZETTE_ID = re.compile(r"\bCG-[A-Z]{2}-E-(\d{2})(\d{2})(\d{4})-(\d+)\b")
_FILE_URL = re.compile(r"/WriteReadData/(\d{4})/(\d+)\.pdf$", re.I)


def gazette_pdf_url(year: int, file_id: int | str) -> str:
    return f"{BASE_URL}/WriteReadData/{year}/{file_id}.pdf"


def url_for_gazette_id(gazette_id: str) -> str:
    """'CG-DL-E-04112020-222941' -> .../WriteReadData/2020/222941.pdf"""
    m = _GAZETTE_ID.fullmatch(gazette_id.strip())
    if not m:
        raise ValueError(f"not a Gazette ID: {gazette_id!r}")
    return gazette_pdf_url(int(m.group(3)), m.group(4))


@dataclass(frozen=True)
class GazetteMeta:
    kind: Kind
    gazette_id: str | None  # "CG-DL-E-04112020-222941"; absent before ~late 2019
    file_id: str | None  # from the Gazette ID, else from the URL
    category: str | None  # "EXTRAORDINARY" / "WEEKLY"
    part: str | None  # "II"
    section: str | None  # "1", "3"
    subsection: str | None  # "ii"
    issue_no: int | None
    issue_date: date | None
    ministry: str | None
    department: str | None
    title: str | None  # "THE ARBITRATION AND CONCILIATION (AMENDMENT) ACT, 2015"
    number: int | None  # act / ordinance number: 3 (of 2016)
    year: int | None  # year of the number, which can differ from the title's year
    assent_date: date | None
    so_number: str | None  # "S.O. 3154(E)"
    dated: date | None  # "New Delhi, the 30th August, 2019"
    corrects: str | None  # corrigendum: the instrument corrected, as printed
    corrects_number: int | None
    corrects_year: int | None
    from_ocr: bool

    @property
    def doc_type(self) -> DocType:
        if self.kind == "ordinance":
            return DocType.ORDINANCE
        if self.kind == "act":
            amending = self.title and "(AMENDMENT)" in self.title.upper()
            return DocType.AMENDING if amending else DocType.PRINCIPAL
        return DocType.NOTIFICATION


_FLAGS = re.I | re.M
_PART = re.compile(
    r"\bPART\s+([IVX]+)\s*[—–-]+\s*Sec(?:tion|\.)\s*(\d+)"
    r"(?:\s*[—–-]*\s*(?:Sub-section\s*)?\(([ivx]+)\))?", re.I)
_ISSUE_NO = re.compile(r"^No\.\s*(\d+)\]", _FLAGS)
_ISSUE_DATE = re.compile(r"NEW DELHI,\s*[A-Z]+DAY,\s*([A-Z]+)\s+(\d{1,2}),\s*(\d{4})", re.I)
_CATEGORY = re.compile(r"^(EXTRAORDINARY|WEEKLY)$", _FLAGS)
_MINISTRY = re.compile(r"^(MINISTRY OF [A-Z ,&]+)$", re.M)
_DEPARTMENT = re.compile(r"^\(((?:Legislative )?Department[^)]*)\)$", _FLAGS)
_NUMBER = re.compile(r"^No\.\s*(\d+)\s+of\s+(\d{4})$", _FLAGS)
_ORDINAL = r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+),?\s+(\d{4})"
_ASSENT = re.compile(r"received the assent of the President on the\s+" + _ORDINAL, re.I)
_DATED = re.compile(r"^New Delhi, the\s+" + _ORDINAL, re.M)
_SO = re.compile(r"\bS\.\s?O\.\s*(\d+)\s*\(E\)")
_CORRIGENDUM = re.compile(r"^CORRIGENDUM$", re.M)
_CORRECTS = re.compile(r"CORRIGENDUM\s+In the\s+(.+?)\s*\(\s*No\.\s*(\d+)\s+of\s+(\d{4})\s*\)",
                       re.S)
_NOTIFICATION = re.compile(r"^NOTIFICATION$", re.M)


def _date(month: str, day: str, year: str) -> date | None:
    try:
        return datetime.strptime(f"{month.title()} {int(day)} {year}", "%B %d %Y").date()
    except ValueError:
        return None  # OCR-mangled month


def _title_before(lines: list[str], at: int) -> str | None:
    """The upper-case title lines just above the 'NO. x OF yyyy' line."""
    title: list[str] = []
    for line in reversed(lines[:at]):
        if not line:  # OCR text has blank lines between title and number
            if title:
                break
            continue
        if not line.isupper() or line.startswith(("[", "MINISTRY")):
            break
        title.insert(0, line)
    return " ".join(title) or None


def parse_gazette(pages: list[str], *, url: str | None = None, from_ocr: bool = False
                  ) -> GazetteMeta:
    """Read a Gazette's self-description from the English text of its first pages."""
    text = "\n".join(pages[:HEAD_PAGES])
    flat = " ".join(text.split())
    part, issue_no = _PART.search(text), _ISSUE_NO.search(text)
    if not (part or issue_no):
        raise LayoutChanged(f"{url or 'gazette'}: no PART/Section or issue number on page 1-2")

    gid = _GAZETTE_ID.search(text)
    file_id = gid.group(4) if gid else None
    if not file_id and url and (m := _FILE_URL.search(url)):
        file_id = m.group(2)

    lines = [ln.strip() for ln in text.split("\n")]
    number = title = None
    for i, line in enumerate(lines):
        if m := _NUMBER.match(line):
            number, title = m, _title_before(lines, i)
            break

    so = _SO.search(text)
    corrects = _CORRECTS.search(flat)
    if _CORRIGENDUM.search(text):
        kind: Kind = "corrigendum"
    elif title and re.search(r"\bORDINANCE\b", title):
        kind = "ordinance"
    elif title and re.search(r"\bACT\b", title):
        kind = "act"
    elif so or _NOTIFICATION.search(text):
        kind = "notification"
    else:
        raise LayoutChanged(f"{url or 'gazette'}: cannot tell act/ordinance/notification")

    def when(m: re.Match | None) -> date | None:
        return _date(m.group(2), m.group(1), m.group(3)) if m else None

    issue_date = _ISSUE_DATE.search(text)
    ministry = _MINISTRY.search(text)
    department = _DEPARTMENT.search(text)
    category = _CATEGORY.search(text)
    return GazetteMeta(
        kind=kind,
        gazette_id=gid.group(0) if gid else None,
        file_id=file_id,
        category=category.group(1).upper() if category else None,
        part=part.group(1).upper() if part else None,
        section=part.group(2) if part else None,
        subsection=part.group(3).lower() if part and part.group(3) else None,
        issue_no=int(issue_no.group(1)) if issue_no else None,
        issue_date=_date(*issue_date.groups()) if issue_date else None,
        ministry=ministry.group(1).title().replace(" Of ", " of ").replace(" And ", " and ")
        if ministry else None,
        department=department.group(1) if department else None,
        title=title,
        number=int(number.group(1)) if number else None,
        year=int(number.group(2)) if number else None,
        assent_date=when(_ASSENT.search(flat)),
        so_number=f"S.O. {so.group(1)}(E)" if so else None,
        dated=when(_DATED.search(text)),
        corrects=corrects.group(1) if corrects else None,
        corrects_number=int(corrects.group(2)) if corrects else None,
        corrects_year=int(corrects.group(3)) if corrects else None,
        from_ocr=from_ocr,
    )


def meta_from_extraction(ex: Extraction, url: str | None = None) -> GazetteMeta:
    head = ex.pages[:HEAD_PAGES]
    return parse_gazette([p.en for p in head], url=url,
                         from_ocr=any(p.method in ("ocr", "mixed") for p in head))


class EgazetteAdapter(SourceAdapter):
    source = Source.EGAZETTE

    def ref(self, url: str, title: str, doc_type: DocType | None = None,
            found_on: str | None = None) -> DocRef:
        """A reference to a Gazette PDF located by ID (see gazette_pdf_url)."""
        if not _FILE_URL.search(url):
            raise ValueError(f"not an e-Gazette file URL: {url}")
        return DocRef(url=url, source=self.source, doc_type=doc_type, title=title,
                      found_on=found_on)

    def describe(self, cache: Cache, url: str, extract=None) -> GazetteMeta:
        """Fetch a Gazette PDF (through the cache) and read its masthead."""
        from opensansad.extract.pdf import extract_pdf

        doc = self.fetch(cache, url)
        ex = (extract or extract_pdf)(doc.path)
        return meta_from_extraction(ex, url)
