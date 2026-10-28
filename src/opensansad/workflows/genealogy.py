"""Genealogy workflow (LangGraph): rules → Genealogy Agent → Contradiction loop.

1. `corpus()` re-runs the 4c extractor over the amending Acts in order (carrying the known
   paths), keeping each Act's state *before* it so old units have the text they had then.
   Resolved ops become `rule` edges; every instruction group can be rendered as a `Case`.
2. `genealogy` asks the Genealogy Agent about each group the rules left unresolved.
3. `contradiction` runs the checks over all edges (rule and agent); the model judges each
   conflict; `revise` re-asks the Genealogy Agent (reasoning model) about the groups it names,
   with the findings as feedback. Loops until no conflict is left or `max_rounds`.

Ordinances are not traced (the Acts re-enact them). Every revision is recorded.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypedDict

from opensansad.agents.contradiction import Texts, adjudicate, detect
from opensansad.agents.genealogy import Case, Decision, Edge, OldUnit, Slot, propose
from opensansad.agents.llm import LLM, LLMError, Role
from opensansad.parse.amending_ops import Op, PathState, extract


@dataclass
class Group:
    id: str
    instrument: str
    provision: str
    instruction: str
    ops: list[Op]
    before: PathState

    @property
    def unresolved(self) -> bool:
        return any(op.status == "unresolved" for op in self.ops)


@dataclass
class Corpus:
    groups: dict[str, Group]
    order: list[str]  # instruments, in order
    texts: Texts
    later_refs: dict[str, set[str]]
    scope_flags: dict[str, str] = field(default_factory=dict)


def subtree_text(state: PathState, path: str) -> str:
    return "\n".join(t for p in state.subtree(path) if (t := state.own.get(p)))


def corpus(instruments: list[tuple[str, str]], base: PathState) -> Corpus:
    """`instruments` = (label "N/YYYY", extracted text) of each amending Act, in order."""
    state = base.copy()
    groups: dict[str, Group] = {}
    texts = Texts()
    targets: dict[str, set[str]] = {}
    order = []
    for label, source in instruments:
        before = state.copy()
        ext = extract(source, state, label)
        order.append(label)
        targets[label] = {op.target_path for op in ext.ops if op.target_path}
        for op in ext.ops:
            gid = f"{label}|{op.provision}"
            g = groups.setdefault(gid, Group(gid, label, op.provision,
                                             " ".join(source[op.span[0]:op.span[1]].split()),
                                             [], before))
            g.ops.append(op)
            t = op.target_path
            if t and before.has(t) and (label, t) not in texts.old:
                texts.old[(label, t)] = subtree_text(before, t)
            if t and op.new_paths and op.new_text:
                texts.new[(label, t)] = op.new_text
    later = {inst: set().union(*(targets[o] for o in order[i + 1:]))
             for i, inst in enumerate(order)}
    flags = {gid: op.reason for gid, g in groups.items() for op in g.ops
             if op.reason and "another instrument" in op.reason}
    return Corpus(groups, order, texts, later, flags)


def rule_edges(g: Group) -> list[Edge]:
    out = []
    for op in g.ops:
        if op.status != "resolved" or op.target_path is None:
            continue
        t = op.target_path
        if op.op_type == "insert" and op.unit not in ("words",):
            old, new = None, t
        elif op.op_type == "omit" and op.unit not in ("words", "portion"):
            old, new = t, None
        elif op.op_type == "renumber":
            old, new = t, op.new_text
        else:
            old, new = t, t
        out.append(Edge(g.instrument, old, new, op.op_type, "rule", g.id, 1.0))
    return out


UNIT_OPS = ("insert", "substitute")


def case(g: Group) -> Case:
    """The group as a question: old units it names, new units it quotes."""
    olds, slots, context = [], [], []
    for op in g.ops:
        t = op.target_path
        if t and op.unit not in ("words", "portion") and g.before.has(t) and \
                op.op_type in ("substitute", "omit") and t not in [u.path for u in olds]:
            olds.append(OldUnit(t, subtree_text(g.before, t)))
        if t and op.op_type in UNIT_OPS and op.new_text and op.unit not in ("words", "portion"):
            slots.append(Slot(f"N{len(slots) + 1}", op.unit, op.new_text, [t]))
        elif op.status == "resolved":
            context.append(f"{op.op_type} {op.unit or ''} at {t}: {(op.new_text or '')[:80]}")
    # an Explanation / proviso quoted with other new units may belong to one of them
    for s in slots:
        last = s.paths[0].rsplit(".", 1)[-1]
        if s.unit in ("explanation", "proviso") and (last.startswith("expl") or
                                                      last.startswith("p")):
            suffix = "expl1" if s.unit == "explanation" else "p1"
            for other in slots:
                if other is not s and other.unit not in ("explanation",):
                    alt = f"{other.paths[0]}.{suffix}"
                    if alt not in s.paths:
                        s.paths.append(alt)
    return Case(g.id, g.instrument, g.provision, g.instruction, olds, slots, context,
                [op.reason for op in g.ops if op.reason])


# -- the graph --------------------------------------------------------------------------------

class State(TypedDict):
    edges: list[Edge]
    decisions: dict[str, Decision]
    round: int
    to_revise: dict[str, list[str]]  # group -> findings
    dismissed: list[tuple[str, str]]  # conflict signatures judged false alarms
    conflicts: list[list[dict]]  # per round
    revisions: list[dict]
    errors: list[str]


@dataclass
class Workflow:
    corpus: Corpus
    llm: LLM
    first_role: Role = "reasoning"
    revise_role: Role = "reasoning"
    max_rounds: int = 3
    contradiction: bool = True
    trace: list[str] = field(default_factory=list)

    def _replace(self, edges: list[Edge], gid: str, new: list[Edge]) -> list[Edge]:
        return [e for e in edges if e.group != gid] + new

    def genealogy(self, state: State) -> State:
        edges = [e for g in self.corpus.groups.values() for e in rule_edges(g)]
        for gid, g in self.corpus.groups.items():
            if not g.unresolved:
                continue
            try:
                d = propose(self.llm, case(g), role=self.first_role,
                            attempts=3 if self.contradiction else 1)
            except LLMError as e:
                state["errors"].append(str(e))
                continue
            state["decisions"][gid] = d
            edges = self._replace(edges, gid, d.edges)
            self.trace.append(f"genealogy {gid}: {[e.key[1:] for e in d.edges]}"
                              f"{' out of scope' if d.out_of_scope else ''}")
        state["edges"] = edges
        return state

    def check(self, state: State) -> State:
        conflicts = [c for c in detect(state["edges"], self.corpus.texts,
                                       self.corpus.later_refs, self.corpus.order,
                                       self.corpus.scope_flags)
                     if c.signature not in state["dismissed"]]
        state["conflicts"].append([vars(c) for c in conflicts])
        state["to_revise"] = {}
        if conflicts:
            verdicts = adjudicate(self.llm, conflicts, state["edges"], self.corpus.texts)
            for v in verdicts:
                self.trace.append(f"contradiction {v.conflict.id} {v.conflict.message}: "
                                  f"{'false alarm' if v.false_alarm else 'revise ' + str(v.revise)}"
                                  f" ({v.reason})")
                if v.false_alarm:
                    state["dismissed"].append(v.conflict.signature)
                for gid in v.revise:
                    state["to_revise"].setdefault(gid, []).append(
                        f"{v.conflict.message} — reviewer: {v.reason}")
        return state

    def revise(self, state: State) -> State:
        state["round"] += 1
        edges = state["edges"]
        for gid, findings in state["to_revise"].items():
            before = sorted(e.key for e in edges if e.group == gid)
            try:
                d = propose(self.llm, case(self.corpus.groups[gid]), role=self.revise_role,
                            feedback=findings)
            except LLMError as e:
                state["errors"].append(str(e))
                continue
            state["decisions"][gid] = d
            edges = self._replace(edges, gid, d.edges)
            after = sorted(e.key for e in d.edges)
            state["revisions"].append({"round": state["round"], "group": gid,
                                       "findings": findings, "before": before,
                                       "after": after, "changed": before != after})
            self.trace.append(f"revise {gid}: {before} -> {after}")
        state["edges"] = edges
        return state

    def _next(self, state: State) -> str:
        if state["to_revise"] and state["round"] < self.max_rounds:
            return "revise"
        return "end"

    def run(self) -> State:
        from langgraph.graph import END, START, StateGraph

        graph = StateGraph(State)
        graph.add_node("genealogy", self.genealogy)
        if self.contradiction:
            graph.add_node("contradiction", self.check)
            graph.add_node("revise", self.revise)
            graph.add_edge(START, "genealogy")
            graph.add_edge("genealogy", "contradiction")
            graph.add_conditional_edges("contradiction", self._next,
                                        {"revise": "revise", "end": END})
            graph.add_edge("revise", "contradiction")
        else:
            graph.add_edge(START, "genealogy")
            graph.add_edge("genealogy", END)
        init: State = {"edges": [], "decisions": {}, "round": 0, "to_revise": {},
                       "dismissed": [], "conflicts": [], "revisions": [], "errors": []}
        return graph.compile().invoke(init, {"recursion_limit": 4 * self.max_rounds + 10})


def label_pairing(c: Corpus) -> list[Edge]:
    """Naive baseline: what pairing new units with old ones by label would give."""
    edges = []
    for g in c.groups.values():
        if not g.unresolved:
            edges += rule_edges(g)
            continue
        q = case(g)
        claimed = set()
        for s in q.slots:
            path = s.paths[0]
            old = path if path in [u.path for u in q.old] else None
            claimed.add(old)
            edges.append(Edge(g.instrument, old, path, "substitute" if old else "insert",
                              "label", g.id))
        edges += [Edge(g.instrument, u.path, None, "omit", "label", g.id)
                  for u in q.old if u.path not in claimed]
    return edges


Loader = Callable[[], tuple[list[tuple[str, str]], PathState]]
