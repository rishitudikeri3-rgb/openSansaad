"""Clause tree of an act's text: the nodes every later stage addresses by path.

Input is a checked transcription (`parse/transcription.py`): one line per printed line,
`[[page: N]]` / `[[margin: ...]]` / `[[flush]]` markers. Output is a list of `Node`s in
document order, each with a stable path (CLAUDE.md rule 6):

    long_title, preamble, enacting_formula
    pt1, pt1.ch2                    Parts and Chapters (containers)
    s9, s9.ss1, s9.ss1.cl(a), s9.ss1.cl(a).sc(ii)
    s9.ss1.p1, s9.ss1.expl1         provisos and Explanations, numbered within their parent
    sch1, sch1.art5, sch1.art5.para1, sch1.art5.para1.cl(a), sch2.para1

Sections and Schedules are top-level paths whatever Part or Chapter holds them, so a path
survives a Chapter being renumbered; `parent_path` records the container.

Deterministic rules, with every judgment call written into the node's `meta` (and the
report) rather than made silently:

- A proviso attaches to the deepest open provision (the clause or sub-clause it follows,
  else the sub-section or section): `meta.attached = "deepest open provision"`.
- An Explanation attaches to the open sub-section, else the section (in a Schedule, the
  paragraph or article); one that begins "In this section" attaches to the section.
- "(i)" after "(h)", "(v)" after "(u)", "(x)" after "(w)" are clauses; otherwise roman.
- A list item continues the sequence of the nearest open list of its kind, closing any
  proviso in between; "(a)" / "(i)" out of sequence opens a new list under the deepest node.
- `[[flush]]` text goes back to the provision that opened the list it follows.
- Margin notes head the next section, sub-section or article; margin act citations
  ("5 of 1908.") go into `meta.margin_citations` of the node whose text follows them.

    uv run python -m opensansad.parse.clause_tree show data/transcriptions/NAME.txt [PATH]
    uv run python -m opensansad.parse.clause_tree load data/transcriptions/NAME.txt
"""

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from opensansad.parse.transcription import FLUSH, MARGIN, PAGE, ROMAN, load_meta

NUMBER_WORDS = {"FIRST": 1, "SECOND": 2, "THIRD": 3, "FOURTH": 4, "FIFTH": 5, "SIXTH": 6,
                "SEVENTH": 7, "EIGHTH": 8, "NINTH": 9, "TENTH": 10}

_CITATION = re.compile(r"^\d+ of \d{4}\.?$")
_PART = re.compile(r"^PART ([IVX]+)$")
_CHAPTER = re.compile(r"^CHAPTER ([IVX]+)$")
_SCHEDULE = re.compile(r"^THE (\w+) SCHEDULE$")
_SEE = re.compile(r"^\(See (.+)\)$")
_ARTICLE_HEAD = re.compile(r"^ARTICLE ([IVX]+)$")
_ARTICLE_INLINE = re.compile(r"^Article (\d+)\.\s*—\s*")
_SECTION = re.compile(r"^(\d+[A-Z]*)\.\s+")
_PARAGRAPH = re.compile(r"^(\d+)\.\s+")
_SUB = re.compile(r"^\((\d+)\)\s*")
_ITEM = re.compile(r"^\(([a-z]+)\)\s*")
_PROVISO = re.compile(r"^Provided\b")
_EXPLANATION = re.compile(r"^Explanation(?:\s+(\d+))?\.\s*—\s*")
_GROUP = re.compile(r"^[A-Z][A-Z ]+$")  # a heading such as PRELIMINARY
_REFERENCE_END = re.compile(
    r"(?:\b(?:sub-sections?|sections?|clauses?|sub-clauses?|articles?|paragraphs?"
    r"|of|to|in|under)|\)\s+(?:and|or))$")
SUBCLAUSES = "i ii iii iv v vi vii viii ix x xi xii xiii xiv xv".split()
_LETTER_BEFORE_ROMAN = {"i": "h", "v": "u", "x": "w"}

LIST_KINDS = ("clause", "subclause")


@dataclass
class Node:
    path: str
    node_type: str
    number: str | None
    parent: "Node | None"
    heading: str | None = None
    segments: list[tuple[int, int]] = field(default_factory=list)  # own text, file offsets
    breaks: set[int] = field(default_factory=set)  # segment indexes that follow a child's text
    span: list[int] = field(default_factory=lambda: [-1, -1])  # whole node, file offsets
    pages: list[int] = field(default_factory=list)
    children: list["Node"] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    container: str | None = None  # Part / Chapter path for sections (parent_path)

    @property
    def parent_path(self) -> str | None:
        return self.parent.path if self.parent else self.container

    def text(self, source: str) -> str:
        """Own text; runs separated by a child's text (a list's flush tail) on new lines."""
        runs: list[list[str]] = []
        for i, (a, b) in enumerate(self.segments):
            if not runs or i in self.breaks:
                runs.append([])
            runs[-1].append(source[a:b])
        return "\n".join(join_lines(run) for run in runs)


def join_lines(lines: list[str]) -> str:
    """Printed lines as running text: a line-end hyphen joins without a space ("sub-" +
    "section"; every line-end hyphen in a checked transcription is a real one)."""
    out = ""
    for line in (ln.strip() for ln in lines):
        if not line:
            continue
        out += line if not out or out.endswith(("-", "—")) else " " + line
    return out


@dataclass
class Tree:
    nodes: list[Node]
    warnings: list[str]
    source: str

    def by_path(self) -> dict[str, Node]:
        return {n.path: n for n in self.nodes}


class _Parser:
    def __init__(self, source: str) -> None:
        self.source = source
        self.nodes: list[Node] = []
        self.warnings: list[str] = []
        self.stack: list[Node] = []  # open provisions, outermost first
        self.part: Node | None = None
        self.chapter: Node | None = None
        self.schedule: Node | None = None
        self.expect_title: Node | None = None
        self.pending_heading: str | None = None
        self.pending_citations: list[str] = []
        self.pending_group: str | None = None
        self.flush = False
        self.page = 0
        self.line_no = 0
        self.previous = ""
        self.front: Node | None = None  # long title / preamble / enacting formula, while open
        self.body_started = False
        self.last_owner: Node | None = None
        self.cursor = -1  # file offset of the marker being read: where a new node's span starts

    # -- helpers -----------------------------------------------------------------------
    def warn(self, msg: str) -> None:
        self.warnings.append(f"p{self.page} L{self.line_no}: {msg}")

    def new(self, path: str, node_type: str, number: str | None, parent: Node | None,
            **kw) -> Node:
        if any(n.path == path for n in self.nodes):
            self.warn(f"duplicate path {path}")
            path = f"{path}~{self.line_no}"
        node = Node(path, node_type, number, parent, **kw)
        if parent:
            parent.children.append(node)
        self.nodes.append(node)
        if self.cursor >= 0:
            self.touch(node, self.cursor, self.cursor)
        return node

    def own(self, node: Node, start: int, end: int) -> None:
        if start < end:
            if node.segments and self.last_owner is not node:
                node.breaks.add(len(node.segments))
            node.segments.append((start, end))
            self.last_owner = node
            if self.pending_citations:
                node.meta.setdefault("margin_citations", []).extend(self.pending_citations)
                self.pending_citations = []
        self.touch(node, start, end)

    def touch(self, node: Node | None, start: int, end: int) -> None:
        """Extend the spans and pages of a node and all its ancestors."""
        while node is not None:
            if node.span[0] < 0:
                node.span[0] = start
            node.span[1] = max(node.span[1], end)
            if self.page and self.page not in node.pages:
                node.pages.append(self.page)
            node = node.parent

    def take_heading(self, node: Node) -> None:
        if self.pending_heading:
            node.heading = self.pending_heading
            self.pending_heading = None

    def top(self) -> Node | None:
        return self.stack[-1] if self.stack else None

    def pop_to(self, node: Node) -> None:
        while self.stack and self.stack[-1] is not node:
            self.stack.pop()

    def number_in(self, parent: Node | None, node_type: str) -> int:
        return 1 + sum(c.node_type == node_type for c in (parent.children if parent else []))

    # -- line handling -----------------------------------------------------------------
    def feed(self, line: str, start: int) -> None:
        self.line_no += 1
        stripped = line.strip()
        if not stripped:
            return
        if m := PAGE.match(stripped):
            self.page = int(m.group(1))
            return
        if stripped == FLUSH:
            self.flush = True
            return
        if MARGIN.fullmatch(stripped):
            note = stripped[len("[[margin: "):-2].strip()
            if _CITATION.match(note):
                self.pending_citations.append(note.rstrip("."))
            elif note == "Preamble.":
                pass  # the preamble node is opened by its first line
            else:
                self.pending_heading = f"{self.pending_heading} {note}" if self.pending_heading \
                    else note
            return
        lead = len(line) - len(line.lstrip())
        begin, end = start + lead, start + lead + len(stripped)
        self.cursor = begin
        continues = bool(_REFERENCE_END.search(self.previous))
        self.previous = stripped
        if not self.body_started and self.front_matter(stripped, begin, end):
            return
        if self.expect_title is not None:
            node, self.expect_title = self.expect_title, None
            if not node.heading and not re.match(r"^(\d|\(|Provided|Explanation)", stripped):
                node.heading = stripped
                self.touch(node, begin, end)
                return
        if not continues and self.structure(stripped, begin, end):
            return
        self.body(stripped, begin, end, continues)

    def front_matter(self, s: str, begin: int, end: int) -> bool:
        """Long title, preamble and enacting formula. False once the body begins."""
        if s.startswith("BE it enacted") or s.startswith("BE IT ENACTED"):
            self.front = self.new("enacting_formula", "enacting_formula", None, None)
        elif s.startswith("WHEREAS") and (self.front is None
                                         or self.front.node_type != "preamble"):
            self.front = self.new("preamble", "preamble", None, None)
        elif re.match(r"^(An Act )?to [a-z]", s) and self.front is None:
            self.front = self.new("long_title", "long_title", None, None)
        elif self.front is None:
            return True  # front matter of the print ("Bill No. ...", the Bill's title)
        elif self.front.node_type == "enacting_formula" and self.front.segments and (
                self.source[self.front.segments[-1][0]:self.front.segments[-1][1]]
                .rstrip().endswith(("—", ":", "-"))):
            self.body_started = True
            return False
        self.own(self.front, begin, end)
        return True

    def structure(self, s: str, begin: int, end: int) -> bool:
        """Parts, Chapters, Schedules, Articles and group headings."""
        if m := _PART.match(s):
            n = ROMAN[m.group(1)]
            self.part = self.new(f"pt{n}", "part", m.group(1), None)
            self.chapter, self.schedule, self.stack = None, None, []
            self.touch(self.part, begin, end)
            self.expect_title = self.part
            return True
        if m := _CHAPTER.match(s):
            n = ROMAN[m.group(1)]
            base = self.part.path if self.part else ""
            self.chapter = self.new(f"{base}.ch{n}".lstrip("."), "chapter", m.group(1), self.part)
            self.stack = []
            self.touch(self.chapter, begin, end)
            self.expect_title = self.chapter
            return True
        if m := _SCHEDULE.match(s):
            n = NUMBER_WORDS.get(m.group(1))
            if n is None:
                self.warn(f"schedule ordinal {m.group(1)!r} not known")
                n = 1 + sum(x.node_type == "schedule" for x in self.nodes)
            self.schedule = self.new(f"sch{n}", "schedule", str(n), None)
            self.part = self.chapter = None
            self.stack = [self.schedule]
            self.touch(self.schedule, begin, end)
            return True
        if self.schedule is not None:
            if m := _SEE.match(s):
                self.schedule.meta["see"] = m.group(1)
                self.touch(self.schedule, begin, end)
                return True
            if _GROUP.match(s) and not self.schedule.heading:  # the treaty's title
                self.schedule.heading = s
                self.touch(self.schedule, begin, end)
                return True
            if m := _ARTICLE_HEAD.match(s):
                self.article(str(ROMAN[m.group(1)]), m.group(1), begin, end)
                return True
            if m := _ARTICLE_INLINE.match(s):
                node = self.article(m.group(1), m.group(1), begin, begin + m.end())
                self.body(s[m.end():], begin + m.end(), end, False, opened=node)
                return True
            return False
        if _GROUP.match(s) and not _PART.match(s):
            self.pending_group = s
            return True
        return False

    def article(self, n: str, printed: str, begin: int, end: int) -> Node:
        sch = self.schedule
        assert sch is not None
        node = self.new(f"{sch.path}.art{n}", "article", printed, sch)
        self.take_heading(node)
        self.stack = [sch, node]
        self.touch(node, begin, end)
        return node

    def body(self, s: str, begin: int, end: int, continues: bool,
             opened: Node | None = None) -> None:
        """Numbered provisions, provisos, Explanations and running text."""
        pos = 0
        if not continues and opened is None:
            if self.schedule is None and (m := _SECTION.match(s)):
                self.section(m.group(1))
                pos = m.end()
            elif self.schedule is not None and (m := _PARAGRAPH.match(s)):
                self.paragraph(m.group(1))
                pos = m.end()
        # markers at the start of the rest of the line: "(1)", then "(a)" ("2. (1) (a) ...")
        while not continues or pos > 0 or opened is not None:
            rest = s[pos:]
            self.cursor = begin + pos
            if m := _SUB.match(rest):
                if self.schedule is not None:
                    self.paragraph(m.group(1))
                else:
                    self.subsection(m.group(1))
                pos += m.end()
                continue
            if (m := _ITEM.match(rest)) and self.list_item(m.group(1)):
                pos += m.end()
                continue
            break
        rest = s[pos:]
        if pos == 0 and not continues:
            if _PROVISO.match(rest):
                self.proviso()
            elif m := _EXPLANATION.match(rest):
                self.explanation(m.group(1), rest[m.end():])
                pos += m.end()
        target = self.top()
        if self.flush and pos == 0:
            self.flush = False
            target = self.list_opener()
        if target is None:
            self.warn(f"text outside any provision: {s[:50]!r}")
            return
        self.own(target, begin + pos, end)

    # -- node creation -----------------------------------------------------------------
    def section(self, number: str) -> None:
        container = self.chapter or self.part
        node = self.new(f"s{number}", "section", number, None, container=(
            container.path if container else None))
        self.take_heading(node)
        if self.pending_group:
            node.meta["group_heading"] = self.pending_group
            self.pending_group = None
        self.stack = [node]

    def subsection(self, number: str) -> None:
        section = next((n for n in self.stack if n.node_type == "section"), None)
        if section is None:
            self.warn(f"sub-section ({number}) outside a section")
            return
        self.pop_to(section)
        node = self.new(f"{section.path}.ss{number}", "subsection", number, section)
        self.take_heading(node)
        self.stack.append(node)

    def paragraph(self, number: str) -> None:
        holder = next((n for n in reversed(self.stack)
                       if n.node_type in ("article", "schedule")), None)
        if holder is None:
            self.warn(f"paragraph {number} outside a Schedule")
            return
        self.pop_to(holder)
        node = self.new(f"{holder.path}.para{number}", "paragraph", number, holder)
        self.take_heading(node)
        self.stack.append(node)

    def list_item(self, label: str) -> bool:
        kind = "clause"
        if label in SUBCLAUSES:
            before = _LETTER_BEFORE_ROMAN.get(label)
            open_letter = any(n.node_type == "clause" and n.number == before
                              for n in self.stack)
            if not open_letter:
                kind = "subclause"
        elif len(label) != 1:
            return False  # "(ab)": not a marker this parser knows
        # continue the nearest open list of this kind
        for i in range(len(self.stack) - 1, -1, -1):
            n = self.stack[i]
            if n.node_type == kind and _next_label(kind, n.number) == label:
                parent = n.parent
                assert parent is not None
                self.pop_to(parent)
                return self._item(kind, label, parent)
        parent = self.top()
        if parent is None:
            self.warn(f"({label}) outside any provision")
            return False
        if label not in ("a", "i"):
            self.warn(f"{kind} ({label}) does not continue any open list; under {parent.path}")
            parent.meta.setdefault("notes", []).append(f"({label}) out of sequence")
        return self._item(kind, label, parent)

    def _item(self, kind: str, label: str, parent: Node) -> bool:
        prefix = "cl" if kind == "clause" else "sc"
        node = self.new(f"{parent.path}.{prefix}({label})", kind, label, parent)
        self.stack.append(node)
        return True

    def proviso(self) -> None:
        parent = self.top()
        if parent is None:
            self.warn("proviso outside any provision")
            return
        # "Provided further that" is a second proviso to the same provision
        if parent.node_type == "proviso":
            self.stack.pop()
            parent = self.top()
            assert parent is not None
        node = self.new(f"{parent.path}.p{self.number_in(parent, 'proviso')}", "proviso",
                        str(self.number_in(parent, "proviso")), parent)
        if parent.node_type in LIST_KINDS:
            node.meta["attached"] = "deepest open provision"
        self.stack.append(node)

    def explanation(self, printed: str | None, text: str) -> None:
        # one that says "In this section" belongs to the section, not the last sub-section
        kinds = ("section",) if text.startswith("In this section") else (
            "subsection", "section", "paragraph", "article")
        parent = next((n for n in reversed(self.stack) if n.node_type in kinds), None)
        if parent is None:
            self.warn("Explanation outside any provision")
            return
        self.pop_to(parent)
        k = self.number_in(parent, "explanation")
        node = self.new(f"{parent.path}.expl{k}", "explanation", printed or str(k), parent)
        if kinds == ("section",):
            node.meta["attached"] = "says 'In this section'"
        self.stack.append(node)

    def list_opener(self) -> Node | None:
        """For `[[flush]]` text: the provision that holds the list the text follows."""
        while self.stack and self.stack[-1].node_type in LIST_KINDS + ("proviso",):
            self.stack.pop()
        return self.top()


def _next_label(kind: str, label: str | None) -> str | None:
    if label is None:
        return None
    if kind == "clause":
        return chr(ord(label) + 1) if len(label) == 1 and label < "z" else None
    i = SUBCLAUSES.index(label) if label in SUBCLAUSES else -1
    return SUBCLAUSES[i + 1] if 0 <= i < len(SUBCLAUSES) - 1 else None


def parse(source: str) -> Tree:
    """The clause tree of a transcription (see module docstring)."""
    p = _Parser(source)
    offset = 0
    for line in source.splitlines(keepends=True):
        p.feed(line.rstrip("\n"), offset)
        offset += len(line)
    if p.pending_heading:
        p.warn(f"margin note never placed: {p.pending_heading!r}")
    for n in p.nodes:
        if not n.segments and not n.children and n.node_type not in ("part", "chapter"):
            p.warnings.append(f"{n.path}: no text")
    return Tree(p.nodes, p.warnings, source)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def report(tree: Tree) -> str:
    counts: dict[str, int] = {}
    for n in tree.nodes:
        counts[n.node_type] = counts.get(n.node_type, 0) + 1
    lines = [f"{len(tree.nodes)} nodes: " + ", ".join(f"{v} {k}" for k, v in counts.items())]
    sections = [n.number for n in tree.nodes if n.node_type == "section"]
    lines.append(f"sections {sections[0]}–{sections[-1]}" if sections else "no sections")
    for n in tree.nodes:
        if n.meta.get("attached"):
            lines.append(f"{n.node_type} {n.path}: attached to {n.parent_path}"
                         f" ({n.meta['attached']})")
    lines += [f"warning: {w}" for w in tree.warnings]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m opensansad.parse.clause_tree")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("show", help="print the tree (or one node) without writing anything")
    p.add_argument("transcription", type=Path)
    p.add_argument("path", nargs="?")
    p = sub.add_parser("load", help="replace this version's clauses in Postgres and Neo4j")
    p.add_argument("transcription", type=Path)
    p.add_argument("--version-label", default=None,
                   help="default: from the transcription's .toml (version_label)")
    p.add_argument("--no-graph", action="store_true", help="Postgres only")
    p.add_argument("--force", action="store_true",
                   help="replace even if genealogy edges point at this version")
    args = parser.parse_args(argv)

    source = args.transcription.read_text()
    tree = parse(source)
    if args.cmd == "show":
        nodes = tree.nodes if not args.path else [
            n for n in tree.nodes if n.path == args.path or n.path.startswith(args.path + ".")]
        for n in nodes:
            depth = 0 if n.parent is None else n.path.count(".") - (
                n.path.count(".") - n.parent.path.count(".") - 1)
            head = f" [{n.heading}]" if n.heading else ""
            text = n.text(source).replace("\n", " ⏎ ")
            print(f"{'  ' * depth}{n.path}{head}: {text[:90]}")
        print(report(tree), file=sys.stderr)
        return 0

    from opensansad.parse.load import LoadRefused, load

    print(report(tree))
    meta = load_meta(args.transcription.with_suffix(".toml"))
    try:
        result = load(tree, args.transcription, meta, version_label=args.version_label,
                      graph=not args.no_graph, force=args.force)
    except LoadRefused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
