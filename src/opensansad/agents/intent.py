"""Intent Agent: finds the stated reason for one amendment in the passages of one tier.

It may only quote: the answer names a passage and copies a sentence from it; the claim
paraphrases that quote and nothing else. Whether the quote is really there, who said it and
whether it states a reason at all are checked afterwards (Verification Agent).
"""

from dataclasses import dataclass

from opensansad.agents.llm import LLM, Role
from opensansad.intent.evidence import Passage, Unit

SYSTEM = """You find the officially stated REASON for one amendment to an Indian statute, in
the evidence passages given (and only there).
A reason says WHY (purpose, objective, problem addressed: "in order to", "with a view to",
"to ensure", "it has been decided", "difficulties were noticed ..."). A sentence that only
says WHAT the amendment does is not a reason.
If a passage states a reason for THIS amendment, copy it exactly: one to three contiguous
sentences, word for word, at most 80 words. If no passage does, answer found=false.
Reply in JSON only:
{"found": true|false, "passage": "<passage id>", "quote": "<verbatim text>",
 "claim": "<one sentence giving the stated reason, adding nothing the quote does not say>"}"""


@dataclass
class Proposal:
    found: bool
    passage: Passage | None
    quote: str
    claim: str


def render(unit: Unit, passages: list[Passage], feedback: list[str]) -> str:
    out = [f"Amendment: section {unit.act_section[1:]} of Act {unit.instrument}, amending "
           f"{', '.join(unit.targets)} of the principal Act"
           f" ({'; '.join(h for h in unit.headings if h)})",
           f"Instruction (start): {unit.instruction[:500]}", "", "Evidence passages:"]
    for p in passages:
        out.append(f"[{p.id}] ({p.kind}{', speaker ' + p.speaker if p.speaker else ''})")
        out.append(p.text.strip())
    if feedback:
        out += ["", "A previous answer was rejected:"] + [f"- {f}" for f in feedback]
    return "\n".join(out)


def propose(llm: LLM, unit: Unit, passages: list[Passage], *, role: Role = "bulk",
            feedback: list[str] = ()) -> Proposal:
    a = llm.json(role, SYSTEM, render(unit, passages, list(feedback)))
    by_id = {p.id: p for p in passages}
    if not a.get("found"):
        return Proposal(False, None, "", "")
    p = by_id.get(str(a.get("passage")))
    if p is None and len(passages) == 1:
        p = passages[0]
    return Proposal(True, p, str(a.get("quote", "")), str(a.get("claim", ""))[:400])
