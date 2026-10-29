"""Evidence for amendment intent: deterministic selection, quoting and attribution.

Tiers, searched in order (cheapest and most authoritative first):
- `sor`: the Bill as introduced — Statement of Objects and Reasons (government, signed by the
  minister) and, where the Bill has them, the Notes on Clauses naming the section;
- `debate`: the Lok Sabha passage debate — passages ranked by the section numbers and words
  of the amendment, each attributed to the speaker printed before it;
- `report`: Law Commission reports (2015 Amendment only) — attributed to the Commission.

Nothing here decides what a passage *means*; that is the Intent and Verification Agents'.
What is fixed here: which documents are evidence for which Act, where a quote is in its
document (exact, after whitespace / quote-mark normalization) and who said it.
"""

import re
from dataclasses import dataclass, field

STOP = set("""the of and to in a an for or be by with on as that this any such shall under
which from is are was were it its at may has have been not other all into than also their
thereof section sections act principal sub clause clauses amendment amended amend""".split())

# Evidence documents per amending Act, chosen by title (manifest titles; see data/manifest.toml)
SOURCES: dict[str, dict] = {
    "3/2016": {"bill": "The Arbitration and Conciliation (Amendment) Bill, 2015 (as introduced)",
               "debates": ["Lok Sabha debate, 17 Dec 2015"],
               "reports": ["Report No. 246", "Supplementary to Report No. 246"]},
    "33/2019": {"bill": "The Arbitration and Conciliation (Amendment) Bill, 2019 (as introduced",
                "debates": ["Lok Sabha debate, 1 Aug 2019"], "reports": []},
    "3/2021": {"bill": "The Arbitration and Conciliation (Amendment) Bill, 2021 (as introduced)",
               "debates": ["Lok Sabha debate, 12 Feb 2021"], "reports": []},
}
TIERS = ("sor", "debate", "report")
# a speaker line: honorific, no lower-case letters (labels wrap over lines; stray non-Latin
# characters survive extraction: "SHRI च. K. PREMACHANDRAN"), then a colon
SPEAKER = re.compile(r"\n((?:SHRI|SHRIMATI|SMT\.|KUMARI|DR\.|PROF\.|ADV\.|THE\s|HON\.|"
                     r"SEVERAL|AN HON)[^:a-z]{2,240}?)\s*:")
CHAIR = re.compile(r"\b(CHAIRPERSON|CHAIRMAN|SPEAKER|DEPUTY)\b")


@dataclass
class Doc:
    id: int
    title: str
    doc_type: str
    text: str


@dataclass
class Passage:
    id: str
    doc: Doc
    tier: str
    kind: str  # sor | notes | debate | report
    start: int
    end: int
    speaker: str | None = None

    @property
    def text(self) -> str:
        return self.doc.text[self.start:self.end]


@dataclass
class Unit:
    """One section of an amending Act: what intent is asked about."""
    id: str  # "<instrument>|<act section>"
    instrument: str
    act_section: str
    targets: list[str]  # principal-Act paths touched (top level)
    headings: list[str]
    instruction: str
    terms: list[str] = field(default_factory=list)


# -- quoting --------------------------------------------------------------------------------

_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-"})


def _norm(text: str) -> tuple[str, list[int]]:
    """Normalized text and, per character, its offset in `text`: whitespace runs → one space,
    curly quotes → straight, a line-end hyphen joins ("sub-\\nsection" → "sub-section")."""
    out, where = [], []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            if out and out[-1] != " " and not (out[-1] == "-" and "\n" in text[i:i + 2]):
                out.append(" ")
                where.append(i)
            while i < n and text[i].isspace():
                i += 1
            continue
        out.append(c.translate(_QUOTES))
        where.append(i)
        i += 1
    return "".join(out), where


def locate(quote: str, doc_text: str) -> tuple[int, int] | None:
    """Span of `quote` in `doc_text`, or None: the quote must be verbatim (modulo spacing)."""
    q, _ = _norm(quote.strip().strip("\"'"))
    if len(q) < 20:
        return None
    t, where = _norm(doc_text)
    i = t.find(q)
    if i < 0:
        return None
    return where[i], where[i + len(q) - 1] + 1


def _letters(name: str) -> str:
    """'D. V. SADANANDA GOWDA' and 'D.V. SADANANDA GOWDA' compare equal."""
    return re.sub(r"[^A-Z]", "", name.upper())


def speaker_at(doc_text: str, offset: int) -> str | None:
    last = None
    for m in SPEAKER.finditer(doc_text, 0, offset):
        last = " ".join(m.group(1).split())
    return last


def attribution(p: Passage, offset: int, signatory: str | None) -> dict:
    """Who said it, where: from the document and the speaker line, never from the model."""
    if p.kind in ("sor", "notes"):
        part = "Statement of Objects and Reasons" if p.kind == "sor" else "Notes on Clauses"
        return {"by": "Government", "role": "government", "where": f"{part}, {p.doc.title}",
                "signed_by": signatory}
    if p.kind == "report":
        return {"by": p.doc.title.split(":")[0], "role": "committee", "where": p.doc.title}
    who = speaker_at(p.doc.text, offset)
    role = "member"
    if who and CHAIR.search(who):
        role = "chair"
    elif who and ("MINISTER" in who.upper()
                  or (signatory and _letters(signatory) in _letters(who))):
        role = "government"  # a minister: named as one, or the Bill's signatory by name
    return {"by": who or "unidentified speaker", "role": role, "where": p.doc.title}


# -- passages -------------------------------------------------------------------------------

def sor_passages(doc: Doc) -> tuple[list[Passage], str | None]:
    t = doc.text
    i = t.find("STATEMENT OF OBJECTS")
    if i < 0:
        return [], None
    end_m = re.search(r"\n(MEMORANDUM REGARDING|FINANCIAL MEMORANDUM|ANNEXURE)", t[i:])
    end = i + end_m.start() if end_m else len(t)
    sig_m = re.search(r"NEW DELHI;\s*\n?\s*([A-Z][A-Z .]+?)\.?\n", t[i:end])
    signatory = sig_m.group(1).strip() if sig_m else None
    sor_end = i + sig_m.end() if sig_m else end
    out = [Passage(f"S{doc.id}", doc, "sor", "sor", i, sor_end)]
    # Notes on Clauses: one passage per "Clause N of the Bill ..." paragraph
    starts = [m.start() + 1 for m in re.finditer(r"\nClause \d+ ", t[sor_end:end])]
    starts = [sor_end + s for s in starts] + [end]
    for k in range(len(starts) - 1):
        out.append(Passage(f"N{doc.id}.{k + 1}", doc, "sor", "notes", starts[k], starts[k + 1]))
    return out, signatory


def chunks(doc: Doc, tier: str, kind: str, size: int = 900, stride: int = 600) -> list[Passage]:
    t, out, i = doc.text, [], 0
    while i < len(t):
        j = min(len(t), i + size)
        if j < len(t):
            nl = t.rfind("\n", i + size // 2, j)
            j = nl if nl > 0 else j
        out.append(Passage(f"{kind[0].upper()}{doc.id}.{len(out) + 1}", doc, tier, kind, i, j))
        if j >= len(t):
            break
        nxt = t.find("\n", i + stride)
        i = nxt + 1 if 0 < nxt < j else j
    return out


def terms(unit: Unit) -> list[str]:
    words = re.findall(r"[A-Za-z]{4,}", " ".join(unit.headings + [unit.instruction[:300]]))
    return list(dict.fromkeys(w.lower() for w in words if w.lower() not in STOP))


def section_refs(unit: Unit) -> list[str]:
    nums = []
    for t in unit.targets:
        if m := re.match(r"s(\d+[A-Z]?)", t):
            nums.append(m.group(1))
        elif m := re.match(r"sch(\d+)", t):
            nums.append(["", "First", "Second", "Third", "Fourth", "Fifth", "Sixth",
                         "Seventh", "Eighth"][int(m.group(1))] + " Schedule")
        elif m := re.match(r"pt(\d+)([A-Z]?)", t):
            nums.append(f"Part {['', 'I', 'II', 'III', 'IV'][int(m.group(1))]}{m.group(2)}")
    return nums


def score(p: Passage, unit: Unit) -> float:
    text = p.text.lower()
    s = 0.0
    for n in section_refs(unit):
        pat = (rf"\bsections? {re.escape(n.lower())}\b" if n[0].isdigit()
               else re.escape(n.lower()))
        s += 3 * len(re.findall(pat, text))
    s += sum(1 for w in (unit.terms or terms(unit)) if w in text)
    return s


def notes_for(unit: Unit, notes: list[Passage]) -> list[Passage]:
    out = []
    for p in notes:
        head = p.text[:300].lower()
        if any(re.search(rf"\bsections? (?:\d+[a-z]?, )*{re.escape(n.lower())}\b", head)
               if n[0].isdigit() else n.lower() in head for n in section_refs(unit)):
            out.append(p)
    return out[:2]


def rank(passages: list[Passage], unit: Unit, k: int = 3, floor: float = 4.0) -> list[Passage]:
    scored = sorted(((score(p, unit), i, p) for i, p in enumerate(passages)), reverse=True)
    return [p for s, _, p in scored[:k] if s >= floor]
