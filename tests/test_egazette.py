from datetime import date
from pathlib import Path

import httpx
import pytest

from opensansad.db.models import DocType, Source
from opensansad.extract.pdf import extract_pdf
from opensansad.ingest.cache import Cache
from opensansad.ingest.ratelimit import MemoryGate
from opensansad.ingest.sources.base import LayoutChanged
from opensansad.ingest.sources.egazette import (
    EgazetteAdapter,
    gazette_pdf_url,
    meta_from_extraction,
    parse_gazette,
    url_for_gazette_id,
)
from opensansad.ingest.store import RawStore, normalize_url

FIX = Path(__file__).parent / "fixtures"
# What Tesseract reads from the corrigendum's image region (everything below the masthead).
CORRIGENDUM_BODY = (
    "MINISTRY OF LAW AND JUSTICE\n(Legislative Department)\n\n"
    "New Delhi, the 5th November, 2020/Kartika 14, 1942 (Saka)\n\nCORRIGENDUM\n\n"
    "In the Arbitration and Conciliation (Amendment) Ordinance, 2020 ( No. 14\n"
    "of 2020) as published in the Gazette of India, Extraordinary, Part II, Section 1, Issue\n"
    "No. 67, dated the 4th November, 2020, at page 2 in line 15, Section 3 in the marginal\n"
    "heading for “Subsitution” read “Substitution”.\n\n"
    "DR. G. NARAYANA RAJU,\nSecretary to the Govt. of India."
)


def no_ocr(page, clip=None):
    raise AssertionError("OCR should not be needed")


def body_ocr(page, clip):
    assert clip is not None  # only the image region, never the whole page
    return CORRIGENDUM_BODY, 0.95


def meta(name: str, ocr=no_ocr):
    return meta_from_extraction(extract_pdf(FIX / name, ocr=ocr))


# ---- URLs -------------------------------------------------------------------------


def test_url_from_gazette_id() -> None:
    assert url_for_gazette_id("CG-DL-E-04112020-222941") == (
        "https://egazette.gov.in/WriteReadData/2020/222941.pdf")
    assert gazette_pdf_url(2015, 166406) == "https://egazette.gov.in/WriteReadData/2015/166406.pdf"
    assert normalize_url(gazette_pdf_url(2015, 166406)) == gazette_pdf_url(2015, 166406)
    with pytest.raises(ValueError):
        url_for_gazette_id("NOT available")


# ---- real Gazettes ----------------------------------------------------------------


def test_act_2021_masthead() -> None:
    m = meta("egazette_act3_2021_p1.pdf")
    assert (m.kind, m.doc_type) == ("act", DocType.AMENDING)
    assert m.gazette_id == "CG-DL-E-11032021-225832" and m.file_id == "225832"
    assert (m.category, m.part, m.section, m.subsection) == ("EXTRAORDINARY", "II", "1", None)
    assert (m.issue_no, m.issue_date) == (5, date(2021, 3, 11))
    assert m.ministry == "Ministry of Law and Justice"
    assert m.department == "Legislative Department"
    assert m.title == "THE ARBITRATION AND CONCILIATION (AMENDMENT) ACT, 2021"
    assert (m.number, m.year, m.assent_date) == (3, 2021, date(2021, 3, 11))
    assert not m.from_ocr


def test_ordinance_2020() -> None:
    m = meta("egazette_ordinance14_2020_p1.pdf")
    assert (m.kind, m.doc_type) == ("ordinance", DocType.ORDINANCE)
    assert m.title == "THE ARBITRATION AND CONCILIATION (AMENDMENT) ORDINANCE, 2020"
    assert (m.number, m.year, m.issue_no) == (14, 2020, 67)
    assert m.assent_date is None  # ordinances are promulgated, not assented to


def test_notification_behind_a_hindi_cover_page() -> None:
    """S.O. 3154(E): page 1 is the Hindi version; the English text starts on page 2."""
    ex = extract_pdf(FIX / "egazette_so3154e_2019.pdf", ocr=no_ocr)
    assert "3154" not in ex.pages[0].en  # the Hindi number's digit runs stay with the Hindi
    m = meta_from_extraction(ex)
    assert (m.kind, m.doc_type) == ("notification", DocType.NOTIFICATION)
    assert (m.part, m.section, m.subsection, m.issue_no) == ("II", "3", "ii", 2887)
    assert m.so_number == "S.O. 3154(E)" and m.dated == date(2019, 8, 30)
    assert m.department == "Department of Legal Affairs"
    assert m.gazette_id is None  # pre-dates Gazette IDs on mastheads


def test_corrigendum_body_is_an_image() -> None:
    m = meta("egazette_corrigendum_ord14_2020.pdf", ocr=body_ocr)
    assert (m.kind, m.doc_type) == ("corrigendum", DocType.NOTIFICATION)
    assert m.corrects == "Arbitration and Conciliation (Amendment) Ordinance, 2020"
    assert (m.corrects_number, m.corrects_year) == (14, 2020)
    assert m.from_ocr  # the body came from OCR
    assert m.file_id == "222961" and m.issue_no == 68
    assert m.dated == date(2020, 11, 5) and m.ministry == "Ministry of Law and Justice"


# ---- OCR'd scan (text as Tesseract produced it for e-Gazette 2015/166406) -------------

ORDINANCE_2015_OCR = """REGISTERED NO. DL—(N)04/0007/2003—15

EXTRAORDINARY
PART I — Section 1
PUBLISHED BY AUTHORITY

No. 37] NEW DELHI, FRIDAY, OCTOBER 23, 2015/KARTIKA I, 1937 (SAKA)

Separate paging is given to this Part in order that it may be filed as a separate compilation.

MINISTRY OF LAW AND JUSTICE
(Legislative Department)

New Delhi, the 23rd October, 2015/Kartika 1, 1937 (Saka)

THE ARBITRATION AND CONCILIATION
(AMENDMENT) ORDINANCE, 2015

No. 9 oF 2015

Promulgated by the President in the Sixty-sixth Year of the
Republic of India."""


def test_ocr_text_is_parsed_but_flagged() -> None:
    m = parse_gazette([ORDINANCE_2015_OCR], url=gazette_pdf_url(2015, 166406), from_ocr=True)
    assert m.title == "THE ARBITRATION AND CONCILIATION (AMENDMENT) ORDINANCE, 2015"
    assert (m.number, m.year) == (9, 2015)  # "oF" read case-insensitively
    assert m.file_id == "166406"  # no Gazette ID on the masthead: taken from the URL
    assert m.part == "I"  # OCR misread of "PART II": reported as read, flagged from_ocr
    assert m.from_ocr


def test_principal_act_doc_type() -> None:
    text = ORDINANCE_2015_OCR.replace("THE ARBITRATION AND CONCILIATION\n(AMENDMENT) ORDINANCE",
                                      "THE ARBITRATION AND CONCILIATION ACT")
    assert parse_gazette([text]).doc_type is DocType.PRINCIPAL


def test_not_a_gazette_is_loud() -> None:
    with pytest.raises(LayoutChanged):
        parse_gazette(["Some unrelated PDF\nwith text"])


# ---- adapter ----------------------------------------------------------------------


def test_ref_requires_a_gazette_file_url() -> None:
    adapter = EgazetteAdapter()
    ref = adapter.ref(gazette_pdf_url(2020, 222941), "Ordinance 14 of 2020", DocType.ORDINANCE)
    assert ref.source is Source.EGAZETTE and ref.doc_type is DocType.ORDINANCE
    with pytest.raises(ValueError):
        adapter.ref("https://egazette.gov.in/SearchMinistry.aspx", "search page")
    with pytest.raises(NotImplementedError):
        adapter.parse("https://egazette.gov.in/", b"<html></html>")


class _Recorder:
    def record(self, url, status_code, error) -> None:
        pass


def test_describe_fetches_through_cache(tmp_path) -> None:
    calls = []
    pdf = (FIX / "egazette_act3_2021_p1.pdf").read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})

    cache = Cache(RawStore(tmp_path), MemoryGate(sleep=lambda s: None), _Recorder(),
                  user_agent="OpenSansad/test (contact: test@example.org)",
                  transport=httpx.MockTransport(handler), sleep=lambda s: None)
    url = url_for_gazette_id("CG-DL-E-11032021-225832")
    m = EgazetteAdapter().describe(cache, url, extract=lambda p: extract_pdf(p, ocr=no_ocr))
    assert calls == ["https://egazette.gov.in/robots.txt", url]
    assert (m.number, m.year) == (3, 2021)
