"""Verification Agent: no claim reaches output without a citable primary source (rule 4).

A proposed intent claim is blocked unless:
1. its quote is verbatim in the cited document (deterministic; gives the span),
2. the document is primary (`documents.authority`), and
3. the reviewer model finds that the quote states a reason (not only what is done), that it
   is about this amendment, and that the claim says nothing the quote does not.
Attribution comes from the document and its speaker lines, never from the model. A blocked
claim is not softened: the unit's intent stays undetermined unless a later tier passes.
"""

from dataclasses import dataclass, field

from opensansad.agents.intent import Proposal
from opensansad.agents.llm import LLM, Role
from opensansad.intent.evidence import Unit, attribution, locate

SYSTEM = """You check one claim about why an Indian statute was amended, against the quoted
source text. Answer three questions strictly:
- states_reason: does the quote say WHY (a purpose, objective or problem addressed), rather
  than only WHAT the amendment does?
- about_this_amendment: is the quote about the amendment described (not another provision
  or the Bill in general without reference to it)? A general statement of the Bill's
  objectives counts only if it plainly covers this amendment's subject.
- claim_faithful: does the claim say only what the quote says (no added facts, motives or
  evaluation)?
Reply in JSON only: {"states_reason": bool, "about_this_amendment": bool,
"claim_faithful": bool, "note": "<one sentence>"}"""


@dataclass
class Check:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    span: tuple[int, int] | None = None
    attribution: dict | None = None
    review: dict | None = None


def verify(llm: LLM, unit: Unit, prop: Proposal, *, authority: str, signatory: str | None,
           role: Role = "reasoning") -> Check:
    if not prop.found or prop.passage is None:
        return Check(False, ["no passage named"])
    doc = prop.passage.doc
    span = locate(prop.quote, doc.text)
    if span is None:
        return Check(False, ["quote is not verbatim in the cited document"])
    if authority != "primary":
        return Check(False, [f"document {doc.id} is a {authority} source"], span)
    who = attribution(prop.passage, span[0], signatory)
    if who["role"] == "chair":
        return Check(False, ["quote is the Chair's, not a statement of reasons"], span, who)
    user = (f"Amendment: section {unit.act_section[1:]} of Act {unit.instrument}, amending "
            f"{', '.join(unit.targets)} ({'; '.join(h for h in unit.headings if h)})\n"
            f"Instruction (start): {unit.instruction[:400]}\n\n"
            f"Source: {who['where']} ({who['by']})\nQuote: {doc.text[span[0]:span[1]]}\n\n"
            f"Claim: {prop.claim}")
    r = llm.json(role, SYSTEM, user)
    failed = [k for k in ("states_reason", "about_this_amendment", "claim_faithful")
              if not r.get(k)]
    return Check(not failed, [f"reviewer: not {k}" for k in failed] +
                 ([str(r.get("note"))] if failed and r.get("note") else []), span, who, r)
