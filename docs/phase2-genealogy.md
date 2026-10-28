# Phase 2: genealogy agents and evaluation

Run: `python -m opensansad.eval.genealogy` (results in `data/eval/genealogy.json`; model calls
are served from the committed `data/llm_cache/`, so the run repeats offline with no API key).

## What is evaluated

The **ground truth** (`data/ground_truth/arbitration-1996.toml`) has one entry per operation of
the three amending Acts: 105 operations, 104 lineage edges (`old → new`). One instruction (2019
s.15) amends the 2015 Amendment Act rather than the 1996 Act, so it should produce no edge. 94
entries are the 4c rules' output, re-read against each instruction. The other 11, in 6
instruction groups, are the ones the rules left unresolved; they were decided by hand from the
texts. Every entry is model-checked; a human pass is still pending.

The 6 hand groups are where judgment is needed:

| Group | Why the rules stop |
|---|---|
| 2015 s.18 (s.34 Explanation → Explanations 1, 2) | one unit substituted by two |
| 2015 s.20 (s.37(1) clauses (a),(b) → (a),(b),(c)) | old (a) is new (b), old (b) is new (c): **labels lie** |
| 2015 s.22, s.24 (ss.48, 57 Explanations) | as s.34 |
| 2019 s.15 | amends another instrument (the 2015 Act's s.26), not the 1996 Act |
| 2021 s.2 (s.36(3) proviso + Explanation) | where the quoted Explanation attaches |

## Systems compared

| Mode | What it is |
|---|---|
| `rules` | 4c rule edges only (unresolved operations dropped) |
| `label` | naive baseline: unresolved groups paired by label, unclaimed old units omitted |
| `single` | **single-pass baseline**: one Genealogy call per group (reasoning model), no validation retry, no Contradiction loop |
| `agentic` | Genealogy Agent (validated, retried) + Contradiction loop, reasoning model throughout |
| `single-bulk` / `agentic-bulk` | the same, with the first pass on the cheap bulk model; revisions always use the reasoning model |

Models: Groq free tier, `openai/gpt-oss-120b` (reasoning) and `openai/gpt-oss-20b` (bulk).

## Results (2026-10-28)

| Mode | All edges P / R | Hand edges P / R | Hand groups right | Out-of-scope right | Revisions (wrong→right) |
|---|---|---|---|---|---|
| rules | 1.000 / 0.904 | 1.000 / 0.091 | 1/6 | 1/1 | — |
| label | 0.952 / 0.962 | 0.583 / 0.636 | 3/6 | 0/1 | — |
| single | 1.000 / 1.000 | 1.000 / 1.000 | 6/6 | 1/1 | — |
| agentic | 1.000 / 1.000 | 1.000 / 1.000 | 6/6 | 1/1 | 0 needed |
| single-bulk | 0.981 / 0.981 | 0.818 / 0.818 | 5/6 | 1/1 | — |
| **agentic-bulk** | **1.000 / 1.000** | **1.000 / 1.000** | **6/6** | 1/1 | **1 revision, 1 wrong→right, 0 right→wrong** |

**The Contradiction loop at work (agentic-bulk).** On s.37(1) the 20B model paired by label:
old (b) → new (b) and old (a) → new (c). The `mispaired` check found that new (b) has
similarity 1.00 to old (a) but only 0.43 to old (b), the clause it was mapped from. The reviewer
(reasoning model) named group 2015 s.20 for revision. The Genealogy Agent, given those
findings, re-mapped old (a) → (b) and old (b) → (c). The second round found no conflicts.
Separately, a `kind` flag on the renumbering s.9 → s.9(1) was judged a false alarm in an earlier
run; the check now skips renumberings.

## Caveats (read before citing these numbers)

- **One act, no held-out data.** The first live run got 5/6 hand groups. In both `single` and
  `agentic`, 2019 s.15 was read as omitting the 1996 Act's own s.26. Two changes followed: the
  rules' "unresolved because" reasons are now in the prompt, and a `scope` check now flags
  edges from an instruction the rules found to amend another instrument. Both were designed on
  this data, so the scores above are optimistic. A second act is needed for an honest
  generalization number.
- With the strong model, the single-pass baseline already scores 6/6, so on this act the loop
  only adds value when the first pass is the weaker, cheaper model. That is the cost argument
  for the design (bulk first, escalate on conflict), not an accuracy one.
- The 94 rule-checked truth entries came from the rules and were then re-read, so `rules`
  precision is close to circular. The checks did catch errors in the truth: two word
  insertions had been recorded as new units, and both were corrected.
- Contradiction checks are deterministic and few (mispaired, old_twice, kind, dangling, scope).
  A mapping that is wrong yet consistent passes them. Future work: India Code footnotes (when
  reachable) and Bill annexures as later evidence.
