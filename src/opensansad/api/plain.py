"""Easy-view wording: each verified reason rewritten once by the reasoning model in casual,
everyday words, checked, and written to `data/intent/arbitration-1996.plain.json` (committed,
with the LLM cache, so the demo never calls a model to show it).

    python -m opensansad.api.plain

The model gets the verified claim and its verbatim quote and returns only a verb phrase
("make arbitration cheaper and quicker"): who said it stays the record's attribution, added by
the page. A rewrite that brings in numbers the record does not have, names a speaker, quotes,
or runs long is re-asked once with the problems listed, then dropped (the page shows the claim).
"""

import json
import re
import sys

from opensansad.agents.llm import LLM, LLMError
from opensansad.config import PROJECT_ROOT

INTENT = PROJECT_ROOT / "data" / "intent" / "arbitration-1996.json"
OUT = INTENT.with_suffix(".plain.json")

SYSTEM = """You rewrite the stated reason for a change to India's arbitration law so that anyone
can understand it: casual, friendly, everyday words, like explaining to a friend. No legal
jargon (say "private judge" or "arbitrator", "decision" not "award", "court case" not
"proceedings").
Use only what the reason and quote say: add no facts, numbers, dates, examples or opinions,
and do not say who said it.
Reply with JSON only: {"plain": "<a short verb phrase, at most 20 words, that completes the
sentence 'They did it to ...', without the word 'to' at the start and without a full stop>"}"""

SPEAKERS = re.compile(r"\b(government|minister|mp|member|commission|parliament said)\b", re.I)


def check(text: str, claim: str, quote: str) -> list[str]:
    problems = []
    words = text.split()
    if not 2 <= len(words) <= 24:
        problems.append(f"{len(words)} words; use 2 to 20")
    new_numbers = set(re.findall(r"\d+", text)) - set(re.findall(r"\d+", claim + " " + quote))
    if new_numbers:
        problems.append(f"numbers not in the reason: {sorted(new_numbers)}")
    if SPEAKERS.search(text):
        problems.append("names who said it (leave that out)")
    if re.search(r"[\"“”]", text):
        problems.append("contains quotation marks")
    if re.match(r"(?i)to\b", text):
        problems.append("starts with 'to'")
    return problems


def rewrite(llm: LLM, claim: str, quote: str) -> str | None:
    user = f"Reason: {claim}\nTheir exact words: {' '.join(quote.split())}"
    for attempt in range(2):
        try:
            out = llm.json("reasoning", SYSTEM, user)
        except LLMError:
            return None
        text = str(out.get("plain") or "").strip().rstrip(".")
        problems = check(text, claim, quote)
        if not problems:
            return text
        user += f"\n\nYour answer {text!r} was rejected: {'; '.join(problems)}. Try again."
    return None


def main() -> int:
    llm = LLM.from_settings()
    results = [r for r in json.loads(INTENT.read_text())["results"] if r["status"] == "sourced"]
    out, dropped = {}, []
    for r in results:
        text = rewrite(llm, r["claim"], r["quote"])
        if text:
            out[r["unit"]] = text
        else:
            dropped.append(r["unit"])
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    print(f"{len(out)} rewritten, {len(dropped)} dropped {dropped}; "
          f"{llm.calls} model calls, {llm.hits} cached -> {OUT.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
