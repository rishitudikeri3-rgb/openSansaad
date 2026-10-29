# OpenSansad

**Clause-level amendment history of Indian legislation, with sourced and attributed reasons for
every change.**

The first Act traced is the **Arbitration and Conciliation Act, 1996**, followed through its
amendments in 2015, 2019 and 2021. Click any section to see:
- which old clause became which new one;
- what each amending Act did;
- **why** it was amended, as a verbatim quote from the Statement of Objects and Reasons, a Lok
  Sabha debate or a Law Commission report, attributed to whoever said it;
- or "intent not determinable from available record" when no source states a reason.

**Live demo:** https://rishitudikeri3-rgb.github.io/openSansaad/

## Why agents

A retrieve-and-summarize pipeline cannot do this:

- **Lineage is a judgment call.** Amending Acts substitute two clauses with three, or one
  Explanation with two. Pairing them by label is wrong: in s.37(1), old (a) became new (b).
- **Later evidence overturns earlier conclusions.** The system must revise its own committed output.
- **Evidence varies.** Some amendments are explained in the SOR, some only in debate, and some
  nowhere. The system must know when to refuse.

Deterministic code does the mechanical work (fetching, extraction, clause parsing, formulaic
amendments); agents do only the rest, and every agent answer is checked before it is kept.

| Stage | What it does |
|---|---|
| Corpus | 57 primary documents (e-Gazette, sansad.in, Parliament Digital Library, PRS, Law Commission), cached, hashed, rate-limited |
| Parser | 1996 text → 553 addressable clauses (`s34.ss2.cl(b)`); amending Acts → 105 operations, 94 resolved by rules |
| **Genealogy Agent** | maps the 11 operations rules cannot decide, choosing only among parser-built candidates |
| **Contradiction Agent** | detects inconsistent lineage, sends wrong mappings back for revision (LangGraph loop) |
| **Intent Agent** | escalating search: SOR → debate → Law Commission; may only quote |
| **Verification Agent** | blocks any claim whose quote is not verbatim in a primary source, or that does not state a reason for this amendment |

## Results

Genealogy against a hand-built answer key (104 lineage edges, 11 of them hard):

| System | All edges P / R | Hard cases right |
|---|---|---|
| Rules only | 1.000 / 0.904 | 1/6 |
| Label pairing (naive) | 0.952 / 0.962 | 3/6 |
| Single pass, 20B model | 0.981 / 0.981 | 5/6 |
| **Agentic (20B first pass + Contradiction loop)** | **1.000 / 1.000** | **6/6**: 1 revision, wrong → right |

Intent: **26 of 42** amendments sourced (SOR 17, debate 6, Law Commission 3); 26 draft claims
blocked by verification; 16 refused. A hand check found 13 of the refusals justified and 3 misses.

Details and caveats: [`docs/phase2-genealogy.md`](docs/phase2-genealogy.md),
[`docs/phase3-intent.md`](docs/phase3-intent.md). Caveat: one Act, so there is no held-out data.

## Run it

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```bash
cp .env.example .env      # USER_AGENT contact; LLM_* only needed to re-query models
make install              # uv sync (Python 3.12)
make up && make migrate   # postgres, redis, neo4j
make demo                 # http://127.0.0.1:8000
make test                 # offline; network blocked
```

The evaluation and intent runs replay offline from the committed model cache:
`python -m opensansad.eval.genealogy`, `python -m opensansad.workflows.intent`.
`make site` exports the demo as static files (published on the `gh-pages` branch).

Models: open-weight gpt-oss-120b / 20b on Groq's free tier. See `CLAUDE.md` for architecture,
rules and the decisions log.

*Agentic AI course project, BMS College of Engineering. Work in progress: per-provision
commencement, court judgments (s.87 struck down, 2019), the chatbot and more Acts come next.*
