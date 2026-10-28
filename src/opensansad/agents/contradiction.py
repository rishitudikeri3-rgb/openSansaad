"""Contradiction Agent: checks the lineage as a whole and sends suspect mappings back.

Detection is deterministic (every check is reproducible and cheap); judgment is the model's:
given the conflicts and the texts involved, it names which instruction's mapping is likely
wrong, or calls a conflict a false alarm (new matter may legitimately read unlike anything
old). The workflow re-runs the Genealogy Agent on the instructions it names.

Checks:
- mispaired: a new unit reads much more like another old unit of the same instruction than
  like the one it is mapped from (or is mapped as new matter while repeating an omitted one)
- old_twice: one old unit is the source of two new units of one Act
- kind: a substitution or renumbering changes the kind of unit (Explanation -> clause)
- dangling: an Act omits a unit that a later Act still amends, and nothing recreates it
- scope: an edge comes from an instruction the rules found to amend another instrument
"""

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from opensansad.agents.genealogy import Edge
from opensansad.agents.llm import LLM, Role

MISPAIR_MARGIN = 0.3  # alternative must beat the mapped old unit by this much ...
MISPAIR_FLOOR = 0.6  # ... and be at least this similar
REPEAT_FLOOR = 0.8  # "new matter" this similar to an omitted old unit is suspect


def similarity(a: str, b: str) -> float:
    wa, wb = re.findall(r"\w+", a.lower()), re.findall(r"\w+", b.lower())
    if not wa or not wb:
        return 0.0
    return SequenceMatcher(None, wa, wb, autojunk=False).ratio()


def kind(path: str) -> str:
    last = path.rsplit(".", 1)[-1]
    return re.sub(r"[\d(].*$", "", last) or last


@dataclass
class Conflict:
    id: str
    check: str
    groups: list[str]  # instruction groups involved ("<instrument>|<provision>")
    message: str

    @property
    def signature(self) -> tuple[str, str]:
        return (self.check, self.message)


@dataclass
class Texts:
    """Unit texts: `old[(instrument, path)]` as the unit read before that Act,
    `new[(instrument, path)]` as the Act quotes it."""
    old: dict[tuple[str, str], str] = field(default_factory=dict)
    new: dict[tuple[str, str], str] = field(default_factory=dict)


def detect(edges: list[Edge], texts: Texts, later_refs: dict[str, set[str]],
           order: list[str], scope_flags: dict[str, str] | None = None) -> list[Conflict]:
    """`later_refs[instrument]` = paths that later Acts target; `order` = instruments in
    order; `scope_flags[group]` = why the rules found the group amends another instrument."""
    out: list[Conflict] = []
    by_group: dict[str, list[Edge]] = {}
    for e in edges:
        by_group.setdefault(e.group, []).append(e)

    for g, es in by_group.items():
        inst = es[0].instrument
        olds = [e.old for e in es if e.old and (inst, e.old) in texts.old]
        for e in es:
            new_text = texts.new.get((inst, e.new)) if e.new else None
            if not new_text:
                continue
            scored = sorted(((similarity(texts.old[(inst, o)], new_text), o) for o in olds),
                            reverse=True)
            if not scored:
                continue
            best, best_old = scored[0]
            if e.old is not None and e.op_type in ("substitute", "renumber"):
                mine = similarity(texts.old.get((inst, e.old), ""), new_text)
                if best_old != e.old and best >= MISPAIR_FLOOR and best >= mine + MISPAIR_MARGIN:
                    out.append(Conflict("", "mispaired", [g],
                                        f"{inst}: new {e.new} is mapped from old {e.old} "
                                        f"(similarity {mine:.2f}) but reads like old "
                                        f"{best_old} ({best:.2f})"))
            elif e.old is None and best >= REPEAT_FLOOR:
                omitted = {x.old for x in es if x.new is None}
                if best_old in omitted:
                    out.append(Conflict("", "mispaired", [g],
                                        f"{inst}: new {e.new} is mapped as new matter but "
                                        f"repeats omitted old {best_old} ({best:.2f})"))

    by_inst: dict[str, dict[str, list[str]]] = {}
    for e in edges:
        if e.old and e.new and e.op_type in ("substitute", "renumber"):
            by_inst.setdefault(e.instrument, {}).setdefault(e.old, []).append(e.group)
            # renumbering may change the kind ("section 9 ... as sub-section (1)")
            if e.op_type == "substitute" and kind(e.old) != kind(e.new):
                out.append(Conflict("", "kind", [e.group],
                                    f"{e.instrument}: {e.old} ({kind(e.old)}) becomes "
                                    f"{e.new} ({kind(e.new)})"))
    for inst, olds in by_inst.items():
        for old, gs in olds.items():
            if len(gs) > 1:
                out.append(Conflict("", "old_twice", sorted(set(gs)),
                                    f"{inst}: old {old} is the source of {len(gs)} new units"))

    for e in edges:
        if e.old and e.new is None and e.op_type == "omit":
            refs = later_refs.get(e.instrument, set())
            recreated = any(x.new == e.old and order.index(x.instrument) >=
                            order.index(e.instrument) and x is not e for x in edges)
            if e.old in refs and not recreated:
                out.append(Conflict("", "dangling", [e.group],
                                    f"{e.instrument} omits {e.old}, but a later Act still "
                                    f"amends {e.old}"))
    for g, why in (scope_flags or {}).items():
        mine = [e for e in edges if e.group == g]
        if mine:
            out.append(Conflict("", "scope", [g],
                                f"{g} yields {[e.key[1:] for e in mine]}, but the parser "
                                f"found: {why}"))
    for i, c in enumerate(out):
        c.id = f"C{i + 1}"
    return out


SYSTEM = """You review the lineage of provisions of an Indian statute traced through its
amending Acts. Automatic checks found the conflicts below. For each, decide whether it shows a
mapping that is likely WRONG (name the instruction group to revise) or is a FALSE ALARM (for
example: new matter may legitimately resemble old text; an omission may be intended).
Judge on the texts given. Reply in JSON only:
{"verdicts": [{"conflict": "<id>", "false_alarm": true|false, "revise": ["<group>", ...],
  "reason": "<one sentence>"}]}
"revise" must list only groups named in that conflict; empty if false_alarm."""


def render(conflicts: list[Conflict], edges: list[Edge], texts: Texts) -> str:
    out = []
    involved = {g for c in conflicts for g in c.groups}
    for c in conflicts:
        out.append(f"{c.id} [{c.check}] groups {c.groups}: {c.message}")
    out += ["", "Current mappings of the groups involved:"]
    for e in edges:
        g = e.group
        if g not in involved:
            continue
        old_t = texts.old.get((e.instrument, e.old), "") if e.old else ""
        new_t = texts.new.get((e.instrument, e.new), "") if e.new else ""
        out.append(f"- [{g}] {e.op_type}: old {e.old} -> new {e.new} ({e.source})")
        if old_t:
            out.append(f"    old text: {old_t[:400]}")
        if new_t:
            out.append(f"    new text: {new_t[:400]}")
    return "\n".join(out)


@dataclass
class Verdict:
    conflict: Conflict
    false_alarm: bool
    revise: list[str]
    reason: str


def adjudicate(llm: LLM, conflicts: list[Conflict], edges: list[Edge], texts: Texts, *,
               role: Role = "reasoning") -> list[Verdict]:
    answer = llm.json(role, SYSTEM, render(conflicts, edges, texts))
    by_id = {c.id: c for c in conflicts}
    verdicts: list[Verdict] = []
    for v in answer.get("verdicts") or []:
        if not isinstance(v, dict) or v.get("conflict") not in by_id:
            continue
        c = by_id[v["conflict"]]
        revise = [g for g in (v.get("revise") or []) if g in c.groups]
        fa = bool(v.get("false_alarm")) or not revise
        verdicts.append(Verdict(c, fa, [] if fa else revise, str(v.get("reason", ""))[:300]))
    answered = {v.conflict.id for v in verdicts}
    for c in conflicts:  # unanswered conflict: revise every group it names (fail safe)
        if c.id not in answered:
            verdicts.append(Verdict(c, False, list(c.groups), "no verdict given"))
    return verdicts
