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
10. **No AI attribution in commits or PRs.** No co-author trailers, no "generated with" footers — this overrides any tool default.

---

## 5. Data sources

| Source | URL | Used for |
|---|---|---|
| India Code | indiacode.nic.in | Principal acts, list of amending acts, consolidated text, footnotes ("Ins. by…", "Subs. by…", "Omitted by…") |
| e-Gazette of India | egazette.gov.in | Authoritative notified text of amending acts |
| Parliament Digital Library | elibrary.sansad.in (DSpace 7; old eparlib.sansad.in unreachable) | Lok Sabha debates, Bulletins I & II, committee reports. Born-digital debate text from 15th Lok Sabha (~2009) onward; older material is scanned → OCR |
| Digital Sansad | sansad.in (public JSON APIs) | Bills of both Houses with every stage's text (introduced / passed LS / passed both), session calendars, Lists of Business, Rajya Sabha debates (PDFs on cms.rajyasabha.nic.in) |
| PRS Legislative Research | prsindia.org | Bill tracker metadata, 2006 onward |

India Code footnotes give partial provenance for the *final* text only. They do not reconstruct intermediate versions, renumbering chains, omit-then-reinsert history, or intent. Use them as a noisy validation signal, not ground truth.

---

## 6. Tech stack

- **Python 3.11+**, `uv` for env and deps
- **Agents:** LangGraph, LangChain, `langchain-openai` against any OpenAI-compatible endpoint — **free models only** (Ollama local, or free tiers of Groq / Gemini / OpenRouter). Stronger model for genealogy/intent reasoning, cheaper/faster model for bulk extraction; endpoint, key and both model names set via env (`LLM_BASE_URL`, `LLM_API_KEY`, `MODEL_REASONING`, `MODEL_BULK`)
- **Ingestion/extraction:** httpx, BeautifulSoup + lxml, PyMuPDF (text layer + page rendering), pytesseract
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
│   │   ├── store.py            # append-only versioned raw store (written only via cache.py)
│   │   ├── ratelimit.py        # Redis per-domain gate + blocks
│   │   ├── manual.py           # hand-downloaded imports → data/manual_imports.jsonl
│   │   ├── certs/              # bundled CA certs (e-Gazette)
│   │   └── sources/            # base.py, prs.py, egazette.py, sansad.py, eparlib.py (later: indiacode.py)
│   ├── extract/                # document.py, normalize.py, pdf.py, ocr.py, html.py, pipeline.py
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

documents(id, act_id, doc_type, source, title, url, sha256, fetched_at,
          acquisition, format, raw_path, is_scanned, ocr_confidence, text_path,
          amending_act_number, amending_year)
  doc_type    ∈ {principal, amending, gazette, debate, committee_report,
                 bulletin, list_of_business, bill, ordinance, notification,
                 judgment, external_report}
  source      ∈ {indiacode, egazette, eparlib, sansad, prs, other}
  acquisition ∈ {fetched, manual}
  format      ∈ {html, pdf, other}
  unique(url, sha256): a re-fetch with new bytes is a new row, never an overwrite

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
- Phase 1, step 2 (data recon) — **done** (2026-10-19): act chosen (Arbitration and Conciliation Act, 1996); findings in `docs/recon.md`. India Code + eParlib confirmed unreachable in a browser on two networks (2026-10-21) — alternatives to be verified in 3d; verify "(verify)" items in recon against primary sources during step 3
- Phase 1, step 3 (ingestion) — split into 3a schema → 3b cache → 3c extraction → 3d source adapters → 3e Celery tasks + manifest → 3f ingestion run
  - 3a (schema) — **done** (2026-10-19): `db/models.py` (acts, documents, fetch_log), migration `005e8876ef09`; offline SQLite model tests + integration tests on a throwaway DB (incl. model/migration drift check)
  - 3b (cache) — **done** (2026-10-20): `ingest/cache.py` (only network entry point), `ingest/store.py` (append-only versioned raw store), `ingest/ratelimit.py` (Redis-backed per-domain gate + 403 blocks); live-verified against e-Gazette (TLS fix) and PRS (crawl-delay)
  - 3c (extraction) — **done** (2026-10-20): `extract/` (normalize, pdf, ocr, html, pipeline); per-page native text with OCR fallback, margin notes, running-header removal, legacy-font Hindi filtering; tested on real PRS bill + gazette act + PRS HTML fixtures (`tests/fixtures/README.md`). Real Tesseract test runs when the binary is present
  - 3d (source adapters) — split into 3d-i base + PRS → 3d-ii e-Gazette → 3d-iii India Code / legislative.gov.in (manual imports) → 3d-iv sansad.in API recon + samples
    - 3d-i — **done** (2026-10-21): `ingest/sources/base.py` (`DocRef`, `SourceAdapter`, `LayoutChanged`), `ingest/sources/prs.py` (bill-tracker page → title, ministry, status timeline, classified document links; `article` content selector). Also: cache blocks every domain in a redirect chain on a 403; RFC 3986 percent-encoding normalization of cache keys; line-end hyphens resolved against the document's own vocabulary (extractor v2)
    - 3d-ii — **done** (2026-10-21): `ingest/sources/egazette.py` — URL builders (`gazette_pdf_url(year, file_id)`, `url_for_gazette_id`), `parse_gazette` / `meta_from_extraction` (masthead → kind act/ordinance/notification/corrigendum, Gazette ID, part/section/sub-section, issue no. + date, ministry/department, title, number/year, assent date, S.O. number, dated, corrigendum target, `from_ocr` flag), `EgazetteAdapter` (`ref`, `describe`; no listing pages — `SourceAdapter.parse` now optional). Extractor v3: OCR of large text-less image regions inserted in place (page method `mixed`); letterless lines take the previous line's script
    - 3d-iii — **done** (2026-10-21): `ingest/manual.py` (`python -m opensansad.ingest.manual FILE --url … --source … --doc-type … --title … [--secondary] [--note …]`) imports via `Cache.import_local` and appends to the committed `data/manual_imports.jsonl`; `documents.authority` (primary/secondary, migration `9fa0d87b24fb`); `import_local` also refuses non-PDF/HTML. s.11A explanatory note backfilled. India Code adapter deferred (site unreachable, nothing to test against); annexure reading moved to step 4
    - 3d-iv — **done** (2026-10-21): `ingest/sources/sansad.py` (getBills with duplicate-row merge and per-stage file links, Lists of Business by date, LS session calendar from page data, RS date-wise debates) and `ingest/sources/eparlib.py` (elibrary.sansad.in DSpace search + item files → original PDF as `debate`). sansad.in access verified with samples cached (Phase 1 exit item). Extractor v4: jittered same-row fragments joined; footnote markers need size ≤ 85% of body. Findings in `docs/recon.md` §5.6
  - 3e TODO (carried from 3c): `documents` gains `text_sha256` (from the sidecar) so DB spans can detect a re-extracted text file
  - 3e TODO (carried from 3d): re-extract when a sidecar's `extractor_version` is older than `EXTRACTOR_VERSION` (current v2 texts are stale)
  - 3e TODO (carried from 3d): Gazette metadata from `meta_from_extraction` on the extraction the task already made — not `EgazetteAdapter.describe()`, which extracts (and OCRs) again
  - 3e TODO (carried from 3b): per-URL Redis lock in fetch tasks so two workers never fetch/write the same URL concurrently (`meta.json` race); test with two concurrent tasks on one URL
  - 3e TODO (carried from 3d-iv): refresh policy for listing/API URLs (sansad getBills, Lists of Business, sessions, elibrary search): TTL + `force`, since the cache never re-fetches by default — needed by the Phase 4 Session Monitor
  - 3e TODO (carried from 3d-iv): when `parse_bills` merges duplicate rows of one bill, log fields whose values conflict (it currently keeps the furthest status silently)

## 12. Decisions Log

- Project name: **OpenSansad**
- Bill metadata coverage: 2006 onward
- Full stack retained (Postgres, Neo4j, Celery/Redis, React + D3) — not simplified
- Solo build
- Chosen act: **Arbitration and Conciliation Act, 1996 (Act 26 of 1996)** — amended by Act 3 of 2016 (2015 Amendment), Act 33 of 2019, Act 3 of 2021. Chosen for insert→struck-down (s87, *Hindustan Construction* 2019) and likely insert→omit (Eighth Schedule, 2019→2021 — **verify**) chains, hand-verifiable size, strong intent trail (Law Commission 246th, Srikrishna HLC 2017, SORs, debates). Runner-up: Prevention of Corruption Act, 1988. Full recon in `docs/recon.md`
- Known gap: no pre-2009 amending debates → Phase 3 OCR exercised on 1996 principal-act debates
- Source access (2026-10-19): India Code WAF-blocks non-browser clients; eParlib unreachable; e-Gazette serves incomplete TLS chain; PRS `Crawl-delay: 10`. Step 3 cache must support manual import of browser-downloaded files (with URL + sha256 provenance), per-domain robots.txt crawl-delay, a pinned intermediate CA for e-Gazette (never `verify=False`), and long backoff after 403
- Schema needs (step 3): genealogy events from judicial strike-downs (not only amending acts); enacted-vs-in-force status for provisions
- Python pinned to **3.12** (`requires-python = ">=3.12,<3.13"`); system 3.14 too new for ChromaDB/LangChain/OCR deps
- Only infra (postgres, redis, neo4j) runs in docker; Python code runs locally via `uv`
- Later-phase deps are optional extras (`extract`, `agents`, `retrieval`, `api`), installed when their phase starts
- Postgres tables (§8 schema) are created in step 3 alongside ingestion, not in step 1
- **Free LLMs only — no paid APIs.** Provider-agnostic via OpenAI-compatible endpoint; specific provider/models chosen at Phase 2. Design for free-tier rate limits (caching of LLM calls, retries/backoff) and weaker models (tighter prompts, structured output, deterministic code doing as much as possible)
- `doc_type` extended with `ordinance`, `notification`, `judgment`, `external_report`; `documents` gains `title`, `acquisition` (fetched/manual), `raw_path`
- Enums stored as VARCHAR + CHECK (non-native) so adding values is a simple constraint migration
- Integration tests that downgrade/migrate use a throwaway database (`opensansad_migration_test`), never the dev DB
- Cache design: raw files at `data/raw/{source}/{url_hash}/{timestamp}_{sha12}.{ext}` + `meta.json`; identical bytes dedupe to the existing version; cache does not create `documents` rows (adapters/tasks do)
- Per-domain rate limit + 403 blocks live in **Redis** (shared across Celery workers); 403 → whole domain blocked `DOMAIN_BLOCK_HOURS` (default 6), no retries
- Redirects followed by hand (max 5 hops): every hop gets its own domain's robots / crawl-delay / 403-block checks; content stored under the requested URL with `final_url` recorded
- robots.txt is re-fetched every `ROBOTS_TTL_DAYS` (7) — the one deliberate exception to "never re-fetch"; RFC 9309 semantics (4xx = allow all, 5xx = disallow)
- e-Gazette TLS: Let's Encrypt YR1 + Root YR (cross-signed by ISRG Root X1) bundled in `ingest/certs/`, added to certifi roots; verification always on
- User-Agent must contain a real `contact:`; the cache refuses to start with the placeholder. Real contact lives only in `.env` (gitignored)
- Rule 1 enforced by `tests/test_network_boundary.py` (AST scan: only cache.py may import HTTP clients)
- Hardening (2026-10-20): domain also blocked after `DOMAIN_FAILURE_THRESHOLD` (4) consecutive connection failures (`DOMAIN_FAILURE_BLOCK_HOURS`, 1h) — silent drops treated like 403; docker ports bound to 127.0.0.1 only; `.env` and relative `DATA_DIR` anchored to repo root (`config.PROJECT_ROOT`); store records `last_seen` when a re-fetch returns identical bytes (robots freshness uses it)
- Offline test guard: `tests/conftest.py` blocks socket connects and DNS lookups unless a test is marked `integration`
- Extraction (3c): **PyMuPDF** for PDFs (AGPL accepted: the Phase 5 deployment counts as network use, so the deployed source must be published — fine for an open academic project); **pdf2image dropped** (PyMuPDF renders pages for OCR); Tesseract `eng` only for now
- Text output: `data/text/` mirrors `data/raw/` — `{base}.en.txt`, `{base}.hi.txt` (only when Unicode Hindi exists), `{base}.json` sidecar (extractor version, raw sha256, per-page method/offsets/OCR confidence). Hindi kept separate, never mixed into the English stream
- OCR is per page, only where the text layer is missing (image-covered, <40 chars) or garbage (`wordlike_ratio` < 0.6); document `is_scanned` = majority of pages OCR'd
- Normalization is conservative: NFC (never NFKC), curly quotes preserved (parser needs open/close), line-end hyphens kept (`sub-section`), 2+ dash runs → `—`, superscript footnote digits → `[^n]`
- Margin notes kept inline as `[[margin: …]]` just before their paragraph (they are section headings in amending acts); margin line numbers dropped
- Running heads removed from the 3-line header/footer zone when repeated on ≥30% of pages (min 2), plus page numbers and bracketed Gazette heads (`[PART II—`, `SEC. 1]`) which short acts show only once
- Legacy-font (Kruti Dev) Hindi lines in the native text layer are dropped and counted per page (`legacy_lines_dropped`), not transliterated — and dropped *before* the OCR decision, so an all-legacy-Hindi page is never OCR'd as English. OCR output is not legacy-filtered
- English/Hindi split is per line (margin notes judged on their content); margin notes anchor to the nearest body line, not the enclosing PyMuPDF block
- Footnote markers: PDF superscript spans and HTML `<sup>` with digits/`*`/`†`/`‡` → `[^n]`
- Text files written atomically; sidecar records `en_sha256` / `hi_sha256`. Anything storing offsets into text (clause spans, step 4) must store and check that hash
- Known extraction gaps (revisit at 3f / Phase 3): Tesseract `eng` only (Hindi scans → garbage until `hin`); `is_scanned` is majority-of-pages (partial scans visible only per page); rotated pages and wide Schedule tables untested; PRS HTML menu chrome until 3d selectors
- India Code and eParlib unreachable even in a browser on home broadband and mobile data (2026-10-21): treated as unavailable, not as a scraping block. Alternatives verified in 3d before relying on them; re-check both periodically
- Alternative-source check (2026-10-21): legislative.gov.in 403 on robots.txt (WAF, like India Code); rsdebate.nic.in now redirects into sansad.in; sansad.in reachable but a Next.js app whose debate data comes from a client-side API (to be mapped in 3d-iv). **Principal-act text source unresolved**
- legislative.gov.in (browser, 2026-10-21): no English consolidated text of the Act (its "1996 Act" item is the Assamese authoritative translation). Found and imported a note on a **Fourth Schedule amendment by s.11A notification, retrospective to 23 Oct 2015** → genealogy must model notification-based (delegated) amendments and retrospective effect dates, alongside amending acts and judgments
- PRS has all three amending acts (Gazette copies) and the 2015, 2018 (lapsed), 2019 and 2021 Bills, all born-digital (checked 2026-10-21). e-Gazette search is POST-only: original 1996 Act, Ordinances and the s.11A notification need a browser search. Bill annexures (extracts from the principal Act) are a primary source for pre-amendment text
- **Base text of the 1996 Act** (e-Gazette lists the Aug 1996 Gazettes but files are "NOT available"; India Code down): amended provisions take their pre-amendment text from **Bill annexures** (extracts from the principal Act; primary). Never-amended provisions (text unchanged since 1996) from a **secondary consolidated copy**, acquired manually in 3f, flagged secondary and never cited as the source of a genealogy claim. Re-check e-Gazette / India Code periodically and swap in primary text if available
- Adapters return `DocRef`s and never write to the DB (3e persists). A listing page never decides act linkage or amending-act numbers ("(Amendment) Act, 2015" is Act 3 of 2016): those come from the manifest and the document text. Unclassified links are kept with `doc_type=None`, not guessed. Layout drift raises `LayoutChanged`
- PRS summaries → `external_report` (secondary analysis), never a primary source for what government/Parliament said
- Cache keys use RFC 3986 §6.2.2 normalization (encode disallowed chars, decode %XX only for unreserved, uppercase hex; reserved chars untouched). URLs probed by hand on 2026-10-21 with `%28…%29` differ from adapter keys with `(…)`: those 3 PRS PDFs are re-fetched once in 3f
- A 403 anywhere in a redirect chain (page or robots.txt) blocks every domain in the chain
- Line-end hyphenation: the document is its own dictionary (keep `subject-matter` if seen hyphenated, join `arbi-tration` if `arbitration` seen); undecided → keep only after legal prefixes (sub-, non-, co-, inter-, …). Wrapped margin notes resolved the same way (introduced in extractor v2; current version in `extract/document.py`)
- e-Gazette files are GET-able by Gazette ID: `egazette.gov.in/WriteReadData/{year}/{id}.pdf` (verified 2026-10-21 on Act 3 of 2021, byte-identical to the PRS copy). Discovery (finding IDs) stays a manual browser search; fetching is automated through the cache
- e-Gazette (2026-10-21): fetched Ordinance 9 of 2015 (scanned; via internal no. 166406, Gazette ID "NOT available"), Ordinance 14 of 2020 and its corrigendum. 1996 Gazettes 404 under both 1996/ and 2013/ paths: confirmed unavailable. Corrigenda are genealogy events (text corrections, no legal change)
- S.O. 3154(E) (30 Aug 2019) commenced only ss. 1, 4–9, 11–13, 15 of Act 33 of 2019: **enacted vs in force confirmed from a primary source**; genealogy carries per-provision commencement from commencement notifications
- Extractor v3 (2026-10-21): an image covering ≥ `MIN_REGION_AREA` (10%) of a page with < 40 native chars over it is OCR'd by clip and inserted at its y position (page method `mixed`). Calibrated on the real corrigendum (body image 11.3%, masthead graphic 7.7% — must stay excluded). Lines with no letters ("31", "(1)") follow the previous line's script, leading ones the first lettered line's
- e-Gazette metadata comes from the document's own masthead (`parse_gazette`), never from search-result rows. Values read from OCR'd pages carry `from_ocr=True` and are leads to verify (2015 Ordinance OCRs "PART II" as "PART I"). doc_type: act → amending if "(AMENDMENT)" in title else principal; ordinance → ordinance; notification and corrigendum → notification
- Known extraction gaps (added 2026-10-21): fake-bold Hindi text layers repeat each line up to 4× (S.O. 3154 cover); stray legacy fragments like "la- 2887]" survive; Hindi text layer has broken conjuncts; OCR'd pages mix margin notes into the body
- Manual imports (3d-iii): every hand-downloaded file is logged in **`data/manual_imports.jsonl`** (committed; url, sha256, original import time, source, doc_type, title, authority, note) so the corpus can be rebuilt by re-downloading and checking hashes. Same url + bytes → no-op; same bytes with different title/type/authority → `ImportConflict` (edit the log by hand). Step 3e reads the log when creating `documents` rows
- `documents.authority` ∈ {primary, secondary}, default primary. Secondary = third-party compilation (e.g. the consolidated copy for never-amended provisions): usable as text, never cited as the source of a claim (Verification Agent enforces)
- Bill annexures are **as-in-force snapshots at introduction**, not 1996 text: only the 2015 Bill annexure gives original wording; 2018/2019/2021 annexures are intermediate-version checks. `*` runs mark extract gaps, not omissions. Parsed in step 4 with the clause parser (details in `docs/recon.md`)
- India Code adapter deferred until the site is reachable: no adapter is written against pages that cannot be seen or tested
- legislative.gov.in s.11A item is the government's **explanatory note on the draft** notification (OCR, 1 page), not the notification: model-fee correction in the Fourth Schedule (Rs.10–20 crore row), effective 23 Oct 2015; draft laid LS 19 Jul / RS 21 Jul 2017. Final notification not located
- "Scanned docs flagged" (Phase 1 exit) = `ocr_confidence IS NOT NULL` (any page OCR'd or mixed), not `is_scanned` (majority of pages): the corrigendum is `is_scanned=False` but its whole legal content is OCR
- Manual import checks run before any bytes enter the raw store (hash first): a refused import leaves no orphan file. Bytes the cache already fetched for that URL are refused (no manual record needed)
- Known extraction gap (3d-ii): image-region OCR thresholds (≥10% of page, <40 native chars over it) calibrated on one file; image bodies split into small tiles, or images under a real text layer, are missed. Re-check across the corpus in 3f
- **eParlib = elibrary.sansad.in** (2026-10-21): the Digital Library moved; DSpace 7 REST API used as the site's own LS debates page does. Item TEXT bundles (server-side extraction) ignored: text comes from our extractor so spans are reproducible. `Source.EPARLIB` kept for it
- sansad.in adapters call the public JSON APIs with exactly the query string the site sends (stable cache keys). The adapter keeps API values as given (e.g. a lagging `status`) and never infers act linkage from them (base.py rule)
- Bill stages are distinct primary documents: as introduced, as passed by LS / RS, as passed by both Houses, errata. Differences between stages are genealogy evidence (changes during passage)
- The 1995 Bill (as introduced, scanned) is a primary cross-check for the 1996 base text, not a substitute: the Act as passed may differ
- RS debate PDFs are on `cms.rajyasabha.nic.in` (new domain, robots unchecked until first fetch in 3f). `DocRef.lang` gains `mixed` for the RS floor version (each member's own language)
- Extractor v4 (2026-10-21): same-block line fragments that overlap vertically, are ≤ 1.5× text size apart and start with whitespace are one line (HTML-printed debate PDFs); table cells on one row do not start with a space and stay separate. Footnote markers require PyMuPDF's superscript flag **and** size ≤ 85% of the page's median span size (the flag alone fires on baseline jitter)
- Known extraction gap (3d-iv): stray HTML fragment (`an>`) at the top of elibrary debate pages
- Errata `.doc` files linked from sansad.in bills are not extractable (cached as format `other`); whether to convert or import them manually is decided in 3f
- elibrary.sansad.in search results are noisy (a query matches unrelated discussions): which debate items belong to a bill is a manifest decision, made by matching item dates to the bill's stage dates, never by the adapter. `EparlibAdapter.search` returns `(items, total)` so a page-capped result is visibly partial
- Extractor v4's footnote-marker rule (superscript flag + size ≤ 85% of the body median) was calibrated on PRS/e-Gazette/elibrary PDFs only; unverified on India Code PDFs — re-check if they become available
- sansad getBills filter values are percent-encoded (titles contain `&`)
