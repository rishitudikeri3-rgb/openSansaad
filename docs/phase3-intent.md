# Phase 3: Intent and Verification (submission version)

Run: `python -m opensansad.workflows.intent` (results in `data/intent/arbitration-1996.json`;
model calls are served from the committed `data/llm_cache/`, so the run repeats offline).

## What it does

One **unit** per section of each amending Act (42: 25 of Act 3 of 2016, 14 of Act 33 of 2019,
3 of Act 3 of 2021). For each unit, three tiers of evidence are searched in order, and the
search stops at the first claim that passes verification:

| Tier | Evidence | Attributed to |
|---|---|---|
| `sor` | the Bill as introduced: Statement of Objects and Reasons, plus the Notes on Clauses naming the section | Government (signed by the minister) |
| `debate` | Lok Sabha passage debate, ranked chunks (section numbers + headings) | the speaker label before the quote: minister → government, otherwise member |
| `report` | Law Commission Reports 246 and Supplementary 246 (2015 Act only) | the Commission |

- **Intent Agent** (bulk model, gpt-oss-20b). It may only *quote*: a passage id, verbatim text of
  at most 80 words, and a one-sentence claim. If the bulk model cannot produce valid JSON, the
  reasoning model answers instead.
- **Verification Agent**. A claim is blocked unless all of the following hold:
  1. the quote is found verbatim in the document (whitespace and quote marks normalized, at least
     20 characters), which also gives the character span;
  2. the document is `primary`;
  3. the speaker is not the Chair;
  4. the reasoning model (gpt-oss-120b) judges that the quote states a reason, that it concerns this
     amendment, and that the claim adds nothing.

  On a non-verbatim quote the agent retries once within the same tier; a reviewer rejection moves
  the search to the next tier. If no tier yields a passing claim, the unit is recorded as
  **"intent not determinable from available record"**, with every tier searched.
- **Attribution** is deterministic: it comes from the document and its speaker lines, never from
  the model (Rule 5).

## Results (2026-10-29)

| | |
|---|---|
| Units | 42 |
| Sourced | **26 (62%)**: SOR 17, debate 6, Law Commission 3 |
| Attribution of sourced reasons | government 17, member 6, committee 3 |
| Not determinable | 16 |
| Incomplete (model error) | 0 |
| Draft claims blocked by verification | 26 |

### Refusal calibration (hand check of the 16 refusals)

| Verdict | Units | Why |
|---|---|---|
| Justified | 7: 2015 ss. 9, 11, 12, 13, 21, 22, 24 (ss. 14, 23, 24, 25, 47, 48, 57) | The SOR and Notes on Clauses say only *what* changes ("seeks to amend … to provide that …"). No stated reason was found in the debate or reports. |
| Probably justified | 6: 2019 ss. 4, 7, 8, 11, 12, 16 (ss. 17, 34, 37, 45, 50, Fourth Schedule) | Consequential changes the 2019 SOR does not explain. The debate was not read in full by hand. |
| **Miss** | 3: 2015 s.19 (s.36, no automatic stay), 2015 s.25 (Schedules 4–7), 2019 s.15 (s.26 of the 2015 Act) | Law Commission Report 246 explains the first two. The 2019 SOR item (vi) states the third ("to clarify that section 26 … is applicable only to …"). |

## Failure modes found

- **API errors counted as refusals** (fixed): Groq returns HTTP 400 `json_validate_failed` when
  gpt-oss-20b emits malformed JSON. The first full run recorded 5 such units as "not
  determinable". The transport now retries this error twice, the workflow falls back to the
  reasoning model, and a tier the model could not answer marks the unit `incomplete`, never refused.
- **Speaker labels** (fixed): labels wrap across lines ("THE\nMINISTER OF LAW …") and carry stray
  non-Latin characters. Matching the signatory by surname alone tagged "SHRI VARAPRASAD RAO" as
  the government (surname "PRASAD"); the full name is now matched on its letters.
- **Wrong scope in the search terms**: 2019 s.15 amends s.26 of the 2015 *Act*, but its search
  used the 1996 Act's s.26. This is the same scope problem the Genealogy Agent's `scope` check handles.
- **Report tier ranking** is lexical (section numbers and headings). Long reports discuss
  s.36 under "automatic stay", which the ranking misses. Semantic retrieval (ChromaDB, in the
  full version) is the fix.
- **Members restating the Bill**: a member summarizing the Government's Bill is reported as that
  member's statement (role `member`), which is correct attribution but a weaker source than the SOR.
- A new section's heading is missing when 4c did not capture its margin note (s.29A), so it does
  not appear in the prompt.

## Not done (full version)

News tier, Rajya Sabha debates, Hindi OCR for 1996 debates, Srikrishna HLC report (manual
import), semantic retrieval, human pass over the calibration table.
