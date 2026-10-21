"""PDF text extraction: native text layer per page, OCR where the layer is missing or garbage."""

from collections.abc import Callable
from pathlib import Path

import pymupdf

from opensansad.extract.document import Extraction, Page
from opensansad.extract.normalize import (
    LINE_BREAK_HYPHEN,
    document_vocab,
    footnote_marker,
    is_marker_text,
    looks_legacy_hindi,
    normalize_text,
    script_of,
    strip_running_lines,
    wordlike_ratio,
)

MIN_NATIVE_CHARS = 40  # fewer non-space chars than this on an image-covered page -> scanned
MIN_IMAGE_COVERAGE = 0.5
MIN_WORDLIKE = 0.6  # below this the text layer is treated as garbage (legacy-font Hindi etc.)
_SUPERSCRIPT_FLAG = 1  # PyMuPDF span flag bit

OcrFn = Callable[[pymupdf.Page], tuple[str, float]]


def _span_text(span: dict) -> str:
    text = span["text"]
    # Raised small digits are footnote markers (India Code's "¹[...]" amendment notes).
    # Other superscripts (e.g. the "th" in "9th") stay ordinary text.
    if span["flags"] & _SUPERSCRIPT_FLAG and is_marker_text(text.strip()):
        return footnote_marker(text.strip())
    return text


_MARGIN_PREFIX = "[[margin: "


def margin_note(lines: list[str]) -> str:
    """One [[margin: ...]] line; a hyphen ending a wrapped line is marked for normalize."""
    text = ""
    for line in (" ".join(ln.split()) for ln in lines):
        if text.endswith("-"):
            text = text[:-1] + LINE_BREAK_HYPHEN + line
        else:
            text = f"{text} {line}" if text else line
    return f"{_MARGIN_PREFIX}{text}]]"


def _line_script(line: str) -> str:
    """Script of a line, judged on a margin note's content rather than its wrapper."""
    if line.startswith(_MARGIN_PREFIX):
        line = line[len(_MARGIN_PREFIX):-2]
    return script_of(line)


def _native_lines(page: pymupdf.Page) -> list[str]:
    """Text lines in reading order, with margin material separated from the body.

    Gazette acts and bills print notes in the margin ("Amendment of section 2.",
    "26 of 1996.") and bills print line numbers there. The body column is inferred from
    wide lines; anything wholly outside it is margin. Bare numbers are line numbers and
    are dropped; notes are kept as [[margin: ...]] lines placed just before the body
    line they are top-aligned with.
    """
    # Classify LINES, not blocks: PyMuPDF can merge a left-margin note and a right-margin
    # note at the same height into one page-wide block.
    width = page.rect.width
    lines = []  # (block index, rect, text)
    for bi, block in enumerate(page.get_text("dict")["blocks"]):
        if block.get("type") != 0:  # 0 = text, 1 = image
            continue
        for line in block["lines"]:
            text = "".join(_span_text(s) for s in line["spans"])
            if text.strip():
                lines.append((bi, pymupdf.Rect(line["bbox"]), text))

    wide = [r for _, r, _ in lines if r.width > 0.4 * width]
    if not wide:
        left, right = float("-inf"), float("inf")
    else:
        left, right = min(r.x0 for r in wide), max(r.x1 for r in wide)

    def side(r: pymupdf.Rect) -> str:
        return "L" if r.x1 < left else "R" if r.x0 > right else "body"

    # Group consecutive lines of the same block and side.
    groups: list[tuple[str, pymupdf.Rect, list[tuple[pymupdf.Rect, str]]]] = []
    prev = None
    for bi, r, text in lines:
        key = (bi, side(r))
        if key == prev:
            groups[-1][1].include_rect(r)
            groups[-1][2].append((r, text))
        else:
            groups.append((key[1], pymupdf.Rect(r), [(r, text)]))
        prev = key

    # Sort key (group y0, group x0, line index, 0 = note / 1 = body, note x0): body lines
    # keep block reading order and each note sorts just before the line it anchors to.
    keyed: list[tuple[tuple, str]] = []
    body_lines: list[tuple[float, tuple]] = []  # (line y0, sort key)
    for s, g, members in groups:
        if s == "body":
            for i, (r, text) in enumerate(members):
                key = (g.y0, g.x0, i, 1, 0.0)
                keyed.append((key, text))
                body_lines.append((r.y0, key))
    for s, g, members in groups:
        if s == "body":
            continue
        texts = [text for _, text in members]
        if " ".join(texts).strip().isdigit():
            continue  # margin line number
        # Anchor to the body LINE nearest the note's top, not the enclosing block: PyMuPDF
        # can merge two sections into one block, which would hoist the second note.
        if body_lines:
            _, anchor = min(body_lines, key=lambda b: abs(b[0] - g.y0))
            key = (*anchor[:3], 0, g.x0)
        else:
            key = (g.y0, g.x0, 0, 0, g.x0)
        keyed.append((key, margin_note(texts)))
    return [t for _, t in sorted(keyed, key=lambda kt: kt[0])]


def _image_coverage(page: pymupdf.Page) -> float:
    area = page.rect.width * page.rect.height or 1
    covered = 0.0
    for img in page.get_images(full=True):
        for rect in page.get_image_rects(img[0]):
            covered += (rect & page.rect).get_area()
    return min(1.0, covered / area)


def needs_ocr(native_text: str, image_coverage: float) -> bool:
    chars = len("".join(native_text.split()))
    if chars < MIN_NATIVE_CHARS:
        return image_coverage >= MIN_IMAGE_COVERAGE
    return wordlike_ratio(native_text) < MIN_WORDLIKE


def default_ocr(page: pymupdf.Page) -> tuple[str, float]:
    from opensansad.extract.ocr import ocr_page

    return ocr_page(page)


def extract_pdf(path: Path, *, ocr: OcrFn | None = default_ocr) -> Extraction:
    """Extract every page. Pass ocr=None to skip OCR (pages that need it come back empty)."""
    raw_pages: list[tuple[list[str], list[str]]] = []  # (english lines, hindi lines)
    meta: list[dict] = []
    with pymupdf.open(path) as doc:
        for page in doc:
            lines = _native_lines(page)
            ratio = wordlike_ratio("\n".join(lines))
            # Legacy-font Hindi is dropped BEFORE the OCR decision: a page of Kruti Dev text
            # is a real text layer, not a failed one, and must not be OCR'd as English.
            legacy = [ln for ln in lines if looks_legacy_hindi(ln)]
            lines = [ln for ln in lines if not looks_legacy_hindi(ln)]
            if needs_ocr("\n".join(lines), _image_coverage(page)):
                text, conf = ocr(page) if ocr else ("", None)
                lines, method = text.split("\n"), "ocr"
            else:
                conf, method = None, "native"
            en_lines = [ln for ln in lines if _line_script(ln) == "en"]
            hi_lines = [ln for ln in lines if _line_script(ln) == "hi"]
            raw_pages.append((en_lines, hi_lines))
            meta.append({"method": method, "conf": conf, "ratio": ratio, "legacy": len(legacy)})

    en_pages = strip_running_lines([en for en, _ in raw_pages])
    hi_pages = strip_running_lines([hi for _, hi in raw_pages])
    vocab = document_vocab(["\n".join(en) for en in en_pages])
    pages = [
        Page(
            number=i + 1,
            method=m["method"],
            en=normalize_text("\n".join(en), vocab),
            hi=normalize_text("\n".join(hi)),
            ocr_confidence=m["conf"],
            wordlike_ratio=round(m["ratio"], 3),
            legacy_lines_dropped=m["legacy"],
        )
        for i, (en, hi, m) in enumerate(zip(en_pages, hi_pages, meta, strict=True))
    ]
    return Extraction(pages=pages)
