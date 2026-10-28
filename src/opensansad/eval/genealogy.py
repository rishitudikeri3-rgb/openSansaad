"""Genealogy evaluation against `data/ground_truth/arbitration-1996.toml`.

An edge is (instrument, old path, new path); precision / recall are over edges, for all
operations and for the `hand` subset (the instructions the rules left unresolved: where the
agents do the work). Scope: instructions the truth marks `other_instrument` must yield no edge.
Each revision by the Contradiction loop is scored by whether its group was right before and
after (a group is right when its edges equal the truth's).

    python -m opensansad.eval.genealogy [--modes rules,label,single,agentic] [--act-id 1]
"""

import argparse
import json
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from opensansad.agents.genealogy import Edge
from opensansad.config import PROJECT_ROOT

TRUTH = PROJECT_ROOT / "data" / "ground_truth" / "arbitration-1996.toml"
Key = tuple[str, str, str]


@dataclass
class Truth:
    edges: dict[str, set[Key]]  # group -> edges
    hand: set[str]  # groups with a hand-decided op
    out_of_scope: set[str]

    @property
    def all(self) -> set[Key]:
        return set().union(*self.edges.values())


def load_truth(path: Path = TRUTH) -> Truth:
    data = tomllib.loads(path.read_text())
    edges: dict[str, set[Key]] = {}
    hand, scope = set(), set()
    for op in data["op"]:
        gid = f"{op['instrument']}|{op['provision']}"
        edges.setdefault(gid, set())
        if op.get("decided") == "hand":
            hand.add(gid)
        if op.get("scope") == "other_instrument":
            scope.add(gid)
            continue
        edges[gid].add((op["instrument"], op.get("old", ""), op.get("new", "")))
    return Truth(edges, hand, scope)


def prf(pred: set[Key], gold: set[Key]) -> dict:
    tp = len(pred & gold)
    p = tp / len(pred) if pred else 1.0
    r = tp / len(gold) if gold else 1.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"tp": tp, "pred": len(pred), "gold": len(gold), "precision": round(p, 3),
            "recall": round(r, 3), "f1": round(f, 3)}


def score(edges: list[Edge], truth: Truth) -> dict:
    pred = {e.key for e in edges}
    hand_pred = {e.key for e in edges if e.group in truth.hand}
    hand_gold = set().union(*(truth.edges[g] for g in truth.hand))
    groups: dict[str, set[Key]] = {}
    for e in edges:
        groups.setdefault(e.group, set()).add(e.key)
    right = sorted(g for g in truth.hand if groups.get(g, set()) == truth.edges[g])
    return {"all": prf(pred, truth.all), "hand": prf(hand_pred, hand_gold),
            "hand_groups_right": f"{len(right)}/{len(truth.hand)}",
            "out_of_scope_right": sorted(g for g in truth.out_of_scope if not groups.get(g)),
            "wrong_groups": sorted(g for g in truth.hand if g not in right)}


def score_revisions(revisions: list[dict], truth: Truth) -> dict:
    out = {"revisions": len(revisions), "changed": 0, "wrong_to_right": 0,
           "right_to_wrong": 0, "wrong_to_wrong": 0, "right_kept": 0}
    for r in revisions:
        gold = truth.edges.get(r["group"], set())
        before = {tuple(k) for k in r["before"]} == gold
        after = {tuple(k) for k in r["after"]} == gold
        out["changed"] += r["changed"]
        key = ("right_kept" if before and after else "wrong_to_right" if after else
               "right_to_wrong" if before else "wrong_to_wrong")
        out[key] += 1
        r["correct_before"], r["correct_after"] = before, after
    return out


def load_corpus(act_id: int, version: str):
    from opensansad.config import get_settings
    from opensansad.db.session import get_sessionmaker
    from opensansad.parse.load_ops import base_state, instruments
    from opensansad.workflows.genealogy import corpus

    session = get_sessionmaker()()
    try:
        base = base_state(session, act_id, version)
        data_dir = get_settings().data_dir
        acts = [(f"{d.amending_act_number}/{d.amending_year}",
                 (data_dir / d.text_path).read_text())
                for d in instruments(session, act_id) if d.doc_type == "amending"]
    finally:
        session.close()
    return corpus(acts, base)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m opensansad.eval.genealogy")
    ap.add_argument("--act-id", type=int, default=1)
    ap.add_argument("--version", default="1996-enacted")
    ap.add_argument("--modes", default="rules,label,single,agentic,single-bulk,agentic-bulk")
    ap.add_argument("--out", type=Path, default=PROJECT_ROOT / "data" / "eval" / "genealogy.json")
    args = ap.parse_args(argv)

    from opensansad.agents.llm import LLM
    from opensansad.workflows.genealogy import Workflow, label_pairing, rule_edges

    truth = load_truth()
    c = load_corpus(args.act_id, args.version)
    llm = None
    results: dict[str, dict] = {}
    for mode in args.modes.split(","):
        if mode == "rules":
            edges, extra = [e for g in c.groups.values() for e in rule_edges(g)], {}
        elif mode == "label":
            edges, extra = label_pairing(c), {}
        else:
            llm = llm or LLM.from_settings()
            calls = llm.calls
            wf = Workflow(c, llm, contradiction=not mode.startswith("single"),
                          first_role="bulk" if mode.endswith("-bulk") else "reasoning")
            st = wf.run()
            edges = st["edges"]
            extra = {"rounds": st["round"], "conflicts_per_round": [len(x) for x in
                                                                    st["conflicts"]],
                     "conflicts": st["conflicts"], "revisions": st["revisions"],
                     "revision_score": score_revisions(st["revisions"], truth),
                     "errors": st["errors"], "llm_requests": llm.calls - calls,
                     "trace": wf.trace}
        results[mode] = {**score(edges, truth), **extra,
                         "edges": sorted([list(e.key) + [e.op_type, e.source] for e in edges
                                          if e.group in truth.hand])}
        r = results[mode]
        print(f"{mode:13} all P {r['all']['precision']:.3f} R {r['all']['recall']:.3f}  "
              f"hand P {r['hand']['precision']:.3f} R {r['hand']['recall']:.3f}  "
              f"groups {r['hand_groups_right']}  scope {len(r['out_of_scope_right'])}/"
              f"{len(truth.out_of_scope)}"
              + (f"  revisions {r['revision_score']}" if "revision_score" in r else ""))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=1, default=str))
    print(f"written {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
