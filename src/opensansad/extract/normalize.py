"""Text normalisation tuned for Indian statutory text.

Deliberately conservative: anything that could change a clause number, a quotation
boundary or a structural dash is left alone. In particular:
- no NFKC (it would turn the footnote marker in "29A¹" into "29A1"),
- typographic quotes are kept as-is (open/close tells the parser where nested
  quotations in amending acts begin and end),
- a hyphen at a line end is resolved against the document's own vocabulary, and kept
  when in doubt after legal prefixes ("sub-\\nsection" -> "sub-section", never "subsection").
"""

import math
import re
import unicodedata
from collections import Counter

_LIGATURES = str.maketrans({"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"})
# Rare quote variants mapped to the standard curly pair; ‘ ’ “ ” themselves are untouched.
_QUOTE_VARIANTS = str.maketrans({"‚": "‘", "‛": "‘", "„": "“", "‟": "“"})
_SPACES = str.maketrans({" ": " ", " ": " ", " ": " ", "\t": " "})

_SUPERSCRIPT_DIGITS = "⁰¹²³⁴⁵⁶⁷⁸⁹"
_SUPERSCRIPT_RUN = re.compile(f"[{_SUPERSCRIPT_DIGITS}]+")
_SUP_TO_DIGIT = str.maketrans(_SUPERSCRIPT_DIGITS, "0123456789")

# Two or more dash-like characters ("––", "—–", "--", "————") are one structural em dash.
# A single en dash is kept: it is used in ranges such as "sections 43A–43M".
_DASH_RUN = re.compile(r"[‒–—―-]{2,}")
_HORIZONTAL_BAR = re.compile("―")


_MARKER_TEXT = re.compile(r"^[\d*†‡]+$")


def footnote_marker(text: str) -> str:
    return f"[^{text}]"


def is_marker_text(text: str) -> bool:
    """Digits / * / † / ‡ only: what a raised footnote reference looks like."""
    return bool(_MARKER_TEXT.match(text))


def normalize_text(text: str, vocab: frozenset[str] = frozenset()) -> str:
    text = unicodedata.normalize("NFC", text)
    text = text.translate(_LIGATURES).translate(_QUOTE_VARIANTS).translate(_SPACES)
    text = text.replace("­", "")  # soft hyphen: invisible, never meaningful
    text = _SUPERSCRIPT_RUN.sub(lambda m: footnote_marker(m.group().translate(_SUP_TO_DIGIT)), text)
    text = _DASH_RUN.sub("—", text)
    text = _HORIZONTAL_BAR.sub("—", text)
    text = re.sub(r":[ ]+—", ":—", text)  # "namely: ––" and "namely:—" are the same thing
    text = join_hyphenated_lines(text, vocab)
    lines = [re.sub(r" {2,}", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


# Marks a line-end hyphen inside text already joined onto one line (wrapped margin notes).
LINE_BREAK_HYPHEN = "-\u2063"
_HYPHEN_BREAK = re.compile(r"([A-Za-z]+)(?:-[ ]*\n[ ]*|-\u2063)([a-z]+)")
# Prefixes that form real hyphenated compounds in statutes. Keeping the hyphen after these
# protects clause references ("sub-\nsection"); a merged "subsection" would never match.
_KEEP_PREFIXES = {"sub", "non", "co", "inter", "intra", "pre", "post", "self", "quasi", "ex",
                  "vice", "counter", "cross", "multi", "semi", "anti", "full", "part"}
_WORD = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)*")


def document_vocab(texts: list[str]) -> frozenset[str]:
    """Lower-cased words (hyphenated ones whole) seen anywhere in a document."""
    return frozenset(w.lower() for t in texts for w in _WORD.findall(t))


def join_hyphenated_lines(text: str, vocab: frozenset[str] = frozenset()) -> str:
    """Join a word broken across lines, deciding whether its hyphen is real.

    The document is its own dictionary: 'subject-\\nmatter' keeps its hyphen when
    'subject-matter' occurs elsewhere, 'arbi-\\ntration' loses it when 'arbitration' does.
    Undecided cases keep the hyphen only after a known prefix (_KEEP_PREFIXES).
    """
    def join(m: re.Match) -> str:
        left, right = m.group(1), m.group(2)
        if f"{left}-{right}".lower() in vocab:
            return f"{left}-{right}"
        if f"{left}{right}".lower() in vocab:
            return left + right
        return f"{left}-{right}" if left.lower() in _KEEP_PREFIXES else left + right

    text = _HYPHEN_BREAK.sub(join, text)
    return text.replace(LINE_BREAK_HYPHEN, "-")  # unresolved marks (e.g. "(a)-(b)")


# ---- running headers / footers -------------------------------------------------

_PAGE_NUMBER = re.compile(r"^[\[(\-–—\s]*\d{1,4}[\])\-–—\s]*$")
# Gazette of India running heads ("[PART II—" / "SEC. 1]"). Short acts are 2–4 pages, so
# each alternating head may appear only once and never reach the repeat threshold.
# Bracketed forms only: the page-1 masthead "PART II — Section 1" is not matched.
_GAZETTE_HEAD = re.compile(r"^(?:\[PART [IVX]+\s*[—–-]*|SEC\.\s*\d+\s*\])$")
ZONE = 3  # lines at the top and bottom of a page considered header / footer


def _key(line: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", line.strip().lower()))


def strip_running_lines(pages: list[list[str]]) -> list[list[str]]:
    """Remove page numbers and lines repeated in the header/footer zone of many pages.

    The threshold is 30% of pages (min 2), not a majority: gazettes alternate headers
    ("[PART II—" on even pages, "SEC. 1]" on odd ones), so each appears on ~half.
    """
    counts: Counter[str] = Counter()
    for lines in pages:
        counts.update({_key(ln) for ln in _zone(lines) if ln.strip()})
    threshold = max(2, math.ceil(0.3 * len(pages)))
    running = {k for k, c in counts.items() if c >= threshold}

    cleaned = []
    for lines in pages:
        zone_idx = _zone_indices(lines)
        cleaned.append([
            ln for i, ln in enumerate(lines)
            if not (i in zone_idx and (_key(ln) in running or _PAGE_NUMBER.match(ln)
                                       or _GAZETTE_HEAD.match(ln.strip())))
        ])
    return cleaned


def _zone_indices(lines: list[str]) -> set[int]:
    non_empty = [i for i, ln in enumerate(lines) if ln.strip()]
    return set(non_empty[:ZONE]) | set(non_empty[-ZONE:])


def _zone(lines: list[str]) -> list[str]:
    return [lines[i] for i in sorted(_zone_indices(lines))]


# ---- script / garbage detection -------------------------------------------------

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_LATIN = re.compile(r"[A-Za-z]")
_WORDLIKE = re.compile(r"^(?:[A-Z]?[a-z]+|[A-Z]+)$")
_VOWEL = re.compile(r"[aeiouyAEIOUY]")


def script_of(text: str) -> str:
    """'hi' if Devanagari dominates, else 'en' (digits / punctuation default to en)."""
    return "hi" if len(_DEVANAGARI.findall(text)) > len(_LATIN.findall(text)) else "en"


def wordlike_ratio(text: str) -> float:
    """Share of alphabetic tokens that look like real words.

    Legacy Hindi fonts (Kruti Dev etc.) put Devanagari glyphs on Latin code points, so a
    PDF text layer can be Latin "text" like 'vf/kfu;e ljdkj dk' — technically extractable,
    actually garbage. Real English legal text scores ~0.9; such gibberish scores far lower.
    Returns 1.0 when there are too few tokens to judge.
    """
    tokens = []
    for raw in text.split():
        for part in raw.strip("()[]{}.,;:'\"‘’“”!?—–").split("-"):
            if _LATIN.search(part) and not part.isdigit():
                tokens.append(part)
    if len(tokens) < 10:
        return 1.0
    good = sum(1 for t in tokens if _WORDLIKE.match(t) and _VOWEL.search(t))
    return good / len(tokens)


_ROMAN = re.compile(r"^[ivxlcdm]+$", re.I)
_LEGACY_CHARS = re.compile(r"[ñª¡¢£¤¥¦§¨©«¬®¯°±²³´µ¶·¸º»¼½¾¿@`]")
_INNER_PUNCT = re.compile(r"[A-Za-z][/;\].,'][A-Za-z]|[A-Za-z]\[|\[[A-Za-z]")
_ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "smt", "sh", "st", "ltd", "pvt", "jr", "sr", "nos",
                  "rs"}
_NUMBERISH = re.compile(r"^\d+[A-Za-z]{0,3}$")  # '9th', '2nd', '29A', '43B'
_EDGE = "()[]{}.,;:'\"‘’“”!?—–-"


def _legacy_token(token: str) -> bool:
    core = token.strip(_EDGE)
    if not _LATIN.search(core):
        return False
    if _LEGACY_CHARS.search(core) or _INNER_PUNCT.search(core):
        return True  # 'lañ', 'vlk/kkj.k', 'izdkf'kr', 'la[;k', 'i`"B'
    if (_ROMAN.match(core) or core.isupper() or core.lower() in _ABBREVIATIONS
            or _NUMBERISH.match(core)):
        return False
    if re.search(r"[a-z][A-Z]", core):
        return True  # 'dsUnz', 'gS'
    # Every English word of 2+ letters has a vowel (counting y): 'Hkkx', 'ljdkj', 'nh', 'fd'
    return len(core) >= 2 and not _VOWEL.search(core)


def looks_legacy_hindi(line: str) -> bool:
    """Line-level check for legacy-font Hindi (e.g. a gazette masthead on an English page).

    wordlike_ratio() needs a whole page of tokens; mastheads are a few short lines, so this
    flags a line when at least half of its alphabetic tokens look like remapped glyphs.
    """
    tokens = [t for t in line.split() if _LATIN.search(t)]
    return bool(tokens) and sum(map(_legacy_token, tokens)) / len(tokens) >= 0.5
