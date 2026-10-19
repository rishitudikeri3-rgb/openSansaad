# OpenSansad — Project Context for Claude Code

Read this file fully at the start of every session. It is the source of truth for scope, architecture, conventions, and current status. When a decision is made, update the **Decisions Log** at the bottom.

---

## 1. What this project is

OpenSansad is a multi-agent system that makes Indian legislation legible to the public. It has four user-facing surfaces:

1. **Upcoming business** — items listed for upcoming sittings of Lok Sabha / Rajya Sabha.
2. **Session summaries** — what happened in completed sessions (passed, lapsed, withdrawn, referred to committee).
3. **Bill metadata** — searchable record of bills from 2006 onward.
4. **Act genealogy** — click any act and see its full clause-level amendment history: which provision became which, what each amendment did, who introduced it, and the officially stated and contested reasons for it.

Plus a **chatbot** that answers follow-up questions by routing across the above.

**Core contribution:** clause-level amendment genealogy for one fully traced, hand-verified act. Everything else is breadth around that core.

Built for the Agentic AI course (7th semester, AI & Data Science, BMS College of Engineering). Full in-depth scope — do not simplify the architecture.

---

## 2. Why this is an *agentic* problem (keep this argument intact)

A forward-only retrieve-and-summarize pipeline cannot solve genealogy because:

- **Mapping is non-deterministic.** Amending acts insert, substitute, omit, renumber, split, and merge provisions in natural language. Correspondence requires judgment.
- **Later evidence invalidates earlier conclusions.** A mapping inferred from an early amendment can be contradicted by a later one. The system must **revise its own committed output** — this is the Contradiction Agent's loop.
- **Evidence sufficiency varies.** Some amendments are explained by the Statement of Objects and Reasons; others need debates or committee reports, or are undeterminable. The agent decides how deep to investigate and when to **refuse**.

Design principle: **deterministic code for mechanical work, agents only where judgment is required.** Scraping, caching, extraction, and formulaic amendment parsing are NOT agents.

---

## 3. Agent architecture (LangGraph)

| # | Agent | Role | Network access |
|---|---|---|---|
| 1 | Corpus Agent | Orchestrates ingestion jobs; decides what is missing | Via cache only |
| 2 | Structural Parser | Clause trees + candidate amendment operations (mostly deterministic) | None |
| 3 | **Genealogy Agent** | Resolves `unresolved` operations into clause mappings with confidence; may emit multiple candidates | None |
| 4 | **Contradiction Agent** | Validates lineage consistency; on conflict, identifies the likely-wrong mapping and returns control to Genealogy for revision | None |
| 5 | Intent Agent | Determines amendment rationale via escalating search: SOR → debate → committee report → news. Records evidence tier | Via cache only |
| 6 | **Verification Agent** | Blocks any claim without a citable primary source; marks it "intent not determinable from available record" | None |
| 7 | Explanation Agent | Plain-language rendering with citations on every claim | None |
| 8 | Session Monitor | Tracks Lists of Business, links items to acts in the corpus | Via cache only |
| — | Query Agent (chatbot) | Classifies questions and routes to genealogy traversal / intent lookup / metadata query. Refuses unsupported claims and evaluative/opinion questions | None |

---

## 4. Non-negotiable rules

1. **All network access goes through `src/opensansad/ingest/cache.py`.** No other module imports `httpx`/`requests` directly. Cache keyed by URL hash; never re-fetch unless `force=True`. Rate limit ~1 request per 1–2 s per domain, retries with exponential backoff, honest User-Agent, respect robots.txt.
2. **Never overwrite raw downloads.** Raw bytes in `data/raw/`, extracted text stored separately.
3. **Provenance on everything.** Every clause, operation, mapping, and claim references a `documents.id` and, where possible, a text span.
4. **No unsourced claims reach output.** If a claim has no citation, it is blocked, not softened.
5. **Attribution, not "objectivity."** Statements of purpose are reported as *who said it, where*: government (SOR / minister), opposition (debate), committee. Never present an advocacy document's reasoning as fact. Never characterize a party's motives. Party context = which government was in office and which minister introduced the bill.
6. **Stable clause path IDs**, e.g. `s40A.ss3.cl(b).sc(ii).p1`, `s12.expl1`, `sch2`. All stages use these.
7. **Tests run offline** from fixtures in `tests/fixtures/`. No test may hit the network.
8. **Small, verifiable steps.** One component per session. Run tests before declaring done. Do not build ahead of the current phase.
9. Model names, API keys, and DB URLs come from config / env — never hardcoded.

---

## 5. Data sources

| Source | URL | Used for |
|---|---|---|
| India Code | indiacode.nic.in | Principal acts, list of amending acts, consolidated text, footnotes ("Ins. by…", "Subs. by…", "Omitted by…") |
| e-Gazette of India | egazette.gov.in | Authoritative notified text of amending acts |
| Parliament Digital Library | eparlib.sansad.in | Lok Sabha debates, Bulletins I & II, committee reports. Born-digital debate text from 15th Lok Sabha (~2009) onward; older material is scanned → OCR |
| Digital Sansad | sansad.in | Session calendars, Lists of Business, Rajya Sabha material |
| PRS Legislative Research | prsindia.org | Bill tracker metadata, 2006 onward |

India Code footnotes give partial provenance for the *final* text only. They do not reconstruct intermediate versions, renumbering chains, omit-then-reinsert history, or intent. Use them as a noisy validation signal, not ground truth.

---

## 6. Tech stack

- **Python 3.11+**, `uv` for env and deps
- **Agents:** LangGraph, LangChain, `langchain-openai` against any OpenAI-compatible endpoint — **free models only** (Ollama local, or free tiers of Groq / Gemini / OpenRouter). Stronger model for genealogy/intent reasoning, cheaper/faster model for bulk extraction; endpoint, key and both model names set via env (`LLM_BASE_URL`, `LLM_API_KEY`, `MODEL_REASONING`, `MODEL_BULK`)
- **Ingestion/extraction:** httpx, BeautifulSoup + lxml, PyMuPDF, pdf2image + pytesseract
- **Storage:** PostgreSQL (SQLAlchemy 2.x + Alembic), Neo4j (clause graph + genealogy edges), ChromaDB (semantic retrieval over debates/reports)
- **Jobs:** Celery + Redis (resumable ingestion)
- **API/UI:** FastAPI backend, React + D3.js frontend (Phase 5)
- **Infra:** Docker / docker-compose
- **Tests:** pytest

---

## 7. Repository layout

```
opensansad/
├── CLAUDE.md
├── README.md
├── Makefile
├── docker-compose.yml          # postgres, redis, neo4j
├── .env.example
├── .gitignore
├── pyproject.toml
├── alembic.ini
├── data/
│   ├── raw/{source}/           # untouched downloads (gitignored)
│   ├── text/                   # extracted text (gitignored)
│   └── ground_truth/           # hand-built genealogy (committed)
├── src/opensansad/
│   ├── config.py               # pydantic-settings
│   ├── ingest/
│   │   ├── cache.py            # ONLY network entry point
│   │   └── sources/            # base.py, indiacode.py, egazette.py, eparlib.py, sansad.py, prs.py
│   ├── extract/                # html.py, pdf.py, ocr.py, normalize.py
│   ├── parse/                  # clause_tree.py, amending_ops.py
│   ├── db/                     # base.py, models.py, session.py, migrations/
│   ├── graph/                  # neo4j client + genealogy graph ops
│   ├── agents/                 # one module per agent (Phase 2+)
│   ├── workflows/              # LangGraph graphs (Phase 2+)
│   ├── eval/                   # metrics vs ground truth (Phase 2+)
│   ├── tasks/                  # celery_app.py, ingest_tasks.py
│   └── api/                    # FastAPI (Phase 5)
├── frontend/                   # React + D3 (Phase 5)
├── tests/
│   └── fixtures/
└── notebooks/
```

---

## 8. Core data model (Phase 1)

```
acts(id, short_title, act_number, year, india_code_url, created_at)

documents(id, act_id, doc_type, source, url, sha256, fetched_at, format,
          is_scanned, ocr_confidence, text_path,
          amending_act_number, amending_year)
  doc_type ∈ {principal, amending, gazette, debate, committee_report,
              bulletin, list_of_business, bill}

fetch_log(id, url, status_code, error, attempted_at)

clauses(id, act_id, version_label, path, parent_path, node_type, number,
        heading, text, footnote_refs, source_doc_id)
  node_type ∈ {chapter, section, subsection, clause, subclause,
               proviso, explanation, schedule}

amendment_ops(id, amending_doc_id, target_path, op_type, old_text, new_text,
              source_span, status, confidence)
  op_type ∈ {insert, substitute, omit, renumber, split, merge, other}
  status  ∈ {resolved, unresolved}
```

Neo4j: `(:Clause {path, version_label})` with `CHILD_OF` edges. Phase 2 adds `DESCENDS_FROM {op_type, confidence, source_doc_id}`.

---

## 9. Phase plan

**Phase 1 — Foundation** ← CURRENT
1. Skeleton + docker stack + smoke tests
2. Data recon: shortlist 3 acts, assess source quality, pick one
3. Ingestion: cache, source adapters, extraction, OCR detection, Celery tasks
4. Clause parser for the principal act; rule-based amendment-op extractor (formulaic cases only, everything else `unresolved`)

Exit criteria: full stack up; act chosen and recorded; principal + all amending acts cached/extracted/in DB; scanned docs flagged; debates & committee reports for the act fetched; sansad.in + PRS access verified with samples cached; clause tree in Postgres + Neo4j; resolved/unresolved op ratio reported; footnote coverage assessed; all tests pass offline.

**Phase 2 — Agentic core:** hand-built ground truth for every amendment, Genealogy Agent, Contradiction loop, evaluation harness (precision/recall, correction count, single-pass baseline).

**Phase 3 — Intent & trust:** Intent Agent (escalating search, OCR for pre-2009 debates), Verification Agent (refusal).

**Phase 4 — Breadth:** bill metadata 2006→present, session summaries, Session Monitor.

**Phase 5 — Product:** FastAPI, React + D3 genealogy timeline, Query Agent chatbot, Dockerized deploy, full eval run + failure-mode write-up.

---

## 10. Evaluation (what the project is graded on)

- Genealogy precision / recall vs hand-built ground truth
- Number of initial mappings revised by the Contradiction loop, and how many revisions were correct
- Agentic system vs single-pass baseline on the same act
- Intent sourcing rate; refusal calibration (were refused items genuinely undeterminable?)
- Documented failure modes

---

## 11. Current status

- Phase 1, step 1 (skeleton) — **done** (2026-10-19): uv project on Python 3.12, docker stack (postgres 16, redis 7, neo4j 5) healthy, config, DB/Alembic plumbing (no tables yet), Neo4j client, Celery stub. `make test` (offline, sockets blocked) and `make test-integration` both pass.
- Phase 1, step 2 (data recon) — next

## 12. Decisions Log

- Project name: **OpenSansad**
- Bill metadata coverage: 2006 onward
- Full stack retained (Postgres, Neo4j, Celery/Redis, React + D3) — not simplified
- Solo build
- Chosen act: **TBD (Phase 1, step 2 recon)**
- Python pinned to **3.12** (`requires-python = ">=3.12,<3.13"`); system 3.14 too new for ChromaDB/LangChain/OCR deps
- Only infra (postgres, redis, neo4j) runs in docker; Python code runs locally via `uv`
- Later-phase deps are optional extras (`extract`, `agents`, `retrieval`, `api`), installed when their phase starts
- Postgres tables (§8 schema) are created in step 3 alongside ingestion, not in step 1
- **Free LLMs only — no paid APIs.** Provider-agnostic via OpenAI-compatible endpoint; specific provider/models chosen at Phase 2. Design for free-tier rate limits (caching of LLM calls, retries/backoff) and weaker models (tighter prompts, structured output, deterministic code doing as much as possible)
- Offline test guard: `tests/conftest.py` blocks socket connects unless a test is marked `integration`
