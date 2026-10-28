"""Amendment operations of amending Acts and Ordinances: the formulaic cases, by rule.

Input is an instrument's extracted text (`documents.text_path`). Its body is read into
sections and nested items ("(i) in sub-section (1),—" / "(A) after clause (c), ..."); the
text quoted after "namely:—" is a block, kept apart so the "(i)" of an inserted definition is
never read as an instruction. Each item that acts ("shall be inserted / substituted /
omitted / renumbered") gives operations, its ancestors' text giving the location:

    for the words “X”, the words “Y” shall be substituted         substitute, unit words
    after the words “X”, the words “Y” shall be inserted          insert, unit words
    the words “X” shall be omitted                                omit, unit words
    for the portion beginning with “X” and ending with “Y”, ...   substitute, unit portion
    for sub-section (1), the following sub-section shall be ...   substitute, per new unit
    after clause (c), the following clause shall be inserted ...  insert, per new unit
    sub-sections (6A) and (7) shall be omitted                    omit, per unit
    Section 9 ... shall be renumbered as sub-section (1) thereof  renumber

Quoted units are parsed with the clause parser (`parse_fragment`), which gives their paths.

Paths are checked against the **known paths** of the version each instrument amends:
the base clause tree, then every earlier Act's ops applied to the path set (an Ordinance is
checked against the state before it but not carried forward: the Act replacing it enacts
its changes again). Words are checked against the base text where the target is unchanged.

An op is `resolved` only when every field comes from a formula and its target checks out.
Anything else is `unresolved` with a `reason` (the Genealogy Agent's work, Phase 2): a target
not in the known paths, "the proviso" where there are several, a substitution whose new
units do not pair one to one with the old, words not found, text after a quoted block,
a margin note naming another section, an instruction no rule reads.

    uv run python -m opensansad.parse.amending_ops show DOC_ID [DOC_ID...]
    uv run python -m opensansad.parse.amending_ops load [--act-id 1] [--version 1996-enacted]
"""

import argparse
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field

from opensansad.parse.clause_tree import (
    _REFERENCE_END,
    MARGIN,
    Node,
    Tree,
    join_lines,
    parse_fragment,
)

ACTION = re.compile(r"shall\s+be\s+(?:inserted|substituted|omitted|renumbered|numbered)")
NAMELY = re.compile(r"namely\s*[:,]?\s*[—-]*\s*$")
BODY_START = re.compile(r"(?:follows|Ordinance)\s*:\s*[—-]+\s*$")
SIGNATURE = re.compile(r"^(?:—|[A-Z][A-Z .]+,)$")
NOISE = re.compile(r"^(?:THE GAZETTE OF INDIA EXTRAORDINARY|\[PART II\s*[—-]\s*SEC\.\s*[1I]\]"
                   r"|\d{1,3})$", re.I)
OPEN_QUOTE = re.compile(r"^[“‘\"']")
BLOCK_END = re.compile(r"[”’\"']\s*[.;]\s*$")
SECTION = re.compile(r"^(\d+[A-Z]*)\.\s+")
ITEM = re.compile(r"^\(\s*([a-zA-Z]{1,5})\s*\)\s*")
QUOTE = re.compile(r"“(.*?)”|(?<![\w’])‘(.*?)’(?!\w)|\"(.*?)\"")
ROMANS = ["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x", "xi", "xii", "xiii",
          "xiv", "xv", "xvi", "xvii", "xviii", "xix", "xx"]
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
            "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}
ROMAN = {r.upper(): i for i, r in enumerate(ROMANS, 1)}

_LABELS = r"\(\w+\)(?:\s*(?:,|and|to)\s*\(\w+\))*"
_SEC = r"\d+[A-Z]*(?:-[A-Z]+)?"
REF = re.compile(
    rf"(?P<sub>\b[Ss]ub-sections?\s+(?P<sub_l>{_LABELS}))"
    rf"|(?P<subcl>\b[Ss]ub-clauses?\s+(?P<subcl_l>{_LABELS}))"
    rf"|(?P<cl>(?<!-)\b[Cc]lauses?\s+(?P<cl_l>{_LABELS}))"
    rf"|(?P<sec>(?<!-)\b[Ss]ections?\s+(?P<sec_l>{_SEC}(?:\s*(?:,|and|to)\s*{_SEC})*))"
    r"|(?P<prov>\b[Tt]he\s+(?:(?P<prov_o>first|second|third|fourth|fifth)\s+)?proviso\b)"
    r"|(?P<expl>\b[Tt]he\s+Explanation(?:\s+(?P<expl_n>\d+))?)"
    r"|(?P<sch>\b[Tt]he\s+(?P<sch_o>First|Second|Third|Fourth|Fifth|Sixth|Seventh|Eighth|Ninth"
    r"|Tenth)\s+Schedule)"
    r"|(?P<part>\bPart\s+(?P<part_l>[IVX]+[A-Z]?)\b)")
UNIT_WORDS = {"section": "section", "sections": "section", "sub-section": "subsection",
              "sub-sections": "subsection", "clause": "clause", "clauses": "clause",
              "sub-clause": "subclause", "sub-clauses": "subclause", "proviso": "proviso",
              "provisos": "proviso", "explanation": "explanation", "explanations": "explanation",
              "schedule": "schedule", "schedules": "schedule", "part": "part"}
_OBJ = r"the (?:[\w, ]+? )?"
WORDS_SUB = re.compile(rf"\b[Ff]or {_OBJ}(Q\d+)(,? wherever (?:they|it) occurs?)?,?\s+{_OBJ}"
                       r"(Q\d+),?\s+shall be substituted")
WORDS_INS = re.compile(rf"\b([Aa]fter|[Bb]efore) {_OBJ}(Q\d+),?\s+{_OBJ}(Q\d+),?\s+"
                       r"shall be inserted")
WORDS_OMIT = re.compile(rf"\b{_OBJ}(Q\d+)(,? wherever (?:they|it) occurs?)?,?\s+shall be omitted")
PORTION = re.compile(r"\b[Ff]or the portion beginning with (Q\d+) and ending with (Q\d+),?\s+"
                     r"the following shall be substituted")
UNIT_SUB = re.compile(r"\b[Ff]or (?P<obj>(?:(?!\b[Ff]or ).)+?),?\s+the following (?:new )?"
                      r"(?P<unit>[\w-]+)\s+shall be substituted")
UNIT_INS = re.compile(r"(?:\b(?P<pos>[Aa]fter|[Bb]efore) (?P<obj>(?:(?!\b[Aa]fter ).)+?),?\s+)?"
                      r"the following (?:new )?(?:(?P<unit>[\w-]+)\s+)?shall be inserted")
UNIT_OMIT = re.compile(r"shall be omitted")
RENUMBER = re.compile(r"(?P<obj>.+?)\s+shall be (?:re)?numbered as (?P<new>.+?) thereof"
                      r"(?:,?\s+and\s+(?P<rest>.*))?$")
EFFECT = re.compile(r"with effect from (?:the )?(\d+\w* (?:day of )?\w+,? \d{4})")
OTHER_ACT = re.compile(r"\(Amendment\) (?:Act|Ordinance),? \d{4}")


class Unresolved(Exception):
    pass


# -- reading the instrument -----------------------------------------------------------------

@dataclass
class Block:
    lines: list[tuple[int, str]]  # (file offset, line) of quoted text, quotes included
    unclosed: bool = False  # ended by the next provision of the instrument, not a quote

    @property
    def start(self) -> int:
        return self.lines[0][0]

    @property
    def end(self) -> int:
        return self.lines[-1][0] + len(self.lines[-1][1])


@dataclass
class Item:
    """A section of the instrument, or one of its (nested) lettered / numbered items."""

    path: str  # in the instrument: s3, s3.cl(ii), s2.cl(I).cl(A)
    label: str | None
    kind: str | None  # roman / alpha / ROMAN / ALPHA
    parent: "Item | None"
    lines: list[tuple[int, str]] = field(default_factory=list)  # instruction text
    blocks: list[Block] = field(default_factory=list)
    after_block: list[str] = field(default_factory=list)  # instruction text after a block
    children: list["Item"] = field(default_factory=list)
    margins: list[str] = field(default_factory=list)
    stray: list[str] = field(default_factory=list)  # non-quoted lines between namely and quote

    @property
    def text(self) -> str:
        return join_lines([t for _, t in self.lines])

    @property
    def start(self) -> int:
        return self.lines[0][0] if self.lines else (self.blocks[0].start if self.blocks else 0)

    @property
    def end(self) -> int:
        ends = [o + len(t) for o, t in self.lines] + [b.end for b in self.blocks]
        ends += [c.end for c in self.children]
        return max(ends) if ends else self.start

    def chain(self) -> list["Item"]:
        out, node = [], self
        while node is not None:
            out.append(node)
            node = node.parent
        return out[::-1]


_NEXT_PROVISION = re.compile(r"^\[\[margin: (?:Amendment|Insertion|Substitution|Omission)s? "
                             r"(?:of|to)\b")


def _ends_unclosed(line: str, section: "Item | None") -> bool:
    """Inside quoted text: this line is the instrument's next provision (its margin heading,
    or the next section number), so the closing quote was lost."""
    if _NEXT_PROVISION.match(line):
        return True
    m = SECTION.match(line)
    return bool(m and section is not None and section.label and section.label.isdigit()
                and m.group(1) == str(int(section.label) + 1)
                and re.search(r"\bprincipal Act\b", line))


def _item_kind(label: str) -> str:
    roman = label.lower() in ROMANS
    kind = "roman" if roman and label.lower() == "i" or (roman and len(label) > 1) else "alpha"
    return kind.upper() if label.isupper() else kind


def _follows(kind: str | None, previous: str | None, label: str) -> bool:
    if kind is None or previous is None or (kind.isupper() != label.isupper()):
        return False
    p, q = previous.lower(), label.lower()
    if kind.lower() == "roman":
        i = ROMANS.index(p) if p in ROMANS else -1
        return 0 <= i < len(ROMANS) - 1 and ROMANS[i + 1] == q
    return len(p) == 1 and len(q) == 1 and ord(q) == ord(p) + 1


@dataclass
class Instrument:
    sections: list[Item]
    warnings: list[str]


def read(source: str) -> Instrument:
    """Sections and items of an amending instrument's body."""
    lines, offset = [], 0
    for line in source.splitlines(keepends=True):
        lines.append((offset, line.rstrip("\n")))
        offset += len(line)
    warnings: list[str] = []
    sections: list[Item] = []
    started = False
    current: Item | None = None
    block: Block | None = None
    expect_block = False
    margins: list[str] = []
    previous = ""
    for offset, raw in lines:
        s = raw.strip()
        begin = offset + len(raw) - len(raw.lstrip())
        if not s or NOISE.match(s):
            continue
        if not started:
            started = bool(BODY_START.search(s))
            continue
        if block is not None:
            if _ends_unclosed(s, sections[-1] if sections else None):
                block.unclosed = True
                warnings.append(f"quoted text from offset {block.start} not closed before "
                                f"{s[:40]!r}")
                block = None
            else:
                block.lines.append((begin, s))
                if BLOCK_END.search(s):
                    block = None
                continue
        if MARGIN.fullmatch(s):
            margins.append(s[len("[[margin: "):-2].strip())
            continue
        if SIGNATURE.match(s):
            break
        if expect_block:
            if OPEN_QUOTE.match(s):
                block = Block([(begin, s)])
                assert current is not None
                current.blocks.append(block)
                expect_block = False
                if BLOCK_END.search(s) and len(s) > 3:
                    block = None
                continue
            if current is not None:
                current.stray.append(s)
            continue
        continues = bool(_REFERENCE_END.search(previous))
        previous = s
        if not continues and (m := SECTION.match(s)):
            current = Item(f"s{m.group(1)}", m.group(1), None, None)
            current.lines.append((begin + m.end(), s[m.end():]))
            current.margins, margins = margins, []
            sections.append(current)
        elif current is not None and not continues and (m := ITEM.match(s)) and (
                m.group(1).lower() in ROMANS or len(m.group(1)) == 1):
            label = m.group(1)
            node: Item | None = current
            while node is not None and node.parent is not None and not _follows(
                    node.kind, node.label, label):
                node = node.parent
            if node is not None and node.parent is not None:
                parent, kind = node.parent, node.kind  # a sibling of an open item
            else:
                kind = _item_kind(label)
                parent = current
                if label.lower() not in ("a", "i"):
                    warnings.append(f"{current.path}: item ({label}) continues no open list")
            item = Item(f"{parent.path}.cl({label})", label, kind, parent)
            item.lines.append((begin + m.end(), s[m.end():]))
            parent.children.append(item)
            current = item
        elif current is not None:
            if current.blocks:
                current.after_block.append(s)
            current.lines.append((begin, s))
        else:
            warnings.append(f"text before the first section: {s[:50]!r}")
        if current is not None and NAMELY.search(s):
            expect_block = True
    if block is not None:
        warnings.append(f"quoted text never closed (from offset {block.start})")
    if not started:
        warnings.append("no enacting formula found")
    return Instrument(sections, warnings)


# -- known paths ----------------------------------------------------------------------------

class PathState:
    """Clause paths of the version an instrument amends, with the base text where unchanged."""

    def __init__(self, base: Iterable[tuple[str, str]], label: str) -> None:
        self.order: list[str] = []
        self.own: dict[str, str] = {}
        for path, text in base:
            self.order.append(path)
            self.own[path] = text
        self.origin = {p: label for p in self.order}
        self.base_label = label
        self.touched: set[str] = set()

    def copy(self) -> "PathState":
        other = PathState([], self.base_label)
        other.order, other.own = list(self.order), dict(self.own)
        other.origin, other.touched = dict(self.origin), set(self.touched)
        return other

    def has(self, path: str) -> bool:
        return path in self.origin

    def subtree(self, path: str) -> list[str]:
        return [p for p in self.order if p == path or p.startswith(path + ".")]

    def count(self, parent: str, prefix: str) -> int:
        pat = re.compile(re.escape(parent) + r"\." + prefix + r"\d+$")
        return sum(bool(pat.match(p)) for p in self.order)

    def base_text(self, path: str) -> str | None:
        """Subtree text, if every node of it is unchanged base text."""
        nodes = self.subtree(path)
        if not nodes or any(self.origin[p] != self.base_label or p in self.touched
                            for p in nodes) or path in self.touched:
            return None
        return "\n".join(self.own[p] for p in nodes if self.own.get(p))

    def touch(self, path: str) -> None:
        parts = path.split(".")
        for i in range(1, len(parts) + 1):
            self.touched.add(".".join(parts[:i]))

    def remove(self, path: str) -> None:
        for p in self.subtree(path):
            self.order.remove(p)
            self.own.pop(p, None)
            self.origin.pop(p)
        self.touch(path)

    def add(self, paths: list[str], origin: str, after: str | None = None) -> None:
        at = len(self.order)
        if after is not None and after in self.origin:
            at = self.order.index(self.subtree(after)[-1]) + 1
        new = [p for p in paths if p not in self.origin]
        self.order[at:at] = new
        for p in new:
            self.origin[p] = origin
        for p in paths:
            self.touch(p)

    def move(self, old: str, new: str) -> None:
        for p in self.subtree(old):
            q = new + p[len(old):]
            self.order[self.order.index(p)] = q
            self.own[q] = self.own.pop(p, "")
            self.origin[q] = self.origin.pop(p)
            self.touched.add(q)
        if old not in self.origin:  # the old path is now a container of the new
            self.order.insert(self.order.index(new), old)
            self.origin[old], self.own[old] = self.origin[new], ""
        self.touch(new)


# -- interpreting one instruction ------------------------------------------------------------

@dataclass
class Op:
    provision: str
    op_type: str
    unit: str | None
    target_path: str | None
    span: tuple[int, int]
    anchor_path: str | None = None
    old_text: str | None = None
    new_text: str | None = None
    reason: str | None = None
    meta: dict = field(default_factory=dict)
    new_paths: list[str] = field(default_factory=list)  # subtree an insert / substitute adds

    @property
    def status(self) -> str:
        return "unresolved" if self.reason else "resolved"


def normalize_quotes(text: str) -> str:
    return re.sub(r"\(\s*(\w+)\s*\)", r"(\1)", text.replace("‘‘", "“").replace("’’", "”"))


def placeholders(text: str) -> tuple[str, list[str]]:
    quotes: list[str] = []

    def sub(m: re.Match) -> str:
        quotes.append(next(g for g in m.groups() if g is not None).strip())
        return f"Q{len(quotes) - 1}"
    return QUOTE.sub(sub, normalize_quotes(text)), quotes


@dataclass
class Ref:
    kind: str
    labels: list[str]
    start: int
    end: int


def _expand(labels: str, kind: str) -> list[str]:
    """ "(4), (5) and (6)" → 4, 5, 6; "(11) to (14)" → 11..14; "42A and 42B" → 42A, 42B."""
    out: list[str] = []
    pending_range = False
    for paren, to, bare in re.findall(rf"\((\w+)\)|\b(to)\b|\b({_SEC})\b", labels):
        if to:
            pending_range = True
            continue
        value = paren or bare
        if pending_range and out:
            out += _range(out[-1], value)[1:]
            pending_range = False
        else:
            out.append(value)
    return out


def _range(first: str, last: str) -> list[str]:
    if first.isdigit() and last.isdigit():
        return [str(i) for i in range(int(first), int(last) + 1)]
    if first in ROMANS and last in ROMANS:
        return ROMANS[ROMANS.index(first):ROMANS.index(last) + 1]
    if len(first) == len(last) == 1:
        return [chr(c) for c in range(ord(first), ord(last) + 1)]
    raise Unresolved(f"range ({first}) to ({last}) not expandable")


def refs(text: str) -> list[Ref]:
    out = []
    for m in REF.finditer(text):
        kind = m.lastgroup if m.lastgroup and not m.lastgroup.endswith(("_l", "_o", "_n")) else \
            next(k for k in ("sub", "subcl", "cl", "sec", "prov", "expl", "sch", "part")
                 if m.group(k))
        if kind in ("sub", "subcl", "cl", "sec"):
            labels = _expand(m.group(f"{kind}_l"), kind)
        elif kind == "prov":
            labels = [str(ORDINALS[m.group("prov_o")])] if m.group("prov_o") else []
        elif kind == "expl":
            labels = [m.group("expl_n")] if m.group("expl_n") else []
        elif kind == "sch":
            labels = [str(ORDINALS[m.group("sch_o").lower()])]
        else:
            label = m.group("part_l")
            base = label.rstrip("ABCDEFGH") if len(label) > 1 and label[-1] not in "IVX" else label
            labels = [f"{ROMAN[base]}{label[len(base):]}"]
        out.append(Ref(kind, labels, m.start(), m.end()))
    # "the Explanation to sub-section (2)": the first is inside the second
    i = 0
    while i < len(out) - 1:
        between = text[out[i].end:out[i + 1].start]
        if re.fullmatch(r"\s+to\s+", between) and out[i].kind in ("prov", "expl", "sub", "cl",
                                                                   "subcl"):
            out[i], out[i + 1] = out[i + 1], out[i]
            i += 2
        else:
            i += 1
    return out


def build(chain: list[Ref], state: PathState, *, plural_ok: bool = True) -> list[str]:
    paths = [""]
    for ref in chain:
        nxt = []
        for p in paths:
            if ref.kind == "sec":
                nxt += [f"s{x}" for x in ref.labels]
            elif ref.kind == "sch":
                nxt += [f"sch{x}" for x in ref.labels]
            elif ref.kind == "part":
                nxt += [f"pt{x}" for x in ref.labels]
            elif not p:
                raise Unresolved(f"{ref.kind} reference with no section or Schedule before it")
            elif ref.kind in ("sub", "cl", "subcl"):
                fmt = {"sub": "{}.ss{}", "cl": "{}.cl({})", "subcl": "{}.sc({})"}[ref.kind]
                nxt += [fmt.format(p, x) for x in ref.labels]
            else:
                prefix = "p" if ref.kind == "prov" else "expl"
                word = "proviso" if ref.kind == "prov" else "Explanation"
                if ref.labels:
                    nxt.append(f"{p}.{prefix}{ref.labels[0]}")
                    continue
                n = state.count(p, prefix)
                if n != 1:
                    raise Unresolved(f"'the {word}' of {p}: {n} known")
                nxt.append(f"{p}.{prefix}1")
        paths = nxt
    if len(paths) > 1 and not plural_ok:
        raise Unresolved(f"several targets {paths}")
    return [p for p in paths if p]


def _last_object(rs: list[Ref], text: str, at: int) -> tuple[list[Ref], list[Ref]]:
    """(context, object) refs for a "... shall be omitted" at `at`: the object is the last
    ref before the action and any refs joined to it by "to"."""
    before = [r for r in rs if r.end <= at]
    if not before:
        return [], []
    k = len(before) - 1
    while k > 0 and re.fullmatch(r"\s+to\s+", text[before[k - 1].end:before[k].start]):
        k -= 1
    # a swapped pair ("the proviso to sub-section (1)") reads as one object
    if k > 0 and before[k].kind in ("prov", "expl") and before[k - 1].start > before[k].start:
        k -= 1
    return before[:k], before[k:]


def _block_lines(block: Block) -> list[tuple[int, str]]:
    """Block lines with the opening quote and the closing quote + mark removed."""
    lines = list(block.lines)
    o, t = lines[0]
    m = re.match(r"^[“‘\"']+", t)
    if m:
        lines[0] = (o + m.end(), t[m.end():])
    o, t = lines[-1]
    m = re.search(r"[”’\"']+\s*[.;]\s*$", t)
    if m:
        lines[-1] = (o, t[:m.start()])
    return lines


def _block_text(block: Block) -> str:
    return join_lines([t for _, t in _block_lines(block) if not MARGIN.fullmatch(t.strip())])


def _infer_unit(block: Block) -> str | None:
    first = _block_lines(block)[0][1].lstrip("“‘\"'")
    for pattern, unit in ((r"^Provided\b", "proviso"), (r"^Explanation\b", "explanation"),
                          (r"^\(\d+[A-Z]*\)", "subsection"), (r"^\([a-z]{1,2}\)", "clause"),
                          (r"^\d+[A-Z]*(?:-[A-Z]+)?\.", "section"),
                          (r"^THE \w+ SCHEDULE", "schedule"), (r"^PART ", "part")):
        if re.match(pattern, first):
            return unit
    return None


def _subtree_text(tree: Tree, node: Node) -> str:
    nodes = [n for n in tree.nodes if n is node or n.path.startswith(node.path + ".")]
    return "\n".join(t for t in (n.text(tree.source) for n in nodes) if t)


def _fragment_units(source: str, block: Block, unit: str, container: str | None,
                    state: PathState, *, after_label: str | None = None,
                    skip_existing: dict[str, int] | None = None) -> tuple[Tree, list[Node]]:
    """Parse a quoted block; returns the tree and its top-level units."""
    parent_type = None
    if container is not None:
        last = container.split(".")[-1]
        parent_type = ("subsection" if last.startswith("ss") else "clause"
                       if last.startswith("cl(") else "subclause" if last.startswith("sc(")
                       else "schedule" if last.startswith("sch") else "proviso"
                       if re.match(r"p\d", last) else "section")
    siblings = skip_existing
    if siblings is None and container is not None:
        siblings = {"proviso": state.count(container, "p"),
                    "explanation": state.count(container, "expl")}
    # the unit the quoted list continues: the insertion point, or for a substituted "(e)"
    # the "(d)" before it (so a substituted "(i)" after "(h)" reads as a clause)
    after = (unit, after_label) if after_label and unit in ("clause", "subclause") else None
    m = re.match(r"^\(([a-z]+)\)", _block_lines(block)[0][1].lstrip("“‘\"'"))
    if after is None and m and unit == "clause" and len(m.group(1)) == 1 and m.group(1) != "a":
        after = (unit, chr(ord(m.group(1)) - 1))
    elif after is None and m and unit == "subclause" and m.group(1) in ROMANS[1:]:
        after = (unit, ROMANS[ROMANS.index(m.group(1)) - 1])
    tree = parse_fragment(source, _block_lines(block), parent=container,
                          parent_type=parent_type, siblings=siblings, after=after)
    top = [n for n in tree.nodes if n.parent is None or n.parent.path == container
           or n.parent.path.endswith("~")]
    return tree, top


def interpret(item: Item, source: str, state: PathState, origin: str) -> list[Op]:
    """Operations of one acting item (with its ancestors' text as location)."""
    full = " ".join(i.text for i in item.chain())
    q, quotes = placeholders(full)
    span = (item.start, item.end)
    effect = EFFECT.search(full)
    base_meta: dict = {"effective_from": effect.group(1)} if effect else {}
    m = re.search(r"\bin the (long line|opening portion|closing portion|opening words)", q)
    if m:
        base_meta["portion"] = m.group(1)
    if re.search(r"\bat the end\b", q):
        base_meta["position"] = "at the end"
    if item.stray:
        base_meta["stray_lines"] = item.stray
    trailing = [t for t in item.after_block if re.search(r"[A-Za-z]{3}", t)]
    root = item.chain()[0]
    try:
        if OTHER_ACT.search(q.split("shall be")[0]) and "principal Act" not in q:
            raise Unresolved(f"amends another instrument ({OTHER_ACT.search(q).group(0)}), "
                             "not the principal Act")
        ops = _interpret(q, quotes, item, source, state, origin, span, base_meta)
    except Unresolved as e:
        rs = refs(q)
        target = None
        try:
            target = build(rs[:len(rs)], state)[0] if rs else None
        except Unresolved:
            pass
        op_type = ("omit" if "omitted" in q else "insert" if "inserted" in q else "substitute"
                   if "substituted" in q else "renumber" if "numbered" in q else "other")
        return [Op(item.path, op_type, None, target, span, reason=str(e),
                   meta={**base_meta, "instruction": full[:300]})]
    margin_secs = _margin_sections(root.margins)
    for op in ops:
        if trailing and not op.reason:
            op.reason = "instruction text after the quoted text: " + " ".join(trailing)[:120]
        top = (op.target_path or "").split(".")[0]
        if margin_secs and top.startswith("s") and top not in margin_secs and not op.reason:
            op.reason = f"margin note names {sorted(margin_secs)}, op targets {top}"
    return ops


def _margin_sections(margins: list[str]) -> set[str]:
    out: set[str] = set()
    for note in margins:
        if re.match(r"^(Amendment of|Insertion of new|Substitution of new)\s+sections?", note):
            out |= {f"s{x}" for x in re.findall(rf"\b({_SEC})\b", note)}
    return out


def _interpret(q: str, quotes: list[str], item: Item, source: str, state: PathState,
               origin: str, span: tuple[int, int], meta: dict) -> list[Op]:
    rs = refs(q)

    def ctx_paths(at: int) -> list[str]:
        return build([r for r in rs if r.end <= at], state)

    def need(paths: list[str]) -> list[str]:
        missing = [p for p in paths if not state.has(p)]
        if missing:
            raise Unresolved(f"target {', '.join(missing)} not in the known paths")
        return paths

    def qv(token: str) -> str:
        return quotes[int(token[1:])]

    if m := RENUMBER.search(q):
        old = need(build([r for r in rs if r.end <= m.start("new")], state, plural_ok=False))[0]
        new_refs = refs(m.group("new"))
        if len(new_refs) != 1 or new_refs[0].kind != "sub" or len(new_refs[0].labels) != 1:
            raise Unresolved("renumbering to a unit other than one sub-section")
        new = f"{old}.ss{new_refs[0].labels[0]}"
        ops = [Op(item.path, "renumber", "section", old, span, new_text=new, meta=dict(meta),
                  new_paths=[new])]
        state_after = state.copy()
        state_after.move(old, new)
        if m.group("rest"):
            sec = [r for r in rs if r.end <= m.start("new")][0]
            rest_q = q[sec.start:sec.end] + ", " + m.group("rest")
            ops += _interpret(rest_q, quotes, item, source, state_after, origin, span, meta)
        return ops

    if m := PORTION.search(q):
        targets = need(ctx_paths(m.start()))
        if len(item.blocks) != 1:
            raise Unresolved(f"{len(item.blocks)} quoted blocks for one substitution")
        new = _block_text(item.blocks[0])
        return [Op(item.path, "substitute", "portion", t, span, new_text=new,
                   meta={**meta, "begins": qv(m.group(1)), "ends": qv(m.group(2))})
                for t in targets]

    for regex, op_type in ((WORDS_SUB, "substitute"), (WORDS_INS, "insert"),
                           (WORDS_OMIT, "omit")):
        if m := regex.search(q):
            targets = need(ctx_paths(m.start()))
            ops = []
            for t in targets:
                op_meta = dict(meta)
                if op_type == "substitute":
                    old, new = qv(m.group(1)), qv(m.group(3))
                    if m.group(2):
                        op_meta["wherever"] = True
                elif op_type == "insert":
                    old, new = None, qv(m.group(3))
                    op_meta[m.group(1).lower()] = qv(m.group(2))
                else:
                    old, new = qv(m.group(1)), None
                    if m.group(2):
                        op_meta["wherever"] = True
                op = Op(item.path, op_type, "words", t, span, old_text=old, new_text=new,
                        meta=op_meta)
                look_for = old if old is not None else op_meta.get("after") or \
                    op_meta.get("before")
                _check_words(op, look_for, state)
                ops.append(op)
            return ops

    if m := UNIT_SUB.search(q):
        ctx = [r for r in rs if r.end <= m.start("obj")]
        obj = [r for r in rs if m.start("obj") <= r.start and r.end <= m.end("obj")]
        if not obj:
            raise Unresolved(f"substituted unit not read: {m.group('obj')!r}")
        old = need(build(ctx + obj, state))
        return _units(item, source, state, origin, span, meta, "substitute",
                      UNIT_WORDS.get(m.group("unit").lower()), old=old)

    if m := UNIT_INS.search(q):
        unit = UNIT_WORDS.get((m.group("unit") or "").lower())
        if m.group("unit") and unit is None:
            raise Unresolved(f"inserted unit {m.group('unit')!r} not known")
        anchor = None
        if m.group("obj"):
            ctx = [r for r in rs if r.end <= m.start("obj")]
            obj = [r for r in rs if m.start("obj") <= r.start and r.end <= m.end("obj")]
            if not obj:
                raise Unresolved(f"insertion point not read: {m.group('obj')!r}")
            anchor = need(build(ctx + obj, state, plural_ok=False))[0]
        else:
            ctx = [r for r in rs if r.end <= m.start()]
        container = need(build(ctx, state, plural_ok=False))[0] if ctx else None
        return _units(item, source, state, origin, span, meta, "insert", unit,
                      anchor=anchor, container=container,
                      before=(m.group("pos") or "").lower() == "before")

    if m := UNIT_OMIT.search(q):
        ctx, obj = _last_object(rs, q, m.start())
        if not obj:
            raise Unresolved("omitted unit not read")
        targets = need(build(ctx + obj, state))
        return [Op(item.path, "omit", _unit_of(t), t, span, old_text=state.base_text(t),
                   meta=dict(meta)) for t in targets]

    raise Unresolved("no rule reads this instruction")


def _unit_of(path: str) -> str:
    last = path.split(".")[-1]
    for prefix, unit in (("ss", "subsection"), ("cl(", "clause"), ("sc(", "subclause"),
                         ("expl", "explanation"), ("sch", "schedule"), ("pt", "part"),
                         ("s", "section")):
        if last.startswith(prefix):
            return unit
    if re.fullmatch(r"p\d+", last):
        return "proviso"
    return "other"


def _check_words(op: Op, words: str | None, state: PathState) -> None:
    if words is None or op.target_path is None:
        return
    text = state.base_text(op.target_path)
    if text is None:
        op.meta["text_check"] = "target changed since the base text: not checked"
        return

    def norm(t: str) -> str:
        t = normalize_quotes(t).replace("“", '"').replace("”", '"').replace("’", "'")
        return re.sub(r"\s+", " ", t).strip().lower()
    found = norm(text).count(norm(words))
    op.meta["text_check"] = f"found {found}x in the base text"
    if found == 0:
        op.reason = f"words {words[:60]!r} not in the base text of {op.target_path}"
    elif found > 1 and not op.meta.get("wherever"):
        op.reason = f"words {words[:60]!r} occur {found}x in {op.target_path}"


def _attach_followers(tree: Tree, top: list[Node]) -> list[Node]:
    """Provisos / Explanations quoted after a named unit ("the following clause") are part of
    that unit: re-path them under the unit before them."""
    out: list[Node] = []
    for n in top:
        if out and n.node_type in ("proviso", "explanation") and out[-1].node_type not in (
                "proviso", "explanation"):
            unit = out[-1]
            prefix = "p" if n.node_type == "proviso" else "expl"
            k = 1 + sum(c.node_type == n.node_type for c in unit.children)
            old, new = n.path, f"{unit.path}.{prefix}{k}"
            for x in tree.nodes:
                if x is n or x.path.startswith(old + "."):
                    x.path = new + x.path[len(old):]
            n.parent = unit
            unit.children.append(n)
            n.meta["attached"] = f"quoted with {unit.path}"
            continue
        out.append(n)
    return out


def _units(item: Item, source: str, state: PathState, origin: str, span: tuple[int, int],
           meta: dict, op_type: str, unit: str | None, *, old: list[str] | None = None,
           anchor: str | None = None, container: str | None = None,
           before: bool = False) -> list[Op]:
    if len(item.blocks) != 1:
        raise Unresolved(f"{len(item.blocks)} quoted blocks for one {op_type}")
    block = item.blocks[0]
    unit = unit or _infer_unit(block)
    if unit is None:
        raise Unresolved("unit of the quoted text not known")
    after_label = None
    skip = None
    if op_type == "substitute":
        assert old is not None
        container = ".".join(old[0].split(".")[:-1]) or None
        if unit in ("proviso", "explanation"):
            first = int(re.search(r"\d+$", old[0]).group(0))  # type: ignore[union-attr]
            skip = {unit: first - 1}
    elif anchor is not None:
        if unit in ("section", "schedule", "part"):
            container = None
        elif unit in ("proviso", "explanation"):
            container = ".".join(anchor.split(".")[:-1]) if re.search(
                r"\.(p|expl)\d+$", anchor) else anchor
        else:
            container = ".".join(anchor.split(".")[:-1]) or None
            m = re.search(r"\((\w+)\)$", anchor)
            after_label = m.group(1) if m else None
    tree, top = _fragment_units(source, block, unit, container, state, after_label=after_label,
                                skip_existing=skip)
    if not top:
        raise Unresolved("no unit read in the quoted text")
    ops: list[Op] = []
    warnings = [w.split(": ", 1)[-1] for w in tree.warnings]
    if unit not in ("proviso", "explanation") and op_type == "substitute" or (
            op_type == "insert" and unit in ("clause", "subclause", "subsection")):
        top = _attach_followers(tree, top)
    new_by_path = {n.path: n for n in top}
    for n in top:
        sub = [x.path for x in tree.nodes if x is n or x.path.startswith(n.path + ".")]
        op_meta = dict(meta)
        if n.heading:
            op_meta["heading"] = n.heading
        if n.container:
            op_meta["container"] = n.container
        if warnings:
            op_meta["fragment_warnings"] = warnings
        if block.unclosed:
            op_meta["quote_unclosed"] = True
        op = Op(item.path, op_type, n.node_type, n.path, span,
                new_text=_subtree_text(tree, n) or n.heading, meta=op_meta, new_paths=sub)
        if op_type == "insert":
            op.anchor_path = anchor
            if before:
                op.meta["before"] = True
            if state.has(n.path):
                op.reason = f"inserted {n.path} already in the known paths"
            elif n.node_type != unit and not (unit == "part" and n.node_type == "section"):
                op.reason = f"instruction inserts a {unit}, quoted text has a {n.node_type}"
        else:
            assert old is not None
            if n.path in old:
                op.old_text = state.base_text(n.path)
            else:
                op.reason = (f"new {n.path} has no counterpart among the substituted "
                             f"{', '.join(old)}")
                op.meta["substituted"] = old
        ops.append(op)
    if op_type == "substitute":
        assert old is not None
        if len(old) != len(top):
            for op in ops:
                op.reason = op.reason or (
                    f"{len(old)} unit(s) substituted by {len(top)}: pairing by label is not "
                    "evidence of lineage")
                op.meta["substituted"] = old
        for p in old:
            if p not in new_by_path:
                ops.append(Op(item.path, "substitute", _unit_of(p), p, span,
                              old_text=state.base_text(p),
                              reason=f"substituted {p} has no counterpart in the new text "
                                     f"({', '.join(new_by_path)})",
                              meta={**meta, "new": list(new_by_path)}))
    return ops


# -- whole instruments ----------------------------------------------------------------------

@dataclass
class Extraction:
    ops: list[Op]
    non_amending: list[str]  # instrument provisions that change no text of the principal Act
    warnings: list[str]


def acting_items(section: Item) -> list[Item]:
    out = []

    def walk(item: Item) -> None:
        if ACTION.search(item.text):
            out.append(item)
        for c in item.children:
            walk(c)
    walk(section)
    return out


def extract(source: str, state: PathState, origin: str, *, carry: bool = True) -> Extraction:
    """Ops of one instrument; with `carry`, `state` becomes the version it produces."""
    instrument = read(source)
    ops: list[Op] = []
    non_amending: list[str] = []
    work = state if carry else state.copy()
    for section in instrument.sections:
        items = acting_items(section)
        if not items:
            note = f" [{'; '.join(section.margins)}]" if section.margins else ""
            non_amending.append(f"{section.path}{note}: {section.text[:80]}")
            continue
        for item in items:
            for op in interpret(item, source, work, origin):
                ops.append(op)
                apply(op, work, origin)
    return Extraction(ops, non_amending, instrument.warnings)


def apply(op: Op, state: PathState, origin: str) -> None:
    """The op's effect on the known paths (resolved ops and unpaired substitutions alike:
    the instrument's text says what the new units are, whatever their lineage)."""
    t = op.target_path
    if t is None:
        return
    if op.op_type == "renumber" and op.new_text and state.has(t):
        state.move(t, op.new_text)
    elif op.op_type == "omit" and op.unit != "words" and state.has(t):
        state.remove(t)
    elif op.op_type in ("insert", "substitute") and op.new_paths:
        if op.op_type == "substitute" and state.has(t):
            state.remove(t)
        state.add(op.new_paths, origin, after=op.anchor_path)
    elif op.unit in ("words", "portion") and state.has(t):
        state.touch(t)
    elif op.op_type == "substitute" and not op.new_paths and state.has(t):
        state.remove(t)  # substituted unit with no counterpart: gone from the text


def report(name: str, ext: Extraction) -> str:
    resolved = sum(op.status == "resolved" for op in ext.ops)
    lines = [f"{name}: {len(ext.ops)} ops, {resolved} resolved, "
             f"{len(ext.ops) - resolved} unresolved"]
    by_type: dict[str, list[int]] = {}
    for op in ext.ops:
        by_type.setdefault(f"{op.op_type}/{op.unit}", [0, 0])[op.status == "unresolved"] += 1
    lines.append("  " + ", ".join(f"{k} {v[0]}+{v[1]}u" for k, v in sorted(by_type.items())))
    for op in ext.ops:
        if op.reason:
            lines.append(f"  unresolved {op.provision} {op.op_type} {op.target_path}: "
                         f"{op.reason}")
    lines += [f"  not an amendment: {p}" for p in ext.non_amending]
    lines += [f"  warning: {w}" for w in ext.warnings]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m opensansad.parse.amending_ops")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, help_ in (("show", "print the ops without writing anything"),
                        ("load", "replace the ops of every amending instrument of the act")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--act-id", type=int, default=1)
        p.add_argument("--version", default="1996-enacted", help="base clause-tree version")
        p.add_argument("--verbose", "-v", action="store_true", help="print every op")
    args = parser.parse_args(argv)

    from opensansad.parse.load_ops import run

    return run(args.act_id, args.version, write=args.cmd == "load", verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
