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
            the library's OCR layers of both scans, cleaned-OCR drafts of both, a typed copy).
            A line that no witness contains is printed for a second look, unless it is listed
            in {name}.accepted.txt (re-read on the image and found right). Also checks that
            parts, chapters, sections, sub-sections, clauses and articles number in sequence:
            those markers are what the clause parser splits on, and short lines escape the
            witness check

Format of a transcription: `[[page: N]]` (PDF page, 1-based) before each page's text;
margin notes as `[[margin: ...]]` before the paragraph they head, as the extractor writes
them; `[[flush]]` before text set full out after a list (it belongs to the provision that
opened the list, not to the last item: "(b) ...,\n[[flush]]\nthat agreement shall ...");
printed page numbers, running heads and margin line numbers left out.

    uv run python -m opensansad.parse.transcription draft FILE.pdf --pages 6-85
    uv run python -m opensansad.parse.transcription check NAME.txt [WITNESS.txt...]

Without WITNESS arguments, `check` reads them from NAME.toml (paths relative to the repo root).
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
FLUSH = "[[flush]]"

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
    witnesses: list[str] = field(default_factory=list)  # paths relative to the repo root
    tools: str = ""  # versions of the programs that made the drafts
    note: str = ""
    version_label: str = ""  # the clause-tree version this text is (parse/clause_tree.py)


def load_meta(path: Path) -> Meta:
    data = tomllib.loads(path.read_text())
    return Meta(name=path.stem, **data)


def body_lines(text: str) -> list[tuple[int, int, str]]:
    """(page, line number in the file, text) for every non-empty, non-marker line."""
    out, page = [], 0
    for i, line in enumerate(text.splitlines(), 1):
        if m := PAGE.match(line):
            page = int(m.group(1))
        elif line.strip() and line.strip() != FLUSH and not MARGIN.fullmatch(line.strip()):
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


def load_accepted(path: Path) -> set[str]:
    """{name}.accepted.txt: one transcribed line per row, exactly as in the transcription;
    blank rows and rows starting with "#" are comments."""
    if not path.exists():
        return set()
    return {ln.strip() for ln in path.read_text().splitlines()
            if ln.strip() and not ln.startswith("#")}


def check(text: str, witnesses: dict[str, str], *, min_len: int = 12,
          accepted: set[str] = frozenset()) -> list[Flag]:
    """Transcribed lines that no witness contains (after `normalize`), except `accepted` ones.
    Short lines ("(2)", "PART I") are skipped: a short string occurs somewhere by chance, so a
    hit proves nothing and a miss is noise (`numbering` covers the markers among them)."""
    flat = {name: normalize(w) for name, w in witnesses.items()}
    lines = {name: [ln for ln in w.splitlines() if ln.strip()] for name, w in witnesses.items()}
    flags = []
    for page, line_no, line in body_lines(text):
        key = normalize(line)
        if line in accepted or len(key) < min_len or any(key in f for f in flat.values()):
            continue
        closest = {}
        for name, wl in lines.items():
            best = max(((difflib.SequenceMatcher(None, key, normalize(w)).ratio(), w)
                        for w in difflib.get_close_matches(line, wl, n=3, cutoff=0.5)),
                       default=(0.0, ""))
            closest[name] = best
        flags.append(Flag(page, line_no, line, closest))
    return flags


ROMAN = {r: i for i, r in enumerate(
    "I II III IV V VI VII VIII IX X XI XII XIII XIV XV XVI XVII XVIII XIX XX".split(), 1)}
_HEADING = re.compile(r"^(PART|CHAPTER|ARTICLE) (\S+)$")  # a misread numeral is reported
_SCHEDULE = re.compile(r"^THE \w+ SCHEDULE$")
_SECTION = re.compile(r"^(\d+)\.(?:\s|$)")
_ARTICLE = re.compile(r"^Article (\d+)\.")
_SUB = re.compile(r"^(?:\d+\.\s+)?\((\d+)\)")
_CLAUSE = re.compile(r"^\(([a-z])\)")
_SUBCLAUSE = re.compile(r"^\(([ivx]+)\)")
_SUBCLAUSES = "i ii iii iv v vi vii viii ix x xi xii".split()
# A line that starts with a marker after one of these words continues a cross-reference
# ("... clause (a) of sub-section" / "(2) of section 32"), it does not open a provision.
_REFERENCE_END = re.compile(
    r"(?:\b(?:sub-sections?|sections?|clauses?|sub-clauses?|articles?|paragraphs?"
    r"|of|to|in|under)|\)\s+(?:and|or))$")  # not "...; or", which ends a list item


def numbering(text: str) -> list[str]:
    """Breaks in the numbering of the markers a clause parser splits on, as "pN LM: ..." lines.

    PART, CHAPTER (restarting in each PART), sections (1, 2, ... across the Act), ARTICLE and
    "Article N." (restarting in each Schedule), sub-sections "(n)" and Schedule paragraphs "n."
    (restarting in each section or article), clauses "(a)" (restarting with each sub-section,
    section or article; a clause may also open at "(a)" anywhere, as lists in a proviso or an
    Explanation do) and sub-clauses "(i)" (restarting with each clause). "(i)" after "(h)" is a
    clause. A line's first marker counts, and a clause right after a sub-section ("(12) (a)");
    a line continuing a cross-reference (see `_REFERENCE_END`) is skipped."""
    out: list[str] = []
    last = {"PART": 0, "CHAPTER": 0, "ARTICLE": 0, "section": 0, "sub": 0, "clause": "",
            "subclause": 0}
    in_schedule = False
    previous = ""

    def bad(page: int, line_no: int, what: str, got: str, want: str) -> None:
        out.append(f"p{page} L{line_no}: {what} {got} after {want}")

    for page, line_no, line in body_lines(text):
        continues, previous = bool(_REFERENCE_END.search(previous)), line
        if continues:
            continue
        if _SCHEDULE.match(line):
            in_schedule = True
            last.update(ARTICLE=0, section=0, sub=0, clause="", subclause=0)
            continue
        if m := _HEADING.match(line):
            kind, n = m.group(1), ROMAN.get(m.group(2), 0)
            if n != last[kind] + 1:
                bad(page, line_no, kind, m.group(2), str(last[kind]))
            last[kind] = n
            if kind == "PART":
                last["CHAPTER"] = 0
            if kind == "ARTICLE":
                last.update(sub=0, clause="", subclause=0)
            continue
        m = (_ARTICLE if in_schedule else _SECTION).match(line)
        if m and not (in_schedule and _SECTION.match(line) and not _ARTICLE.match(line)):
            n = int(m.group(1))
            if n != last["section"] + 1:
                bad(page, line_no, "Article" if in_schedule else "section", str(n),
                    str(last["section"]))
            last.update(section=n, sub=0, clause="", subclause=0)
        elif in_schedule and (m := _SECTION.match(line)):  # numbered paragraph of an article
            n = int(m.group(1))
            if n != last["sub"] + 1:
                bad(page, line_no, "paragraph", str(n), str(last["sub"]))
            last.update(sub=n, clause="", subclause=0)
        if not in_schedule and (m := _SUB.match(line)):
            n = int(m.group(1))
            if n != last["sub"] + 1:
                bad(page, line_no, "sub-section", f"({n})", f"({last['sub']})")
            last.update(sub=n, clause="", subclause=0)
            if c := _CLAUSE.match(line[m.end():].lstrip()):
                if c.group(1) != "a":
                    bad(page, line_no, "clause", f"({c.group(1)})", "()")
                last["clause"] = c.group(1)
            continue
        if (m := _SUBCLAUSE.match(line)) and not (m.group(1) == "i" and last["clause"] == "h"):
            n = _SUBCLAUSES.index(m.group(1)) + 1 if m.group(1) in _SUBCLAUSES else 0
            if n != last["subclause"] + 1 and n != 1:
                bad(page, line_no, "sub-clause", f"({m.group(1)})",
                    f"({_SUBCLAUSES[last['subclause'] - 1] if last['subclause'] else ''})")
            last["subclause"] = n
            continue
        if m := _CLAUSE.match(line):
            c = m.group(1)
            want = chr(ord(last["clause"]) + 1) if last["clause"] else "a"
            if c != want and c != "a":
                bad(page, line_no, "clause", f"({c})", f"({last['clause']})")
            last.update(clause=c, subclause=0)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m opensansad.parse.transcription")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("draft", help="cleaned-image OCR draft of a scan")
    p.add_argument("pdf", type=Path)
    p.add_argument("--pages", required=True, help="PDF pages, 1-based, e.g. 6-85")
    p = sub.add_parser("check", help="lines of a transcription no witness contains")
    p.add_argument("transcription", type=Path)
    p.add_argument("witnesses", type=Path, nargs="*")
    args = parser.parse_args(argv)

    if args.cmd == "draft":
        first, _, last = args.pages.partition("-")
        print(draft(args.pdf, range(int(first), int(last or first) + 1)))
        return 0
    from opensansad.config import PROJECT_ROOT

    paths = args.witnesses or [
        PROJECT_ROOT / w for w in load_meta(args.transcription.with_suffix(".toml")).witnesses]
    witnesses = {w.name: w.read_text() for w in paths}
    text = args.transcription.read_text()
    accepted = load_accepted(args.transcription.with_suffix(".accepted.txt"))
    flags = check(text, witnesses, accepted=accepted)
    for f in flags:
        print(f"p{f.page} L{f.line_no}: {f.text}")
        for name, (ratio, w) in f.closest.items():
            print(f"    {ratio:.2f} {name}: {w.strip()}")
    present = {line for _, _, line in body_lines(text)}
    stale = sorted(accepted - present)
    for line in stale:
        print(f"accepted but not in the transcription: {line}")
    breaks = numbering(text)
    for b in breaks:
        print(f"numbering: {b}")
    total = len(body_lines(text))
    print(f"{len(flags)} of {total} lines in no witness ({len(accepted)} accepted), "
          f"{len(stale)} stale accepted, {len(breaks)} numbering breaks", file=sys.stderr)
    return 1 if flags or stale or breaks else 0


if __name__ == "__main__":
    sys.exit(main())
