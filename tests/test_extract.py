import json
import re
from pathlib import Path

import pymupdf
import pytest

from opensansad.db.models import DocFormat
from opensansad.extract.document import EXTRACTOR_VERSION
from opensansad.extract.html import extract_html
from opensansad.extract.ocr import tesseract_available
from opensansad.extract.pdf import extract_pdf, needs_ocr
from opensansad.extract.pipeline import extract_file, write_extraction

FIX = Path(__file__).parent / "fixtures"
BILL = FIX / "prs_arbitration_bill_2019.pdf"
GAZETTE = FIX / "gazette_arbitration_act_2019_p1-3.pdf"
PRS_HTML = FIX / "prs_billtrack_arbitration_2019.html"


def no_ocr(page, clip=None):
    raise AssertionError("OCR should not be needed")


# ---- real documents ------------------------------------------------------------


@pytest.fixture(scope="module")
def bill():
    return extract_pdf(BILL, ocr=no_ocr)


@pytest.fixture(scope="module")
def gazette():
    return extract_pdf(GAZETTE, ocr=no_ocr)


def test_bill_is_born_digital(bill) -> None:
    assert len(bill.pages) == 18
    assert not bill.is_scanned
    assert {p.method for p in bill.pages} == {"native"}
    assert bill.ocr_confidence is None


def test_bill_margin_notes_tagged_and_line_numbers_dropped(bill) -> None:
    en, _ = bill.joined("en")
    assert "[[margin: Short title and commencement.]]" in en
    assert "[[margin: Amendment of section 29A.]]" in en
    assert "[[margin: Insertion of new sections 42A and 42B.]]" in en
    assert not [ln for ln in en.split("\n") if ln.strip().isdigit()]


def test_margin_note_placed_before_its_paragraph(bill) -> None:
    page1 = bill.pages[0].en
    assert page1.index("[[margin: Amendment of section 2.]]") < page1.index(
        "2. In the Arbitration and Conciliation Act, 1996"
    )


def test_amending_text_structure_preserved(bill) -> None:
    en, _ = bill.joined("en")
    assert "‘(i) “prescribed” means prescribed by rules made under this Act;" in en
    assert "the following clauses shall be inserted, namely:—" in en
    assert "––" not in en and "namely: —" not in en
    assert "section 43-I" in en


def test_gazette_headers_removed(gazette) -> None:
    en, _ = gazette.joined("en")
    assert "THE GAZETTE OF INDIA EXTRAORDINARY" not in en
    assert "SEC. 1]" not in en and "[PART II—" not in en


def test_gazette_legacy_masthead_dropped_and_counted(gazette) -> None:
    assert gazette.pages[0].legacy_lines_dropped == 7
    assert sum(p.legacy_lines_dropped for p in gazette.pages[1:]) == 0
    page1 = gazette.pages[0].en
    assert "vlk/kkj.k" not in page1 and "Hkkx" not in page1
    assert "PUBLISHED BY AUTHORITY" in page1  # English masthead kept (provenance)
    assert "[9th August, 2019.]" in page1


def test_html_extraction_real_page() -> None:
    ex = extract_html(PRS_HTML.read_bytes())
    text = ex.pages[0].en
    assert ex.pages[0].method == "html"
    assert "The Arbitration and Conciliation (Amendment) Bill, 2019" in text
    assert "Jul 15, 2019" in text
    assert "<script" not in text and "function(" not in text


def test_html_selector_and_tables() -> None:
    html = b"""<html><body><nav>Menu</nav><div id="main"><h1>Title</h1>
    <table><tr><th>Introduced</th><td>Rajya Sabha</td><td>Jul 15, 2019</td></tr></table>
    <p>Para <b>one</b>.</p><script>var x=1;</script></div><div>Sidebar</div></body></html>"""
    text = extract_html(html, selector="#main").pages[0].en
    assert text == "Title\nIntroduced | Rajya Sabha | Jul 15, 2019\nPara one."


# ---- synthetic PDFs ---------------------------------------------------------------


def _text_page(doc: pymupdf.Document, text: str) -> None:
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(72, 72, 520, 770), text, fontsize=12)


def _image_only_page(doc: pymupdf.Document, text: str) -> None:
    """A 'scanned' page: text rendered to an image, no text layer."""
    tmp = pymupdf.open()
    _text_page(tmp, text)
    pix = tmp[0].get_pixmap(dpi=200)
    page = doc.new_page()
    page.insert_image(page.rect, pixmap=pix)


SCANNED_TEXT = (
    "The Arbitration and Conciliation Act provides for the settlement of disputes "
    "by an arbitral tribunal appointed by the parties."
)


def test_needs_ocr_rules() -> None:
    assert needs_ocr("", image_coverage=1.0)
    assert not needs_ocr("", image_coverage=0.0)  # genuinely blank page
    assert not needs_ocr("This Act may be called the Arbitration Act " * 3, image_coverage=0.0)
    gibberish = "bl Hkkx esa fHkUu i`\"B la[;k nh tkrh gS ftlls fd ;g vyx ladyu ds :i " * 3
    assert needs_ocr(gibberish, image_coverage=0.0)


def test_mixed_document_ocr_only_where_needed(tmp_path: Path) -> None:
    doc = pymupdf.open()
    _text_page(doc, "1. Short title.\nThis Act may be called the Test Act, 2026.")
    _image_only_page(doc, SCANNED_TEXT)
    _image_only_page(doc, SCANNED_TEXT)
    path = tmp_path / "mixed.pdf"
    doc.save(path)

    calls = []

    def fake_ocr(page, clip):
        calls.append(page.number)
        return "OCR TEXT for page", 0.8

    ex = extract_pdf(path, ocr=fake_ocr)
    assert calls == [1, 2]
    assert [p.method for p in ex.pages] == ["native", "ocr", "ocr"]
    assert ex.is_scanned  # 2 of 3 pages
    assert ex.ocr_confidence == pytest.approx(0.8)
    assert "This Act may be called the Test Act, 2026." in ex.pages[0].en


@pytest.mark.skipif(not tesseract_available(), reason="tesseract binary not installed")
def test_real_ocr_on_image_page(tmp_path: Path) -> None:
    doc = pymupdf.open()
    _image_only_page(doc, SCANNED_TEXT)
    path = tmp_path / "scan.pdf"
    doc.save(path)

    ex = extract_pdf(path)
    page = ex.pages[0]
    assert page.method == "ocr"
    assert page.ocr_confidence > 0.7
    words = set(re.findall(r"[a-z]+", page.en.lower()))
    assert {"arbitration", "conciliation", "tribunal", "disputes"} <= words


# ---- pipeline output ------------------------------------------------------------


def test_write_extraction_mirrors_raw_layout(tmp_path: Path) -> None:
    raw_root, text_root = tmp_path / "raw", tmp_path / "text"
    raw = raw_root / "prs" / "abc123" / "20261020T151529Z_4a5279e8bb18.pdf"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(GAZETTE.read_bytes())

    ex = extract_file(raw, DocFormat.PDF, ocr=no_ocr)
    en_path = write_extraction(ex, raw, raw_root, text_root)

    base = text_root / "prs" / "abc123" / "20261020T151529Z_4a5279e8bb18"
    assert en_path == base.with_name(base.name + ".en.txt")
    assert not base.with_name(base.name + ".hi.txt").exists()  # no Unicode Hindi here
    sidecar = json.loads(base.with_name(base.name + ".json").read_text())
    assert sidecar["extractor_version"] == EXTRACTOR_VERSION
    assert sidecar["raw_path"] == "prs/abc123/20261020T151529Z_4a5279e8bb18.pdf"
    assert sidecar["is_scanned"] is False
    assert sidecar["pages"][0]["legacy_lines_dropped"] == 7

    # Offsets map every page back into the joined text exactly.
    text = en_path.read_text(encoding="utf-8")
    for page, meta in zip(ex.pages, sidecar["pages"], strict=True):
        start, end = meta["en"]
        assert text[start:end] == page.en
    assert raw.read_bytes() == GAZETTE.read_bytes()  # raw untouched


def test_hindi_written_separately(tmp_path: Path) -> None:
    raw_root, text_root = tmp_path / "raw", tmp_path / "text"
    raw = raw_root / "sansad" / "h1" / "v1.html"
    raw.parent.mkdir(parents=True)
    raw.write_bytes("<html><body><p>Arbitration Act</p><p>मध्यस्थम् अधिनियम</p></body></html>"
                    .encode())
    en_path = write_extraction(extract_file(raw, DocFormat.HTML), raw, raw_root, text_root)
    assert en_path.read_text() == "Arbitration Act"
    assert (text_root / "sansad" / "h1" / "v1.hi.txt").read_text() == "मध्यस्थम् अधिनियम"


def test_unsupported_format_raises(tmp_path: Path) -> None:
    f = tmp_path / "x.bin"
    f.write_bytes(b"\x00")
    with pytest.raises(ValueError):
        extract_file(f, DocFormat.OTHER)


# ---- review fixes -------------------------------------------------------------------

LEGACY_PAGE = [
    "vf/kfu;e ljdkj dk vkns'k",
    "bl Hkkx esa fHkUu i`\"B la[;k nh tkrh gS",
    "ftlls fd ;g vyx ladyu ds :i esa j[kk tk ldsA",
    "izkf/kdkj ls izdkf'kr",
]


def test_legacy_font_hindi_page_is_not_ocrd(tmp_path: Path) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    for i, line in enumerate(LEGACY_PAGE * 3):
        page.insert_text((72, 80 + 16 * i), line, fontsize=11)
    path = tmp_path / "hindi.pdf"
    doc.save(path)

    ex = extract_pdf(path, ocr=no_ocr)  # would raise if OCR were attempted
    assert ex.pages[0].method == "native"
    assert not ex.is_scanned
    assert ex.pages[0].en == ""
    assert ex.pages[0].legacy_lines_dropped == 12


def test_margin_note_anchors_to_line_inside_merged_block(tmp_path: Path) -> None:
    """Two sections in ONE PyMuPDF block: each note must precede its own section line."""
    body = ["1. This Act may be called the Test Act, 2026, and it shall come",
            "into force on such date as the Central Government may appoint.",
            "2. In this Act, unless the context otherwise requires, the word",
            "arbitration means any arbitration whether or not administered."]
    doc = pymupdf.open()
    page = doc.new_page()
    for i, line in enumerate(body):
        page.insert_text((120, 100 + 14 * i), line, fontsize=10)
    page.insert_text((20, 100), "Short title.", fontsize=8)
    page.insert_text((20, 128), "Definitions.", fontsize=8)
    path = tmp_path / "merged.pdf"
    doc.save(path)
    with pymupdf.open(path) as d:  # precondition: the body really is one block
        assert sum(1 for b in d[0].get_text("dict")["blocks"]
                   if b["bbox"][0] > 100) == 1

    lines = extract_pdf(path, ocr=no_ocr).pages[0].en.split("\n")
    assert lines.index("[[margin: Definitions.]]") + 1 == lines.index(body[2])
    assert lines.index("[[margin: Short title.]]") + 1 == lines.index(body[0])


def test_script_split_is_per_line(monkeypatch, tmp_path: Path) -> None:
    import opensansad.extract.pdf as pdf

    mixed = ["1. Short title.", "मध्यस्थम् और सुलह (संशोधन) अधिनियम", "[[margin: धारा 2 का संशोधन]]",
             "This Act may be called the Test Act."]
    monkeypatch.setattr(pdf, "_native_lines", lambda page: list(enumerate(mixed)))
    doc = pymupdf.open()
    doc.new_page()
    path = tmp_path / "x.pdf"
    doc.save(path)

    page = extract_pdf(path, ocr=no_ocr).pages[0]
    assert page.en == "1. Short title.\nThis Act may be called the Test Act."
    assert page.hi == "मध्यस्थम् और सुलह (संशोधन) अधिनियम\n[[margin: धारा 2 का संशोधन]]"


def test_html_sup_footnotes_become_markers() -> None:
    html = (b"<p>29A.<sup>1</sup>[Time limit for arbitral award] made on the 9<sup>th</sup> day "
            b"<sup>*</sup>.</p>")
    assert extract_html(html).pages[0].en == (
        "29A.[^1][Time limit for arbitral award] made on the 9th day [^*]."
    )


def test_sidecar_records_text_hashes_and_no_temp_files_left(tmp_path: Path) -> None:
    import hashlib

    raw_root, text_root = tmp_path / "raw", tmp_path / "text"
    raw = raw_root / "prs" / "h" / "v1.html"
    raw.parent.mkdir(parents=True)
    raw.write_bytes("<p>Arbitration Act</p><p>मध्यस्थम् अधिनियम</p>".encode())
    en_path = write_extraction(extract_file(raw, DocFormat.HTML), raw, raw_root, text_root)

    sidecar = json.loads(en_path.with_name("v1.json").read_text())
    assert sidecar["en_sha256"] == hashlib.sha256(en_path.read_bytes()).hexdigest()
    hi_bytes = en_path.with_name("v1.hi.txt").read_bytes()
    assert sidecar["hi_sha256"] == hashlib.sha256(hi_bytes).hexdigest()
    assert sorted(p.name for p in en_path.parent.iterdir()) == ["v1.en.txt", "v1.hi.txt",
                                                                "v1.json"]

    # Re-extraction without Hindi removes the stale .hi.txt and nulls its hash.
    raw.write_bytes(b"<p>Arbitration Act</p>")
    write_extraction(extract_file(raw, DocFormat.HTML), raw, raw_root, text_root)
    assert not en_path.with_name("v1.hi.txt").exists()
    assert json.loads(en_path.with_name("v1.json").read_text())["hi_sha256"] is None


def test_image_body_under_text_masthead_is_ocrd_in_place(tmp_path: Path) -> None:
    """Corrigendum layout: masthead and signature are text, the body is a pasted picture.

    Real case (e-Gazette 2020/222961): body image 11.3% of the page; the masthead graphic,
    7.7%, stays below MIN_REGION_AREA and is not OCR'd."""
    tmp = pymupdf.open()
    _text_page(tmp, SCANNED_TEXT)
    pix = tmp[0].get_pixmap(dpi=100, clip=pymupdf.Rect(72, 72, 520, 300))
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(72, 60, 520, 120),
                        "MINISTRY OF LAW AND JUSTICE\nCORRIGENDUM\n"
                        "New Delhi, the 6th November, 2020",
                        fontsize=11)
    page.insert_image(pymupdf.Rect(72, 200, 520, 420), pixmap=pix)
    page.insert_text((72, 500), "Secretary to the Government of India.", fontsize=11)
    path = tmp_path / "corr.pdf"
    doc.save(path)

    clips = []

    def fake_ocr(page, clip):
        clips.append(clip)
        return "In the Arbitration Ordinance, for Subsitution read Substitution.", 0.9

    p = extract_pdf(path, ocr=fake_ocr).pages[0]
    assert len(clips) == 1 and clips[0] is not None and clips[0].y0 == pytest.approx(200, abs=1)
    assert p.method == "mixed" and p.ocr_confidence == pytest.approx(0.9)
    lines = p.en.split("\n")
    body = lines.index("In the Arbitration Ordinance, for Subsitution read Substitution.")
    assert lines.index("CORRIGENDUM") < body < lines.index("Secretary to the Government of India.")


def test_small_images_are_not_ocrd(tmp_path: Path) -> None:
    """A seal or emblem (< MIN_REGION_AREA of the page) is not a text region."""
    tmp = pymupdf.open()
    _text_page(tmp, "x")
    pix = tmp[0].get_pixmap(dpi=20)
    doc = pymupdf.open()
    _text_page(doc, "1. Short title.\nThis Act may be called the Test Act, 2026. " * 3)
    doc[0].insert_image(pymupdf.Rect(20, 20, 80, 80), pixmap=pix)
    path = tmp_path / "seal.pdf"
    doc.save(path)
    assert extract_pdf(path, ocr=no_ocr).pages[0].method == "native"


def test_letterless_lines_follow_the_previous_lines_script() -> None:
    from opensansad.extract.pdf import _split_scripts

    lines = ["(1)", "का.आ.", "3154", "(अ).—केन्द्रीय सरकार", "S.O. 3154(E).—In exercise of", "(1)",
             "of section 1"]
    en, hi = _split_scripts(lines)
    assert hi == ["(1)", "का.आ.", "3154", "(अ).—केन्द्रीय सरकार"]  # leading "(1)": first script
    assert en == ["S.O. 3154(E).—In exercise of", "(1)", "of section 1"]
    assert _split_scripts(["12", "(3)"]) == (["12", "(3)"], [])  # no letters anywhere: English
