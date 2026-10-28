"""Checked transcriptions of scans whose text layer cannot be parsed as it is.

The base text of the 1996 Act is Bill XXX-C of 1995 as passed (a scan, see CLAUDE.md). The
library's OCR layer misreads the very tokens the clause parser splits on: italic markers
("(7)" for (1), "(0)" for (b)), Devanagari digits for letters, numbers that lose their
leading "1". Its pages also show the back of the sheet through the paper.

So the text is transcribed once, by hand, into data/transcriptions/{name}.txt, and that file
is what step 4 parses. The scan stays the source: clauses cite its documents row, and the
transcription records the raw file's sha256 so it is tied to exact bytes.

    draft   Tesseract on page images cleaned of show-through: a starting point to correct
            against the images, never used as it is
    check   each transcribed line against independent readings of the same text (witnesses:
            the library's OCR layers of both scans, a cleaned-OCR draft of the second scan, a
            typed copy). A line that no witness contains is printed for a second look

Format of a transcription: `[[page: N]]` (PDF page, 1-based) before each page's text;
margin notes as `[[margin: ...]]` before the paragraph they head, as the extractor writes
them; printed page numbers, running heads and margin line numbers left out.

    uv run python -m opensansad.parse.transcription draft FILE.pdf --pages 6-85
    uv run python -m opensansad.parse.transcription check NAME WITNESS.txt...
"""

import argparse
import difflib
import re
import sys
import tomllib
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

PAGE = re.compile(r"^\[\[page: (\d+)\]\]$")
MARGIN = re.compile(r"\[\[margin: [^\]]*\]\]")

# Show-through: the far side of the sheet prints as mid grey, the text itself near black.
INK, PAPER = 90, 150  # grey levels: at or below INK is text, at or above PAPER is blank


def clean_level(v: int) -> int:
    """One grey level after show-through removal: dark text kept, light bleed whitened."""
    if v <= INK:
        return 0
    if v >= PAPER:
        return 255
    return (v - INK) * 255 // (PAPER - INK)


def clean_scan(image):
    """A PIL image in grey, with show-through removed."""
    lut = [clean_level(v) for v in range(256)]
    return image.convert("L").point(lut)


_LINE_NO = re.compile(r"^(?:[1-9]\d?)\s+(?=\S)|\s+(?:[1-9]\d?)$")


def strip_line_number(line: str) -> str:
    """Drop a margin line number (5, 10, ... 60) printed before or after a line."""
    def keep(m: re.Match) -> str:
        n = int(m.group().strip())
        return "" if n % 5 == 0 and n <= 60 else m.group()
    return _LINE_NO.sub(keep, line)


def draft_page(page, dpi: int = 300) -> str:
    import pytesseract
    from PIL import Image

    pix = page.get_pixmap(dpi=dpi)
    image = clean_scan(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
    text = pytesseract.image_to_string(image, lang="eng", config="--psm 4")
    lines = [strip_line_number(ln.rstrip()) for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln.strip())


def draft(pdf: Path, pages: range) -> str:
    import pymupdf

    doc = pymupdf.open(pdf)
    return "\n".join(f"[[page: {n}]]\n{draft_page(doc[n - 1])}" for n in pages)


@dataclass
class Meta:
    """data/transcriptions/{name}.toml: what was transcribed, how, and how far it is checked."""

    name: str
    doc_url: str
    raw_sha256: str
    pages: str
    method: str
    verified: str  # "model" (read against page images by the assistant) or "human"
    witnesses: list[str] = field(default_factory=list)
    note: str = ""


def load_meta(path: Path) -> Meta:
    data = tomllib.loads(path.read_text())
    return Meta(name=path.stem, **data)


def body_lines(text: str) -> list[tuple[int, int, str]]:
    """(page, line number in the file, text) for every non-empty, non-marker line."""
    out, page = [], 0
    for i, line in enumerate(text.splitlines(), 1):
        if m := PAGE.match(line):
            page = int(m.group(1))
        elif line.strip() and not MARGIN.fullmatch(line.strip()):
            out.append((page, i, MARGIN.sub("", line).strip()))
    return out


_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "—": "-", "–": "-"})


def normalize(text: str) -> str:
    """For comparison only: case, spacing, quote and dash styles do not count as differences."""
    text = unicodedata.normalize("NFC", text).translate(_QUOTES).lower()
    return re.sub(r"\s+", "", MARGIN.sub("", text))


@dataclass
class Flag:
    page: int
    line_no: int
    text: str
    closest: dict[str, tuple[float, str]]  # witness -> (similarity, its closest line)


def check(text: str, witnesses: dict[str, str], *, min_len: int = 12) -> list[Flag]:
    """Transcribed lines that no witness contains (after `normalize`). Short lines ("(2)",
    "PART I") are skipped: a short string occurs somewhere by chance, so a hit proves nothing
    and a miss is noise."""
    flat = {name: normalize(w) for name, w in witnesses.items()}
    lines = {name: [ln for ln in w.splitlines() if ln.strip()] for name, w in witnesses.items()}
    flags = []
    for page, line_no, line in body_lines(text):
        key = normalize(line)
        if len(key) < min_len or any(key in f for f in flat.values()):
            continue
        closest = {}
        for name, wl in lines.items():
            best = max(((difflib.SequenceMatcher(None, key, normalize(w)).ratio(), w)
                        for w in difflib.get_close_matches(line, wl, n=3, cutoff=0.5)),
                       default=(0.0, ""))
            closest[name] = best
        flags.append(Flag(page, line_no, line, closest))
    return flags


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m opensansad.parse.transcription")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("draft", help="cleaned-image OCR draft of a scan")
    p.add_argument("pdf", type=Path)
    p.add_argument("--pages", required=True, help="PDF pages, 1-based, e.g. 6-85")
    p = sub.add_parser("check", help="lines of a transcription no witness contains")
    p.add_argument("transcription", type=Path)
    p.add_argument("witnesses", type=Path, nargs="+")
    args = parser.parse_args(argv)

    if args.cmd == "draft":
        first, _, last = args.pages.partition("-")
        print(draft(args.pdf, range(int(first), int(last or first) + 1)))
        return 0
    witnesses = {w.name: w.read_text() for w in args.witnesses}
    flags = check(args.transcription.read_text(), witnesses)
    for f in flags:
        print(f"p{f.page} L{f.line_no}: {f.text}")
        for name, (ratio, w) in f.closest.items():
            print(f"    {ratio:.2f} {name}: {w.strip()}")
    total = len(body_lines(args.transcription.read_text()))
    print(f"{len(flags)} of {total} lines in no witness", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
