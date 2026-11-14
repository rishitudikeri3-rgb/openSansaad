"""Demo API: the Act's provisions, their lineage across the amending Acts, and the sourced
(or refused) reason for each amendment.

    uv run uvicorn opensansad.api.app:app --reload        # http://127.0.0.1:8000
    python -m opensansad.api.app export site/             # static copy (GitHub Pages)

Reads only committed / derived data: the 1996 clause tree (Postgres), the system's lineage
(`data/genealogy/`, written by the genealogy eval in `agentic-bulk` mode) and the intent
results (`data/intent/`). The one exception is `POST /api/chat` (the Query Agent), which asks
the bulk model, through `agents/llm.py`, only *which provisions* a question is about, or that
it asks for an opinion; the page then shows those provisions' recorded changes and sourced
reasons. The model never writes the answer. The static export has no chat endpoint (the key
cannot ship in a public page): there the page matches words in the browser instead.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from opensansad.config import PROJECT_ROOT

LINEAGE = PROJECT_ROOT / "data" / "genealogy" / "arbitration-1996.json"
INTENT = PROJECT_ROOT / "data" / "intent" / "arbitration-1996.json"
EVAL = PROJECT_ROOT / "data" / "eval" / "genealogy.json"
STATIC = Path(__file__).parent / "static"

# when each amending Act took effect: Acts 3 of 2016 and 3 of 2021 replace Ordinances and
# apply from the Ordinances' dates; Act 33 of 2019 was commenced only in part (per-provision
# commencement is not yet modelled, so the note says so rather than giving one date)
INSTRUMENTS = {
    "3/2016": {"title": "Arbitration and Conciliation (Amendment) Act, 2015 (No. 3 of 2016)",
               "effective": "from 23 Oct 2015 (Ordinance 9 of 2015)"},
    "33/2019": {"title": "Arbitration and Conciliation (Amendment) Act, 2019 (No. 33 of 2019)",
                "effective": "in part: ss. 1, 4–9, 11–13, 15 from 30 Aug 2019 (S.O. 3154(E)); "
                             "other sections by later notification or not commenced"},
    "3/2021": {"title": "Arbitration and Conciliation (Amendment) Act, 2021 (No. 3 of 2021)",
               "effective": "from 4 Nov 2020 (Ordinance 14 of 2020)"},
}


@dataclass
class Provision:
    path: str
    node_type: str
    number: str | None
    heading: str | None
    text: str
    parent: str | None
    ord: int


class Store:
    def __init__(self, provisions: list[Provision], lineage: dict, intent: dict,
                 evaluation: dict | None = None, headings: dict[str, str] | None = None):
        self.provisions = {p.path: p for p in provisions}
        self.headings = headings or {}  # headings of provisions inserted after 1996
        self.evaluation = evaluation or {}
        self.lineage = lineage
        self.intent = {r["unit"]: r for r in intent.get("results", [])}
        self.intent_summary = intent.get("summary", {})

    @classmethod
    def load(cls, act_id: int = 1, version: str = "1996-enacted") -> "Store":
        from sqlalchemy import select

        from opensansad.db.models import AmendmentOp, Clause
        from opensansad.db.session import get_sessionmaker

        with get_sessionmaker()() as s:
            rows = s.scalars(select(Clause).where(Clause.act_id == act_id,
                                                  Clause.version_label == version)).all()
            provs = [Provision(c.path, c.node_type, c.number, c.heading, c.text,
                               c.parent_path, c.ord) for c in rows]
            headings = {o.target_path: o.meta["heading"] for o in s.scalars(
                select(AmendmentOp).where(AmendmentOp.act_id == act_id,
                                          AmendmentOp.op_type == "insert"))
                if o.target_path and "." not in o.target_path and (o.meta or {}).get("heading")}

        def read(p: Path) -> dict:
            return json.loads(p.read_text()) if p.exists() else {}
        return cls(provs, read(LINEAGE) or {"edges": [], "instructions": {}}, read(INTENT),
                   read(EVAL), headings)

    def top(self, path: str) -> str:
        return path.split(".")[0]

    def changed_tops(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.lineage["edges"]:
            for p in {e["old"], e["new"]} - {None}:
                out[self.top(p)] = out.get(self.top(p), 0) + 1
        return out

    def provision(self, top: str) -> dict:
        edges = [e for e in self.lineage["edges"]
                 if top in {self.top(p) for p in (e["old"], e["new"]) if p}]
        base = sorted((p for p in self.provisions.values()
                       if p.path == top or p.path.startswith(top + ".")), key=lambda p: p.ord)
        if not edges and not base:
            raise KeyError(top)
        events = []
        for inst in self.lineage.get("instruments", []):
            es = [e for e in edges if e["instrument"] == inst]
            if not es:
                continue
            groups = sorted({e["group"] for e in es})
            units = sorted({f"{inst}|{g.split('|')[1].split('.')[0]}" for g in groups})
            events.append({"instrument": inst, **INSTRUMENTS.get(inst, {}), "edges": es,
                           "instructions": {g: self.lineage["instructions"].get(g)
                                            for g in groups},
                           "intent": [self._intent(u) for u in units]})
        head = self.provisions.get(top)
        return {"path": top, "heading": head.heading if head else self.headings.get(top),
                "in_1996": head is not None,
                "nodes_1996": [{"path": p.path, "node_type": p.node_type, "number": p.number,
                                "heading": p.heading, "text": p.text} for p in base],
                "events": events}

    def unit_tops(self, unit: str) -> list[str]:
        """Top-level provisions one amending-Act section touches: its reason may concern any of
        them (2019 s.9 inserted both s.42A and s.42B; its stated reason is about s.42B)."""
        inst, sec = unit.split("|")
        return sorted({self.top(p) for e in self.lineage["edges"]
                       if e["instrument"] == inst and e["group"].split("|")[1].split(".")[0] == sec
                       for p in (e["old"], e["new"]) if p})

    def _intent(self, unit: str) -> dict:
        r = self.intent.get(unit)
        if r is None:
            return {"unit": unit, "status": "not_run", "touches": self.unit_tops(unit)}
        keep = ("unit", "status", "claim", "tier", "quote", "document_id", "span",
                "attribution")
        return {k: r.get(k) for k in keep} | {
            "tiers_searched": [a["tier"] for a in r.get("attempts", [])],
            "touches": self.unit_tops(unit)}


CHAT_SYSTEM = """You route questions about India's Arbitration and Conciliation Act, 1996 (and its
2015, 2019 and 2021 amendments) to the parts of the Act that answer them. You never answer the
question yourself.
Reply with JSON only: {"kind": "law" | "opinion" | "other", "paths": [up to 3 ids]}.
- "law": the question is about what the Act says, how it changed, or why. "paths" = the ids
  from the list that answer it, most relevant first (ids exactly as listed).
- "opinion": it asks you to judge, evaluate or predict (is a change good or bad, fair, who is
  right, what should happen). "paths" = the ids of the specific topic it names, if any;
  a question about a whole amendment or year names none: [].
- "other": not about this Act. "paths" = []."""


# names for the chat catalogue where the data has no heading (provisions inserted by an
# instruction whose heading the op extractor did not read); the page shows the same names
CHAT_NAMES = {
    "s11A": "Power of Central Government to amend Fourth Schedule (arbitrators' fees)",
    "s29A": "Time limit for arbitral award (deadline for the decision)",
    "s31A": "Regime for costs (who pays the costs)",
    "s42A": "Confidentiality of information (keeping the case private)",
    "s87": "Effect of arbitral and related court proceedings commenced prior to 23 Oct 2015",
    "sch4": "Fourth Schedule: model fees of arbitrators",
    "sch5": "Fifth Schedule: grounds giving rise to justifiable doubts about an arbitrator's "
            "independence or impartiality",
    "sch6": "Sixth Schedule: form of disclosure by an arbitrator",
    "sch7": "Seventh Schedule: relationships that make a person ineligible to be an arbitrator",
    "sch8": "Eighth Schedule: qualifications and experience of arbitrators",
}


class ChatIn(BaseModel):
    question: str = Field(min_length=1, max_length=300)


def route_question(llm, catalogue: dict[str, str], question: str) -> dict:
    """Query Agent: the model picks provisions from the catalogue; anything else is dropped."""
    from opensansad.agents.llm import LLMError

    user = "Parts of the Act (id | heading):\n" + "\n".join(
        f"{p} | {h}" for p, h in catalogue.items()) + f"\n\nQuestion: {question}"
    for role in ("bulk", "reasoning"):  # the 20B model sometimes fails Groq's JSON check
        try:
            out = llm.json(role, CHAT_SYSTEM, user)
            break
        except LLMError:
            continue
    else:
        return {"kind": "error", "paths": []}
    kind = out.get("kind") if out.get("kind") in ("law", "opinion", "other") else "other"
    paths = out.get("paths") if isinstance(out.get("paths"), list) else []
    seen = [p for p in dict.fromkeys(paths) if isinstance(p, str) and p in catalogue]
    return {"kind": kind, "paths": seen[:3]}


def chat_llm():
    """The bulk model with its own cache dir (`data/chat_cache/`, gitignored): readers'
    questions are not committed with the evaluation's cache."""
    from opensansad.agents.llm import LLM
    from opensansad.config import get_settings

    llm = LLM.from_settings()
    llm.cache_dir = get_settings().data_dir / "chat_cache"
    return llm


def create_app(store: Store | None = None, llm=None) -> FastAPI:
    app = FastAPI(title="OpenSansad demo")
    state: dict = {"store": store, "llm": llm}

    def get() -> Store:
        if state["store"] is None:
            state["store"] = Store.load()
        return state["store"]

    @app.get("/api/act")
    def act() -> dict:
        s = get()
        changed = s.changed_tops()
        tops = sorted((p for p in s.provisions.values()  # top-level paths by design (4b)
                       if p.node_type in ("section", "schedule")), key=lambda p: p.ord)
        rows = [{"path": p.path, "number": p.number, "heading": p.heading,
                 "node_type": p.node_type, "changes": changed.get(p.path, 0)} for p in tops]
        inserted = sorted(set(changed) - set(s.provisions))
        return {"title": "The Arbitration and Conciliation Act, 1996 (26 of 1996)",
                "instruments": [{"id": i, **INSTRUMENTS.get(i, {})}
                                for i in s.lineage.get("instruments", [])],
                "provisions": rows,
                "inserted": [{"path": p, "heading": s.headings.get(p), "changes": changed[p]}
                             for p in inserted],
                "intent_summary": s.intent_summary,
                "genealogy_eval": {m: {"all": v["all"], "hand": v["hand"],
                                       "hand_groups_right": v["hand_groups_right"],
                                       "revisions": v.get("revision_score")}
                                   for m, v in s.evaluation.items()}}

    @app.get("/api/provision/{path}")
    def provision(path: str) -> dict:
        try:
            return get().provision(path)
        except KeyError:
            raise HTTPException(404, f"no provision {path}") from None

    @app.post("/api/chat")
    def chat(body: ChatIn) -> dict:
        s = get()
        if state["llm"] is None:
            try:
                state["llm"] = chat_llm()
            except Exception as e:  # no key in .env, or the agents extra not installed
                raise HTTPException(503, f"chat unavailable: {e}") from None
        changed = s.changed_tops()
        catalogue = {p.path: p.heading or "" for p in sorted(
            s.provisions.values(), key=lambda p: p.ord) if p.node_type in ("section", "schedule")}
        catalogue |= {p: s.headings.get(p, "") for p in sorted(set(changed) - set(s.provisions))}
        catalogue = {p: CHAT_NAMES.get(p) or h for p, h in catalogue.items()}
        return route_question(state["llm"], catalogue, body.question) | {"routed_by": "model"}

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    return app


def export(out: Path, store: Store | None = None) -> int:
    """Write the page and every API response as static files: the same page then runs
    from any static host, with no database (`api/act`, `api/provision/<path>`)."""
    from fastapi.testclient import TestClient

    client = TestClient(create_app(store or Store.load()))
    act = client.get("/api/act").json()
    (out / "api" / "provision").mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text((STATIC / "index.html").read_text())
    (out / ".nojekyll").write_text("")
    (out / "api" / "act").write_text(json.dumps(act, ensure_ascii=False))
    paths = [p["path"] for p in act["provisions"]] + [p["path"] for p in act["inserted"]]
    for path in paths:
        r = client.get(f"/api/provision/{path}")
        r.raise_for_status()
        (out / "api" / "provision" / path).write_text(r.text)
    return len(paths)


app = create_app()

if __name__ == "__main__":
    import sys

    if len(sys.argv) != 3 or sys.argv[1] != "export":
        sys.exit("usage: python -m opensansad.api.app export DIR")
    print(f"exported {export(Path(sys.argv[2]))} provisions to {sys.argv[2]}")
