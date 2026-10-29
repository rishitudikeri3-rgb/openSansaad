import json
from pathlib import Path

from opensansad.agents import intent as ia
from opensansad.agents import verification as va
from opensansad.agents.llm import LLM
from opensansad.intent import evidence as ev
from opensansad.workflows.intent import NOT_DETERMINABLE, Evidence, run_unit, summary

BILL = """THE ARBITRATION (AMENDMENT) BILL, 2020
A BILL further to amend the Arbitration Act.
STATEMENT OF OBJECTS AND REASONS
The Act provides for arbitration. Difficulties were noticed in the appointment of
arbitrators, and with a view to speeding up appointments it has been decided to
amend section 11 of the Act.
2. The Bill seeks to achieve the above objectives.
NEW DELHI;
A. B. MINISTERNAME.
NOTES ON CLAUSES
Clause 2 of the Bill seeks to amend section 11 of the Act so that appointments are
made within thirty days.
Clause 3 of the Bill seeks to amend section 29A of the Act.
FINANCIAL MEMORANDUM
Nothing.
"""

DEBATE = """Proceedings.
HON. SPEAKER: Item no. 12, the hon. Minister.
THE
MINISTER OF LAW
AND JUSTICE (SHRI A. B. MINISTERNAME): Sir, I beg to move. We amend section 11 of the
Act in order to ensure that the arbitrators are appointed without delay.
SHRI C. D. MEMBER (SOMEWHERE): Madam, section 11 is amended to cut delays in appointments,
says the Government, but I doubt it.
SHRI E. F. OTHER: Why?
"""

DEBATE_DOC = ev.Doc(2, "Lok Sabha debate, 1 Jan 2020", "debate", DEBATE)


def unit() -> ev.Unit:
    return ev.Unit("1/2020|s2", "1/2020", "s2", ["s11"], ["Appointment of arbitrators"],
                   "In section 11 of the principal Act, in sub-section (4), ...")


def test_locate_is_verbatim_modulo_spacing_and_quotes() -> None:
    text = "it has been decided to\namend section 11 of the “Act”."
    a, b = ev.locate('it has been decided to amend section 11 of the "Act" ', text)
    assert text[a:b] == "it has been decided to\namend section 11 of the “Act"
    assert ev.locate("it has been decided to amend section 12", text) is None
    assert ev.locate("too short", text) is None  # under 20 characters: never a citation


def test_sor_passages_and_signatory() -> None:
    passages, sig = ev.sor_passages(ev.Doc(1, "Bill, 2020 (as introduced)", "bill", BILL))
    assert sig == "A. B. MINISTERNAME"
    assert [p.kind for p in passages] == ["sor", "notes", "notes"]
    assert "Difficulties were noticed" in passages[0].text
    assert ev.notes_for(unit(), passages[1:]) == [passages[1]]  # section 11, not 29A


def test_speaker_labels_span_lines_and_roles_come_from_the_label() -> None:
    i = DEBATE.index("in order to ensure")
    assert ev.speaker_at(DEBATE, i) == (
        "THE MINISTER OF LAW AND JUSTICE (SHRI A. B. MINISTERNAME)")
    p = ev.Passage("D2.1", DEBATE_DOC, "debate", "debate", 0, len(DEBATE))
    assert ev.attribution(p, i, "A. B. MINISTERNAME")["role"] == "government"
    j = DEBATE.index("cut delays")
    member = ev.attribution(p, j, "A.B. MINISTERNAME")
    assert member == {"by": "SHRI C. D. MEMBER (SOMEWHERE)", "role": "member",
                      "where": DEBATE_DOC.title}
    assert ev.attribution(p, DEBATE.index("Item no."), None)["role"] == "chair"


def make_llm(tmp_path: Path, intent, review) -> tuple[LLM, list]:
    sent = []

    def send(model: str, messages: list[dict]) -> str:
        system, user = messages[0]["content"], messages[1]["content"]
        sent.append((model, system, user))
        return json.dumps(intent(user) if system == ia.SYSTEM else review(user))
    return LLM({"reasoning": "big", "bulk": "small"}, tmp_path / "llm", send), sent


def evidence() -> Evidence:
    sor, sig = ev.sor_passages(ev.Doc(1, "Bill, 2020 (as introduced)", "bill", BILL))
    return Evidence(sor, sig, ev.chunks(DEBATE_DOC, "debate", "debate"), [],
                    {1: "primary", 2: "primary"})


OK = {"states_reason": True, "about_this_amendment": True, "claim_faithful": True}


def test_sor_claim_verified_and_attributed_to_government(tmp_path: Path) -> None:
    def intent(user):
        return {"found": True, "passage": "S1", "quote": "with a view to speeding up "
                "appointments it has been decided to amend section 11 of the Act.",
                "claim": "To speed up appointments of arbitrators."}
    llm, sent = make_llm(tmp_path, intent, lambda u: OK)
    r = run_unit(llm, unit(), evidence())
    assert (r.status, r.tier, r.document_id) == ("sourced", "sor", 1)
    assert r.attribution["role"] == "government"
    assert r.attribution["signed_by"] == "A. B. MINISTERNAME"
    assert BILL[r.span[0]:r.span[1]].startswith("with a view to")
    assert [m for m, *_ in sent] == ["small", "big"]  # bulk proposes, reasoning reviews


def test_reviewer_rejection_escalates_to_the_debate(tmp_path: Path) -> None:
    def intent(user):
        if "(sor" in user:
            return {"found": True, "passage": "S1", "quote": "The Act provides for "
                    "arbitration.", "claim": "Because the Act provides for arbitration."}
        return {"found": True, "passage": "D2.1", "quote": "in order to ensure that the "
                "arbitrators are appointed without delay", "claim": "To avoid delay."}

    def review(user):
        return OK if "debate" in user else {**OK, "states_reason": False, "note": "what only"}
    llm, _ = make_llm(tmp_path, intent, review)
    r = run_unit(llm, unit(), evidence())
    assert [a["outcome"] for a in r.attempts] == ["blocked", "verified"]
    assert r.tier == "debate" and r.attribution["role"] == "government"
    assert "MINISTERNAME" in r.attribution["by"]


def test_non_verbatim_quote_is_retried_then_blocked(tmp_path: Path) -> None:
    def intent(user):
        return {"found": True, "passage": "S1" if "(sor" in user else "D2.1",
                "quote": "the Government wanted faster arbitration everywhere", "claim": "x"}
    llm, sent = make_llm(tmp_path, intent, lambda u: OK)
    r = run_unit(llm, unit(), evidence())
    assert r.status == "not_determinable" and r.claim == NOT_DETERMINABLE
    assert r.quote is None and r.attribution is None
    assert all(a["reasons"] == ["quote is not verbatim in the cited document"]
               for a in r.attempts if a["outcome"] == "blocked")
    assert not any(system == va.SYSTEM for _, system, _ in sent)  # never reaches the reviewer
    assert "A previous answer was rejected" in sent[1][2]
    assert r.attempts[-1] == {"tier": "report", "outcome": "no passages"}


def test_secondary_and_chair_sources_are_blocked(tmp_path: Path) -> None:
    def intent(user):
        return {"found": True, "passage": "S1", "quote": "with a view to speeding up "
                "appointments it has been decided", "claim": "To speed up appointments."}
    llm, _ = make_llm(tmp_path, intent, lambda u: OK)
    e = evidence()
    e.authority[1] = "secondary"
    prop = ia.propose(llm, unit(), e.sor)
    chk = va.verify(llm, unit(), prop, authority="secondary", signatory=e.signatory)
    assert not chk.passed and chk.reasons == ["document 1 is a secondary source"]
    chair = ev.Passage("D2.1", DEBATE_DOC, "debate", "debate", 0, len(DEBATE))
    prop = ia.Proposal(True, chair, "Item no. 12, the hon. Minister.", "")
    chk = va.verify(llm, unit(), prop, authority="primary", signatory=None)
    assert not chk.passed and "Chair" in chk.reasons[0]


def test_summary_counts_tiers_roles_and_blocks(tmp_path: Path) -> None:
    llm, _ = make_llm(tmp_path, lambda u: {"found": False}, lambda u: OK)
    r = run_unit(llm, unit(), evidence())
    assert r.status == "not_determinable"
    s = summary([r])
    assert s["sourcing_rate"] == 0.0 and s["not_determinable"] == 1


def test_model_failure_is_incomplete_not_a_refusal(tmp_path: Path) -> None:
    from opensansad.agents.llm import LLMError

    def send(model: str, messages: list[dict]) -> str:
        if messages[0]["content"] == ia.SYSTEM and model == "small":
            raise LLMError("small: HTTP 400: json_validate_failed")
        if messages[0]["content"] == ia.SYSTEM:
            return json.dumps({"found": False})
        return json.dumps(OK)
    llm = LLM({"reasoning": "big", "bulk": "small"}, tmp_path / "llm", send)
    r = run_unit(llm, unit(), evidence())
    assert r.status == "not_determinable"  # the reasoning model answered for the bulk one

    def broken(model: str, messages: list[dict]) -> str:
        raise LLMError(f"{model}: HTTP 400: json_validate_failed")
    r = run_unit(LLM({"reasoning": "big", "bulk": "small"}, None, broken), unit(), evidence())
    assert r.status == "incomplete" and r.claim == NOT_DETERMINABLE and r.quote is None
    assert summary([r])["incomplete"] == 1 and summary([r])["not_determinable"] == 0
