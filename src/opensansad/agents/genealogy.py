"""Genealogy Agent: resolves an instruction the rules left `unresolved` into lineage edges.

A `Case` is one instruction of one amending Act: its text, the old units it names (with their
text in the version it amends), and one `Slot` per new unit it quotes (its text and the paths
it may take). The agent answers, per slot, which old unit (if any) it descends from and where
it sits; old units no slot claims are omitted. It may also declare the instruction outside
the principal Act's genealogy. Answers are validated against the case: an unknown path, a
slot answered twice or an old unit claimed twice is refused and asked again, never repaired.
"""

from dataclasses import dataclass, field

from opensansad.agents.llm import LLM, LLMError, Role

OP_TYPES = ("insert", "substitute", "renumber")


@dataclass
class OldUnit:
    path: str
    text: str


@dataclass
class Slot:
    id: str
    unit: str | None  # node type of the quoted unit
    text: str
    paths: list[str]  # where it may sit; first = the rules' reading


@dataclass
class Case:
    id: str  # the instruction group: "<instrument>|<provision>"
    instrument: str
    provision: str
    instruction: str
    old: list[OldUnit]
    slots: list[Slot]
    context: list[str] = field(default_factory=list)  # resolved ops of the same instruction
    rule_reasons: list[str] = field(default_factory=list)


@dataclass
class Edge:
    instrument: str
    old: str | None
    new: str | None
    op_type: str
    source: str  # "rule" | "agent"
    group: str = ""  # the instruction it comes from: "<instrument>|<provision>"
    confidence: float | None = None
    rationale: str | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.instrument, self.old or "", self.new or "")


@dataclass
class Decision:
    case: str
    edges: list[Edge]
    out_of_scope: bool
    rationale: str
    attempts: int


SYSTEM = """You trace the lineage of provisions of an Indian statute through amending Acts.
You are given ONE amending instruction, the OLD units of the principal Act it names (as they
read just before this amendment) and the NEW units it quotes. Decide, for each NEW unit, which
OLD unit it descends from (null if it is new matter) and where it sits.

Rules:
- Lineage follows content, not labels. A new clause (b) whose words are old clause (a)'s
  descends from old (a), even though the labels differ.
- A new unit descends from an old unit only if it carries that unit's substance forward.
- An OLD unit no NEW unit descends from is omitted by the instruction.
- If the instruction amends a different instrument (not the principal Act), set
  "out_of_scope": true and give no mappings.
- Use only the slot ids, old paths and allowed paths given. Each slot exactly once; each old
  unit at most once.
Reply in JSON only:
{"out_of_scope": false, "mappings": [{"slot": "<id>", "path": "<one of its allowed paths>",
  "old": "<old path or null>", "op_type": "insert|substitute|renumber",
  "confidence": 0.0-1.0, "rationale": "<one sentence>"}], "rationale": "<one sentence>"}
op_type: "insert" when old is null, "substitute" when the unit replaces its old unit at the
same path, "renumber" when it carries an old unit forward under a different path."""


def render(case: Case, feedback: list[str]) -> str:
    out = [f"Amending Act: {case.instrument}, provision {case.provision}",
           f"Instruction: {case.instruction}", ""]
    if case.rule_reasons:
        out += ["The deterministic parser left this instruction unresolved because:"]
        out += [f"- {r}" for r in dict.fromkeys(case.rule_reasons)] + [""]
    if case.context:
        out += ["Already resolved from the same instruction:"] + [f"- {c}" for c in case.context]
        out.append("")
    out.append("OLD units:" if case.old else "OLD units: none")
    out += [f"- {u.path}: {u.text}" for u in case.old]
    out += ["", "NEW units (slots):"]
    out += [f"- slot {s.id} ({s.unit or 'unit'}), allowed paths {s.paths}: {s.text}"
            for s in case.slots] or ["- none"]
    if feedback:
        out += ["", "Your previous answer was checked and found inconsistent:"]
        out += [f"- {f}" for f in feedback]
        out.append("Reconsider the mapping in the light of these findings.")
    return "\n".join(out)


def validate(case: Case, answer: dict) -> tuple[list[Edge], bool, list[str]]:
    problems: list[str] = []
    scope = bool(answer.get("out_of_scope"))
    raw = answer.get("mappings") or []
    if not isinstance(raw, list):
        return [], scope, ["mappings is not a list"]
    if scope:
        if raw:
            problems.append("out_of_scope with mappings")
        return [], True, problems
    slots = {s.id: s for s in case.slots}
    olds = {u.path for u in case.old}
    seen_slots: set[str] = set()
    seen_old: set[str] = set()
    edges: list[Edge] = []
    for m in raw:
        if not isinstance(m, dict):
            problems.append(f"mapping is not an object: {m!r}")
            continue
        sid, path, old = str(m.get("slot")), m.get("path"), m.get("old")
        op = m.get("op_type")
        if sid not in slots:
            problems.append(f"unknown slot {sid!r}")
            continue
        if sid in seen_slots:
            problems.append(f"slot {sid} answered twice")
        seen_slots.add(sid)
        if path not in slots[sid].paths:
            problems.append(f"slot {sid}: path {path!r} not among {slots[sid].paths}")
        if old is not None and old not in olds:
            problems.append(f"slot {sid}: old {old!r} is not an OLD unit")
        if old is not None and old in seen_old:
            problems.append(f"old {old} claimed by two slots")
        if old is not None:
            seen_old.add(old)
        if op not in OP_TYPES or (op == "insert") != (old is None):
            problems.append(f"slot {sid}: op_type {op!r} does not fit old={old!r}")
        try:
            conf = min(1.0, max(0.0, float(m.get("confidence", 0.5))))
        except (TypeError, ValueError):
            conf = 0.5
        edges.append(Edge(case.instrument, old, path, op, "agent", case.id, conf,
                          str(m.get("rationale", ""))[:300]))
    missing = set(slots) - seen_slots
    if missing:
        problems.append(f"slots not answered: {sorted(missing)}")
    for old in sorted(olds - seen_old):
        edges.append(Edge(case.instrument, old, None, "omit", "agent", case.id, None,
                          "no new unit claims it"))
    return edges, False, problems


def propose(llm: LLM, case: Case, *, role: Role = "reasoning", feedback: list[str] = (),
            attempts: int = 3) -> Decision:
    notes = list(feedback)
    for attempt in range(1, attempts + 1):
        answer = llm.json(role, SYSTEM, render(case, notes))
        edges, scope, problems = validate(case, answer)
        if not problems:
            return Decision(case.id, edges, scope, str(answer.get("rationale", ""))[:300],
                            attempt)
        notes = list(feedback) + [f"invalid answer: {p}" for p in problems]
    raise LLMError(f"{case.id}: no valid answer in {attempts} attempts: {problems}")
