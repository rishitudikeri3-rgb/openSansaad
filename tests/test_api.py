from fastapi.testclient import TestClient

from opensansad.api.app import Provision, Store, create_app

LINEAGE = {
    "instruments": ["3/2016"],
    "edges": [
        {"instrument": "3/2016", "old": None, "new": "s37.ss1.cl(a)", "op_type": "insert",
         "source": "agent", "group": "3/2016|s20", "confidence": 0.9, "rationale": "new"},
        {"instrument": "3/2016", "old": "s37.ss1.cl(a)", "new": "s37.ss1.cl(b)",
         "op_type": "renumber", "source": "agent", "group": "3/2016|s20",
         "confidence": 0.9, "rationale": "same text"},
        {"instrument": "3/2016", "old": None, "new": "s29A", "op_type": "insert",
         "source": "rule", "group": "3/2016|s15", "confidence": 1.0, "rationale": None},
    ],
    "instructions": {"3/2016|s20": "In section 37 ...", "3/2016|s15": "After section 29 ..."},
}
INTENT = {
    "summary": {"units": 2, "sourced": 1, "not_determinable": 1},
    "results": [
        {"unit": "3/2016|s20", "status": "sourced", "claim": "To limit appeals.", "tier": "sor",
         "quote": "with a view to ...", "document_id": 21, "span": [10, 30],
         "attribution": {"by": "Government", "role": "government"},
         "attempts": [{"tier": "sor", "outcome": "verified"}]},
        {"unit": "3/2016|s15", "status": "not_determinable",
         "claim": "intent not determinable from available record",
         "attempts": [{"tier": "sor", "outcome": "none found"},
                      {"tier": "debate", "outcome": "blocked"}]},
    ],
}


def client() -> TestClient:
    provs = [Provision("s37", "section", "37", "Appealable orders.", "", None, 1),
             Provision("s37.ss1", "subsection", "1", None, "An appeal shall lie", "s37", 2),
             Provision("s37.ss1.cl(a)", "clause", "a", None, "granting ...", "s37.ss1", 3),
             Provision("s38", "section", "38", "Deposits.", "", None, 4)]
    return TestClient(create_app(Store(provs, LINEAGE, INTENT)))


def test_act_lists_changed_and_inserted_provisions() -> None:
    a = client().get("/api/act").json()
    assert [(p["path"], p["changes"]) for p in a["provisions"]] == [("s37", 3), ("s38", 0)]
    assert a["inserted"] == [{"path": "s29A", "changes": 1}]
    assert a["intent_summary"]["sourced"] == 1


def test_provision_carries_edges_instructions_and_intent() -> None:
    p = client().get("/api/provision/s37").json()
    assert p["in_1996"] and [n["path"] for n in p["nodes_1996"]] == [
        "s37", "s37.ss1", "s37.ss1.cl(a)"]
    (e,) = p["events"]
    assert len(e["edges"]) == 2 and e["instructions"] == {"3/2016|s20": "In section 37 ..."}
    assert e["intent"][0]["status"] == "sourced"
    assert e["intent"][0]["attribution"]["role"] == "government"


def test_inserted_provision_and_refusal() -> None:
    p = client().get("/api/provision/s29A").json()
    assert not p["in_1996"]
    it = p["events"][0]["intent"][0]
    assert it["status"] == "not_determinable" and it["quote"] is None
    assert it["tiers_searched"] == ["sor", "debate"]


def test_unknown_provision_is_404_and_index_served() -> None:
    c = client()
    assert c.get("/api/provision/s99").status_code == 404
    assert "d3" in c.get("/").text


def test_export_writes_static_copy(tmp_path) -> None:
    from opensansad.api.app import export

    provs = [Provision("s37", "section", "37", "Appealable orders.", "", None, 1)]
    n = export(tmp_path, Store(provs, LINEAGE, INTENT))
    assert n == 2  # s37 and the inserted s29A
    assert (tmp_path / "api" / "provision" / "s29A").exists()
    assert "d3" in (tmp_path / "index.html").read_text()
