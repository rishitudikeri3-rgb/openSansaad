"""Intent workflow: for each section of each amending Act, search the tiers in order
(SOR → debate → report) until a verified, attributed reason is found; otherwise record
"intent not determinable from available record" with every tier searched.

    python -m opensansad.workflows.intent [--act-id 1] [--only 3/2021] [--out PATH]

Results: `data/intent/arbitration-1996.json` (committed). Model calls are cached
(`data/llm_cache/`), so a re-run repeats offline.
"""

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from opensansad.agents.intent import propose
from opensansad.agents.llm import LLM, LLMError
from opensansad.agents.verification import verify
from opensansad.config import PROJECT_ROOT
from opensansad.intent import evidence as ev

NOT_DETERMINABLE = "intent not determinable from available record"
OUT = PROJECT_ROOT / "data" / "intent" / "arbitration-1996.json"


@dataclass
class Evidence:
    """Documents for one amending Act, loaded once."""
    sor: list[ev.Passage]
    signatory: str | None
    debates: list[ev.Passage]
    reports: list[ev.Passage]
    authority: dict[int, str]


@dataclass
class Result:
    unit: str
    instrument: str
    act_section: str
    targets: list[str]
    status: str  # sourced | not_determinable | incomplete (a tier could not be searched)
    claim: str = NOT_DETERMINABLE
    tier: str | None = None
    quote: str | None = None
    document_id: int | None = None
    span: list[int] | None = None
    attribution: dict | None = None
    attempts: list[dict] = field(default_factory=list)


def units(corpus, headings: dict[str, str] | None = None) -> list[ev.Unit]:
    """One unit per amending-Act section that changes the principal Act's text;
    `headings` = 1996 headings of top-level provisions (path -> heading)."""
    from opensansad.workflows.genealogy import Corpus

    assert isinstance(corpus, Corpus)
    by: dict[str, ev.Unit] = {}
    for g in corpus.groups.values():
        sec = g.provision.split(".")[0]
        uid = f"{g.instrument}|{sec}"
        u = by.setdefault(uid, ev.Unit(uid, g.instrument, sec, [], [], g.instruction))
        for op in g.ops:
            if not op.target_path:
                continue
            top = op.target_path.split(".")[0]
            if top not in u.targets:
                u.targets.append(top)
                if (h0 := (headings or {}).get(top)) and h0 not in u.headings:
                    u.headings.append(h0)
            h = op.meta.get("heading") or (op.new_text if op.unit == "part" else None)
            if h and h not in u.headings:
                u.headings.append(h)
    for u in by.values():
        parts = [t for t in u.targets if t.startswith("pt")]
        if parts:  # a Part inserted with its sections: ask about the Part
            u.targets = parts
    return list(by.values())


def run_unit(llm: LLM, unit: ev.Unit, e: Evidence, *, retries: int = 1) -> Result:
    res = Result(unit.id, unit.instrument, unit.act_section, unit.targets, "not_determinable")
    for tier in ev.TIERS:
        if tier == "sor":
            notes = [p for p in e.sor if p.kind == "notes"]
            passages = [p for p in e.sor if p.kind == "sor"] + ev.notes_for(unit, notes)
        elif tier == "debate":
            passages = ev.rank(e.debates, unit)
        else:
            passages = ev.rank(e.reports, unit)
        for p in passages:
            if p.kind == "debate":
                p.speaker = ev.speaker_at(p.doc.text, p.start + 1)
        if not passages:
            res.attempts.append({"tier": tier, "outcome": "no passages"})
            continue
        feedback: list[str] = []
        for _ in range(1 + retries):
            try:
                prop = _propose(llm, unit, passages, feedback)
            except LLMError as err:
                res.attempts.append({"tier": tier, "outcome": "error", "error": str(err)})
                break
            if not prop.found:
                res.attempts.append({"tier": tier, "outcome": "none found",
                                     "passages": [p.id for p in passages]})
                break
            authority = e.authority.get(prop.passage.doc.id, "primary") if prop.passage else ""
            try:
                chk = verify(llm, unit, prop, authority=authority, signatory=e.signatory)
            except LLMError as err:
                res.attempts.append({"tier": tier, "outcome": "error", "error": str(err)})
                break
            attempt = {"tier": tier, "outcome": "verified" if chk.passed else "blocked",
                       "passage": prop.passage.id if prop.passage else None,
                       "quote": prop.quote[:300], "claim": prop.claim,
                       "reasons": chk.reasons}
            res.attempts.append(attempt)
            if chk.passed:
                assert chk.span and prop.passage
                doc = prop.passage.doc
                res.status, res.claim, res.tier = "sourced", prop.claim, tier
                res.quote = doc.text[chk.span[0]:chk.span[1]]
                res.document_id, res.span = doc.id, list(chk.span)
                res.attribution = chk.attribution
                return res
            feedback = chk.reasons
            if "quote is not verbatim in the cited document" not in chk.reasons:
                break  # a reviewer rejection: try the next tier, not the same passages
    if any(a["outcome"] == "error" for a in res.attempts):
        # a tier the model could not answer was not searched: not a refusal on the record
        res.status = "incomplete"
    return res


def _propose(llm: LLM, unit: ev.Unit, passages: list[ev.Passage], feedback: list[str]):
    """Bulk model first; if it cannot produce a valid answer, the reasoning model."""
    try:
        return propose(llm, unit, passages, feedback=feedback)
    except LLMError:
        return propose(llm, unit, passages, role="reasoning", feedback=feedback)


def load_evidence(session, instrument: str, data_dir: Path) -> Evidence:
    from sqlalchemy import select

    from opensansad.db.models import Document

    spec = ev.SOURCES[instrument]

    def docs(prefix: str, bills: bool = False) -> list:
        q = select(Document).where(Document.title.startswith(prefix))
        rows = session.scalars(q).all()
        if bills:  # one copy of the Bill: sansad.in first
            rows = sorted(rows, key=lambda d: d.source != "sansad")[:1]
        return rows

    def load(d) -> ev.Doc:
        return ev.Doc(d.id, d.title, d.doc_type, (data_dir / d.text_path).read_text())

    authority: dict[int, str] = {}
    bill = docs(spec["bill"], bills=True)
    sor, sig = ([], None)
    if bill:
        authority[bill[0].id] = bill[0].authority
        sor, sig = ev.sor_passages(load(bill[0]))
    debates, reports = [], []
    for prefix in spec["debates"]:
        for d in docs(prefix):
            authority[d.id] = d.authority
            debates += ev.chunks(load(d), "debate", "debate")
    for prefix in spec["reports"]:
        for d in session.scalars(select(Document).where(Document.title.contains(prefix))):
            if d.text_path:
                authority[d.id] = d.authority
                reports += ev.chunks(load(d), "report", "report")
    return Evidence(sor, sig, debates, reports, authority)


def summary(results: list[Result]) -> dict:
    by_tier: dict[str, int] = {}
    for r in results:
        if r.status == "sourced":
            by_tier[r.tier] = by_tier.get(r.tier, 0) + 1
    blocked = sum(a["outcome"] == "blocked" for r in results for a in r.attempts)
    sourced = sum(by_tier.values())
    return {"units": len(results), "sourced": sourced,
            "sourcing_rate": round(sourced / len(results), 3) if results else 0.0,
            "by_tier": by_tier,
            "not_determinable": sum(r.status == "not_determinable" for r in results),
            "incomplete": sum(r.status == "incomplete" for r in results),
            "claims_blocked_by_verification": blocked,
            "by_role": _count(r.attribution["role"] for r in results if r.attribution)}


def _count(xs) -> dict:
    out: dict[str, int] = {}
    for x in xs:
        out[x] = out.get(x, 0) + 1
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m opensansad.workflows.intent")
    ap.add_argument("--act-id", type=int, default=1)
    ap.add_argument("--version", default="1996-enacted")
    ap.add_argument("--only", help="one instrument, e.g. 3/2021")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)

    from sqlalchemy import select

    from opensansad.config import get_settings
    from opensansad.db.models import Clause
    from opensansad.db.session import get_sessionmaker
    from opensansad.eval.genealogy import load_corpus

    corpus = load_corpus(args.act_id, args.version)
    llm = LLM.from_settings()
    session = get_sessionmaker()()
    results: list[Result] = []
    try:
        evidence = {i: load_evidence(session, i, get_settings().data_dir) for i in corpus.order}
        headings = {c.path: c.heading for c in session.scalars(select(Clause).where(
            Clause.act_id == args.act_id, Clause.version_label == args.version,
            Clause.heading.is_not(None)))}
    finally:
        session.close()
    for u in units(corpus, headings):
        if args.only and u.instrument != args.only:
            continue
        r = run_unit(llm, u, evidence[u.instrument])
        results.append(r)
        who = r.attribution["by"] if r.attribution else ""
        print(f"{u.id:18} {r.status:16} {r.tier or '':7} {who[:30]:30} {r.claim[:70]}",
              flush=True)
    s = summary(results)
    print(json.dumps(s))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"summary": s, "llm": {"requests": llm.calls,
                                                          "cache_hits": llm.hits},
                                    "results": [asdict(r) for r in results]},
                                   indent=1, ensure_ascii=False))
    print(f"written {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
