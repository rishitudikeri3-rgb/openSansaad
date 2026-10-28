import json
from pathlib import Path

import pytest
from test_amending_ops import AMENDING, state

from opensansad.agents import contradiction as ca
from opensansad.agents import genealogy as ga
from opensansad.agents.llm import LLM, LLMError
from opensansad.eval.genealogy import load_truth, score, score_revisions
from opensansad.workflows.genealogy import Workflow, case, corpus, label_pairing, rule_edges

GROUP = "1/2020|s3.cl(b)"  # for clauses (a) and (b), new (a), (b), (c): old (a) is new (b)
WRONG = [{"slot": "N1", "path": "s3.ss2.cl(a)", "old": "s3.ss2.cl(a)", "op_type": "substitute"},
         {"slot": "N2", "path": "s3.ss2.cl(b)", "old": "s3.ss2.cl(b)", "op_type": "substitute"},
         {"slot": "N3", "path": "s3.ss2.cl(c)", "old": None, "op_type": "insert"}]
RIGHT = [{"slot": "N1", "path": "s3.ss2.cl(a)", "old": None, "op_type": "insert"},
         {"slot": "N2", "path": "s3.ss2.cl(b)", "old": "s3.ss2.cl(a)", "op_type": "renumber"},
         {"slot": "N3", "path": "s3.ss2.cl(c)", "old": "s3.ss2.cl(b)", "op_type": "renumber"}]


def fake(answers):
    """Transport answering by prompt: contradiction reviews revise every group named."""
    sent = []

    def send(model: str, messages: list[dict]) -> str:
        system, user = messages[0]["content"], messages[1]["content"]
        sent.append((model, user))
        if system == ca.SYSTEM:
            ids = [line.split(" ", 1)[0] for line in user.splitlines() if line.startswith("C")]
            return json.dumps({"verdicts": [{"conflict": i, "false_alarm": False,
                                             "revise": [GROUP], "reason": "mispaired"}
                                            for i in ids]})
        return json.dumps(answers(model, user))
    return send, sent


def by_group(model: str, user: str) -> dict:
    if "provision s3.cl(b)" in user:
        return {"mappings": RIGHT if "found inconsistent" in user or model == "big" else WRONG}
    if "provision s6" in user:
        return {"out_of_scope": True, "mappings": []}
    slots = [line.split()[2] for line in user.splitlines() if line.startswith("- slot ")]
    paths = [line.split("allowed paths ['", 1)[1].split("'", 1)[0]
             for line in user.splitlines() if line.startswith("- slot ")]
    return {"mappings": [{"slot": s, "path": p, "old": None, "op_type": "insert"}
                         for s, p in zip(slots, paths, strict=True)]}


def make_llm(tmp_path: Path, answers=by_group) -> tuple[LLM, list]:
    send, sent = fake(answers)
    return LLM({"reasoning": "big", "bulk": "small"}, tmp_path / "llm", send), sent


@pytest.fixture
def test_corpus():
    return corpus([("1/2020", AMENDING)], state())


def test_case_offers_old_units_with_their_text(test_corpus) -> None:
    q = case(test_corpus.groups[GROUP])
    assert [u.path for u in q.old] == ["s3.ss2.cl(a)", "s3.ss2.cl(b)"]
    assert q.old[0].text == "first thing;"
    assert [s.paths for s in q.slots] == [["s3.ss2.cl(a)"], ["s3.ss2.cl(b)"], ["s3.ss2.cl(c)"]]
    assert "pairing by label" in ga.render(q, [])


def test_validate_refuses_invalid_answers(test_corpus) -> None:
    q = case(test_corpus.groups[GROUP])
    edges, scope, problems = ga.validate(q, {"mappings": RIGHT})
    assert problems == [] and not scope
    assert {e.key[1:] for e in edges} == {("", "s3.ss2.cl(a)"), ("s3.ss2.cl(a)", "s3.ss2.cl(b)"),
                                          ("s3.ss2.cl(b)", "s3.ss2.cl(c)")}
    bad = [dict(RIGHT[0], path="s3.ss2.cl(z)"), dict(RIGHT[1], old="s3.ss2.cl(b)"), RIGHT[2]]
    _, _, problems = ga.validate(q, {"mappings": bad})
    assert any("not among" in p for p in problems)
    assert any("claimed by two slots" in p for p in problems)
    _, _, problems = ga.validate(q, {"mappings": RIGHT[:2]})
    assert any("not answered" in p for p in problems)
    edges, _, _ = ga.validate(q, {"mappings": [dict(m, old=None, op_type="insert")
                                               for m in RIGHT]})
    assert sum(e.op_type == "omit" for e in edges) == 2  # unclaimed old units are omitted


def test_llm_cache_serves_repeats(tmp_path: Path) -> None:
    llm, sent = make_llm(tmp_path, lambda m, u: {"ok": True})
    assert llm.json("bulk", "JSON", "x") == llm.json("bulk", "JSON", "x") == {"ok": True}
    assert (llm.calls, llm.hits, len(sent)) == (1, 1, 1)
    assert sent[0][0] == "small"
    bad, _ = make_llm(tmp_path / "b", lambda m, u: ["not", "an", "object"])
    with pytest.raises(LLMError):
        bad.json("bulk", "JSON", "y")


def test_detect_mispairing_scope_and_kind(test_corpus) -> None:
    c = test_corpus
    wrong, _, _ = ga.validate(case(c.groups[GROUP]), {"mappings": WRONG})
    edges = [e for g in c.groups.values() for e in rule_edges(g)] + wrong
    conflicts = ca.detect(edges, c.texts, c.later_refs, c.order, c.scope_flags)
    assert [x.check for x in conflicts] == ["mispaired"]
    assert "reads like old s3.ss2.cl(a)" in conflicts[0].message
    other = ga.Edge("1/2020", "s4", None, "omit", "agent", "1/2020|s6")
    kind = ga.Edge("1/2020", "s2.ss2.p1", "s2.ss2.expl1", "substitute", "agent", "x")
    checks = {x.check for x in ca.detect(edges + [other, kind], c.texts, c.later_refs, c.order,
                                         c.scope_flags)}
    assert {"scope", "kind"} <= checks


def test_loop_revises_a_mispaired_group(tmp_path: Path, test_corpus) -> None:
    llm, sent = make_llm(tmp_path)
    st = Workflow(test_corpus, llm, first_role="bulk").run()
    assert [r["group"] for r in st["revisions"]] == [GROUP] and st["revisions"][0]["changed"]
    keys = {e.key[1:] for e in st["edges"] if e.group == GROUP}
    assert ("s3.ss2.cl(a)", "s3.ss2.cl(b)") in keys and ("s3.ss2.cl(b)", "s3.ss2.cl(c)") in keys
    assert not [e for e in st["edges"] if e.group == "1/2020|s6"]  # out of scope
    assert st["conflicts"][-1] == []
    assert any(m == "big" and "found inconsistent" in u for m, u in sent)


def test_single_pass_keeps_the_first_answer(tmp_path: Path, test_corpus) -> None:
    llm, _ = make_llm(tmp_path)
    st = Workflow(test_corpus, llm, first_role="bulk", contradiction=False).run()
    keys = {e.key[1:] for e in st["edges"] if e.group == GROUP}
    assert ("s3.ss2.cl(b)", "s3.ss2.cl(b)") in keys and st["revisions"] == []


def test_label_pairing_baseline(test_corpus) -> None:
    keys = {e.key[1:] for e in label_pairing(test_corpus) if e.group == GROUP}
    assert ("s3.ss2.cl(a)", "s3.ss2.cl(a)") in keys and ("", "s3.ss2.cl(c)") in keys


def test_scoring_against_the_committed_truth() -> None:
    truth = load_truth()
    assert len(truth.hand) == 6 and truth.out_of_scope == {"33/2019|s15"}
    s37 = "3/2016|s20"
    good = [ga.Edge(i, o or None, n or None, "x", "agent", g)
            for g, keys in truth.edges.items() for i, o, n in keys]
    assert score(good, truth)["all"]["f1"] == 1.0
    assert score(good, truth)["hand_groups_right"] == "6/6"
    rev = [{"group": s37, "changed": True, "before": [],
            "after": sorted(truth.edges[s37])}]
    assert score_revisions(rev, truth)["wrong_to_right"] == 1
