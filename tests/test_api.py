import json

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
    assert a["inserted"] == [{"path": "s29A", "heading": None, "changes": 1}]
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
    assert "OpenSansad" in c.get("/").text


def test_export_writes_static_copy(tmp_path) -> None:
    from opensansad.api.app import export

    provs = [Provision("s37", "section", "37", "Appealable orders.", "", None, 1)]
    n = export(tmp_path, Store(provs, LINEAGE, INTENT), chat_url="")  # not from .env
    assert n == 2  # s37 and the inserted s29A
    assert (tmp_path / "api" / "provision" / "s29A").exists()
    assert "OpenSansad" in (tmp_path / "index.html").read_text()
    cfg = json.loads((tmp_path / "api" / "chat-config").read_text())
    assert cfg["catalogue"] == {"s37": "Appealable orders.",
                                "s29A": "Time limit for arbitral award (deadline for the decision)"}
    assert "Reply with JSON only" in cfg["system"] and not (tmp_path / "chat.json").exists()
    export(tmp_path, Store(provs, LINEAGE, INTENT), chat_url="https://chat.example.dev")
    assert json.loads((tmp_path / "chat.json").read_text()) == {"url": "https://chat.example.dev"}


class FakeLLM:
    def __init__(self, out: dict) -> None:
        self.out, self.prompts = out, []

    def json(self, role: str, system: str, user: str) -> dict:
        self.prompts.append(user)
        return self.out


def chat_client(out: dict) -> tuple[TestClient, FakeLLM]:
    llm = FakeLLM(out)
    provs = [Provision("s37", "section", "37", "Appealable orders.", "", None, 1)]
    return TestClient(create_app(Store(provs, LINEAGE, INTENT), llm=llm)), llm


def test_chat_routes_to_known_provisions_only() -> None:
    c, llm = chat_client({"kind": "law", "paths": ["s37", "s99", "s37", "s29A"]})
    r = c.post("/api/chat", json={"question": "Can I appeal?"}).json()
    assert r == {"kind": "law", "paths": ["s37", "s29A"], "routed_by": "model"}
    assert "s37 | Appealable orders." in llm.prompts[0] and "s29A |" in llm.prompts[0]


def test_chat_bad_answers_are_dropped_not_repaired() -> None:
    c, _ = chat_client({"kind": "verdict", "paths": "s37"})
    assert c.post("/api/chat", json={"question": "x"}).json()["kind"] == "other"
    c, _ = chat_client({"kind": "opinion", "paths": ["s37"]})
    assert c.post("/api/chat", json={"question": "Was 2015 good?"}).json()["kind"] == "opinion"
    assert c.post("/api/chat", json={"question": ""}).status_code == 422
    assert c.post("/api/chat", json={"question": "x" * 301}).status_code == 422


def test_intent_lists_every_provision_its_amending_section_touches() -> None:
    p = client().get("/api/provision/s37").json()
    assert p["events"][0]["intent"][0]["touches"] == ["s37"]
    lineage = {**LINEAGE, "edges": LINEAGE["edges"] + [
        {"instrument": "3/2016", "old": None, "new": "s29B", "op_type": "insert",
         "source": "rule", "group": "3/2016|s15", "confidence": 1.0, "rationale": None}]}
    provs = [Provision("s37", "section", "37", "Appealable orders.", "", None, 1)]
    c = TestClient(create_app(Store(provs, lineage, INTENT)))
    it = c.get("/api/provision/s29A").json()["events"][0]["intent"][0]
    assert it["touches"] == ["s29A", "s29B"]


def test_chat_writes_a_reply_only_inside_the_facts() -> None:
    facts = ("about: Deadline\nhistory: This rule was added in 2015.\n"
             "reason: The Government said it was to make cases faster.")
    c, _ = chat_client({"reply": "Yep! It came in 2015 and the Government said it's to speed cases up."})
    assert c.post("/api/chat", json={"question": "q", "facts": facts}).json()["reply"].startswith("Yep")
    c, _ = chat_client({"reply": "It came in 2016 to speed things up."})  # wrong year, no speaker
    assert c.post("/api/chat", json={"question": "q", "facts": facts}).json() == {"reply": None}


def test_check_reply_needs_speaker_and_admits_no_reason() -> None:
    from opensansad.api.app import check_reply

    mp = "history: x\nreason: An MP said it was to help (their own view, not the Government's)."
    assert check_reply("An MP reckoned it would help, just their view though.", mp) == []
    assert check_reply("It was to help.", mp) == [
        "does not say who gave the reason", "does not say the MP's reason is their own view"]
    none = "history: This rule was added in 2019.\nreason: none found"
    assert check_reply("Added in 2019, but I couldn't find a reason written down.", none) == []
    assert check_reply("Added in 2019 to make things fair.", none) == ["does not say no reason was found"]


def test_easy_wording_is_served_with_the_reason() -> None:
    provs = [Provision("s37", "section", "37", "Appealable orders.", "", None, 1)]
    c = TestClient(create_app(Store(provs, LINEAGE, INTENT, plain={"3/2016|s20": "limit appeals"})))
    assert c.get("/api/provision/s37").json()["events"][0]["intent"][0]["plain"] == "limit appeals"
