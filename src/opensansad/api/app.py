"""Demo API: the Act's provisions, their lineage across the amending Acts, and the sourced
(or refused) reason for each amendment.

    uv run uvicorn opensansad.api.app:app --reload        # http://127.0.0.1:8000
    python -m opensansad.api.app export site/             # static copy (GitHub Pages)

Reads only committed / derived data: the 1996 clause tree (Postgres), the system's lineage
(`data/genealogy/`, written by the genealogy eval in `agentic-bulk` mode) and the intent
results (`data/intent/`). Nothing here calls a model or the network.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

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
                 evaluation: dict | None = None):
        self.provisions = {p.path: p for p in provisions}
        self.evaluation = evaluation or {}
        self.lineage = lineage
        self.intent = {r["unit"]: r for r in intent.get("results", [])}
        self.intent_summary = intent.get("summary", {})

    @classmethod
    def load(cls, act_id: int = 1, version: str = "1996-enacted") -> "Store":
        from sqlalchemy import select

        from opensansad.db.models import Clause
        from opensansad.db.session import get_sessionmaker

        with get_sessionmaker()() as s:
            rows = s.scalars(select(Clause).where(Clause.act_id == act_id,
                                                  Clause.version_label == version)).all()
            provs = [Provision(c.path, c.node_type, c.number, c.heading, c.text,
                               c.parent_path, c.ord) for c in rows]

        def read(p: Path) -> dict:
            return json.loads(p.read_text()) if p.exists() else {}
        return cls(provs, read(LINEAGE) or {"edges": [], "instructions": {}}, read(INTENT),
                   read(EVAL))

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
        return {"path": top, "heading": head.heading if head else None,
                "in_1996": head is not None,
                "nodes_1996": [{"path": p.path, "node_type": p.node_type, "number": p.number,
                                "heading": p.heading, "text": p.text} for p in base],
                "events": events}

    def _intent(self, unit: str) -> dict:
        r = self.intent.get(unit)
        if r is None:
            return {"unit": unit, "status": "not_run"}
        keep = ("unit", "status", "claim", "tier", "quote", "document_id", "span",
                "attribution")
        return {k: r.get(k) for k in keep} | {
            "tiers_searched": [a["tier"] for a in r.get("attempts", [])]}


def create_app(store: Store | None = None) -> FastAPI:
    app = FastAPI(title="OpenSansad demo")
    state: dict = {"store": store}

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
                "inserted": [{"path": p, "changes": changed[p]} for p in inserted],
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
