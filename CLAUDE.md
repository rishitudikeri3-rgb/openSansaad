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
│   ├── manifest.toml           # corpus decisions: doc types, act links, numbers (committed)
│   ├── manual_imports.jsonl    # provenance of hand-downloaded files (committed)
│   ├── raw/{source}/           # untouched downloads (gitignored)
│   ├── text/                   # extracted text (gitignored)
│   └── ground_truth/           # hand-built genealogy (committed)
├── src/opensansad/
│   ├── config.py               # pydantic-settings
│   ├── ingest/
│   │   ├── cache.py            # ONLY network entry point
│   │   ├── store.py            # append-only versioned raw store (written only via cache.py)
│   │   ├── ratelimit.py        # Redis per-domain gate + blocks
│   │   ├── locks.py            # per-URL fetch locks (Redis across workers)
│   │   ├── manual.py           # hand-downloaded imports → data/manual_imports.jsonl
│   │   ├── manifest.py         # loads + validates data/manifest.toml
│   │   ├── persist.py          # manifest → acts/documents rows + extracted text
│   │   ├── certs/              # bundled CA certs (e-Gazette)
│   │   └── sources/            # base.py, prs.py, egazette.py, sansad.py, eparlib.py (later: indiacode.py)
│   ├── extract/                # document.py, normalize.py, pdf.py, ocr.py, html.py, pipeline.py
│   ├── parse/                  # transcription.py, clause_tree.py, load.py, amending_ops.py, load_ops.py
│   ├── db/                     # base.py, models.py, session.py, migrations/
│   ├── graph/                  # client.py, clauses.py (clause trees; later genealogy ops)
│   ├── agents/                 # llm.py (only model-client module, disk cache), genealogy.py, contradiction.py, intent.py, verification.py
│   ├── intent/                 # evidence.py (evidence docs per Act, passages, verbatim locate, speaker attribution)
│   ├── workflows/              # genealogy.py (LangGraph: rules → Genealogy → Contradiction loop), intent.py (tiers → verify → refuse)
│   ├── eval/                   # genealogy.py (P/R vs ground truth, baselines, revision scoring; writes data/genealogy/)
│   ├── tasks/                  # celery_app.py, ingest_tasks.py (`make worker`)
│   └── api/                    # app.py (demo FastAPI: /api/act, /api/provision/{path}) + static/index.html (D3 lineage view; `make demo`)
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
          acquisition, authority, format, raw_path, is_scanned, ocr_confidence, ocr_regions_dropped,
          text_path, text_sha256, extractor_version, meta,
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
        heading, text, footnote_refs, source_doc_id, ord, page_start, page_end,
        text_path, text_sha256, span_start, span_end, meta)
  node_type ∈ {long_title, preamble, enacting_formula, part, chapter, section,
               subsection, clause, subclause, proviso, explanation, schedule,
               article, paragraph}
  unique(act_id, version_label, path); text = own text only; span covers the subtree

amendment_ops(id, act_id, amending_doc_id, ord, provision, op_type, unit, target_path,
              anchor_path, old_text, new_text, text_path, text_sha256, span_start, span_end,
              status, confidence, reason, meta)
  op_type ∈ {insert, substitute, omit, renumber, split, merge, other}
  status  ∈ {resolved, unresolved}
  unique(amending_doc_id, ord); provision = the amending instrument's own item (s3.cl(ii));
  span = into the instrument's extracted text (text_sha256 checked); resolved → confidence 1.0,
  unresolved → confidence NULL + reason
```

Neo4j: `(:Clause {act_id, version_label, path, node_type, number, heading, ord})` (unique on act_id + version_label + path) with `CHILD_OF` edges. Phase 2 adds `DESCENDS_FROM {op_type, confidence, source_doc_id}`.

---

## 9. Phase plan

**Phase 1 — Foundation** — done (2026-10-28)
1. Skeleton + docker stack + smoke tests
2. Data recon: shortlist 3 acts, assess source quality, pick one
3. Ingestion: cache, source adapters, extraction, OCR detection, Celery tasks
4. Clause parser for the principal act; rule-based amendment-op extractor (formulaic cases only, everything else `unresolved`)

Exit criteria: full stack up; act chosen and recorded; principal + all amending acts cached/extracted/in DB; scanned docs flagged; debates & committee reports for the act fetched; sansad.in + PRS access verified with samples cached; clause tree in Postgres + Neo4j; resolved/unresolved op ratio reported; footnote coverage assessed; all tests pass offline.

**Phase 2 — Agentic core:** ← CURRENT hand-built ground truth for every amendment, Genealogy Agent, Contradiction loop, evaluation harness (precision/recall, correction count, single-pass baseline).

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
  - 3f (ingestion run) — split into 3f-i discovery + bill stages → 3f-ii debates (LS, RS, 1996) → 3f-iii reports, judgments, manual items, exit check
    - 3f-i — **done** (2026-10-21): `ingest/discover.py` (`python -m opensansad.ingest.discover bills|debates-ls|debates-rs …` prints candidate `[[document]]` blocks, never writes); 15 sansad.in stage documents added (as introduced / passed LS / passed RS / passed both Houses, Gazette copies, 2018 errata .doc, the 2022 Private Member's Bill). Manifest 30 documents; DB 30 rows; masthead checks pass; 5 sansad copies byte-identical to PRS copies
    - 3f-ii — **done** (2026-10-21): 20 debate documents. LS (elibrary): 12 items — 2015 introduction, Ordinance 9/2015 explanatory statement, 17 Dec (passed); 2018 introduction, 10 Aug (passed); 2019 1 Aug (passed); 2021 introduction, Ordinance 14/2020 explanatory statement, 12 Feb (passed); 1996 1 Aug + 2 Aug (passed); 2022 Private Member's Bill introduction (title only). RS (cms.rajyasabha.nic.in, whole-day PDFs): 23 Dec 2015, 15 + 18 Jul 2019, 10 Mar 2021 (Floor + English), 22 Jul 1996 (Floor only; 2015 also Floor only). Run on the Celery worker: 20 added, 0 errors; manifest 50, DB 50 rows; every RS day's text names the Bill
    - 3f-iii — **done** (2026-10-21): 7 documents. elibrary: **Bill XXX-C of 1995 as passed by RS** (items 79403, 159905: two scans) and RS Bill LXXV of 2003 (withdrawn). Law Commission (`committee_report`, source `other`): Report 246 (two parts), Supplementary to 246 (2015), Report 176 (2001). Manifest 57, DB 57 rows, 0 errors; 9 with OCR'd pages, 3 with image regions left unread. Write-up and Phase 1 exit check in `docs/phase1-ingestion.md`. Browser-only items (Srikrishna HLC report, SC judgments, e-Gazette searches) left for manual import
  - 3e split into 3e-i manifest + persistence → 3e-ii Celery tasks, per-URL lock, listing refresh
    - 3e-i — **done** (2026-10-21): `data/manifest.toml` (1 act, 15 documents: every cached document file), `ingest/manifest.py` (validation lists every problem; manual entries take source/type/title/authority from the import log), `ingest/persist.py` (`python -m opensansad.ingest.persist [--offline] [--no-ocr]`: upsert on (url, sha256), extraction when text is missing / changed on disk / older extractor, masthead check, report with scanned + stale rows). Migration `48e8e5a42dd2` (`text_sha256`, `extractor_version`, `meta`). Dev DB loaded: 15 rows, 4 with OCR'd pages, all mastheads agree with the manifest
    - 3e-ii — **done** (2026-10-21): `ingest/locks.py` (`RedisLock` / `MemoryLock`) held by `Cache` around every fetch, robots.txt fetch and `import_local`, with a re-check of the store once held; `Cache.get(max_age=…)` + adapters' `listing_max_age` (listing/API pages only; `SourceAdapter.from_settings()` applies `LISTING_TTL_HOURS` — for the 3f / Phase 4 discovery code, no task uses adapters yet); `tasks/ingest_tasks.py` (one task per manifest entry, chord → summary report; stale re-extraction; `python -m opensansad.tasks.ingest_tasks manifest|stale`); persist split into `ingest_entry` / `reextract` / `summarize` shared by CLI and workers; getBills merge conflicts logged. Verified end to end with `make worker` on the dev DB (15 unchanged, 0 errors)

- Phase 1, step 4 (parser) — split into 4a checked base-text transcription → 4b clause parser (`clauses`, Neo4j `CHILD_OF`) → 4c amendment-op extractor (resolved/unresolved ratio) → 4d Phase 1 exit
  - 4a — **done** (2026-10-28): `data/transcriptions/bill-xxx-c-1995.{txt,toml,notes.md}` (Bill XXX-C as passed, PDF pages 6–41: preamble, ss.1–86, three Schedules), `parse/transcription.py` (`draft`: cleaned-image Tesseract; `check`: lines no witness contains). All 86 sections and pages 6–41 present; WIPO typed copy (manifest row, secondary) used as a witness only. Follow-up (2026-10-28): every flagged line reviewed and listed in `bill-xxx-c-1995.accepted.txt` (`check` skips them, reports stale ones); `numbering()` checks parts/chapters/sections/sub-sections/clauses/articles in sequence (catches the short marker lines the witness check skips); Schedule pages 33–41 all read in full on the images (6 more fixes); `.toml` lists witness paths and draft tool versions, so `check NAME.txt` runs from it; notes state the as-printed rule and tabulate every editorial change. `check` exits 1 on any flag, stale accepted line or numbering break (currently 0 / 0 / 0)
  - 4b — **done** (2026-10-28): `parse/clause_tree.py` (`show` / `load`), `parse/load.py` (Postgres, replaces one version in one transaction; graph written before commit), `graph/clauses.py` (`replace_tree`, refuses if genealogy edges touch the version unless `--force`); `clauses` table (migration `a2f5a9b20067`). Base text loaded as version `1996-enacted` of act 1, citing documents row 56: 553 nodes (86 sections, 191 sub-sections, 142 clauses, 26 sub-clauses, 12 provisos, 8 Explanations, 4 Parts, 12 Chapters, 3 Schedules, 27 articles, 39 paragraphs), 542 `CHILD_OF` edges, 0 parse warnings. Provisos / Explanations counted against the text (12 / 8). `[[flush]]` marker added to the transcription (11 list tails)
  - 4c — **done** (2026-10-28): `parse/amending_ops.py` (rule-based extractor: instruction tree, quoted blocks parsed with `clause_tree.parse_fragment`, known-paths state; `show` / `load`), `parse/load_ops.py` (one copy per sha256, e-Gazette > sansad.in > PRS; replaces each instrument's rows), `amendment_ops` table (migration `2ed6176fde15`). **Acts: 94 resolved / 11 unresolved (90%)** — Act 3 of 2016 46/9 (Explanation → Explanations 1 and 2 in ss.34/48/57, s.37(1) (a)(b) → (a)(b)(c): unequal substitutions), Act 33 of 2019 45/1 (s.15 amends Act 3 of 2016, not the principal Act), Act 3 of 2021 3/1 (where the quoted Explanation attaches). **Ordinances: 9 / 33** (after the roman-kind fix) (Ordinance 9 of 2015 is OCR with margin notes mixed into the body — known extraction gap; Ordinance 14 of 2020: Explanation printed outside the quoted proviso, stray margin fragments after s.3's quote). Ops loaded for act 1 against `1996-enacted`
  - 4d — **done** (2026-10-28): exit check in `docs/phase1-ingestion.md` §6–7. All criteria met except footnote coverage, **not assessable** (India Code unreachable). **Phase 1 closed**
- Phase 2 (agentic core) — **done for the submission version** (2026-10-28): ground truth (105 ops), `agents/llm.py` (Groq, JSON mode, disk cache `data/llm_cache/` committed for offline re-runs), Genealogy Agent (cases with candidate old units + slots; answers validated, invalid ones re-asked), Contradiction Agent (deterministic checks: mispaired / old_twice / kind / dangling / scope; model judges each conflict; LangGraph loop ≤ 3 rounds), eval harness (`python -m opensansad.eval.genealogy`). Results in `docs/phase2-genealogy.md`: label pairing 3/6 hand groups; single-pass 6/6 (120B) and 5/6 (20B); agentic-bulk 6/6 with **1 revision, wrong→right** (s.37(1) label mispairing caught by the similarity check). Caveat: one act, fixes designed on the same data
- Phase 3 (intent & verification) — **done for the submission version** (2026-10-29): `intent/evidence.py`, `agents/intent.py`, `agents/verification.py`, `workflows/intent.py`. 42 units (one per amending-Act section): **26 sourced (62%)** — SOR 17, debate 6, Law Commission 3; 16 not determinable, 0 incomplete; 26 draft claims blocked. Hand calibration: 7 refusals justified, 6 probably justified, 3 misses (s.36 and Schedules in LC 246; 2019 SOR on s.26 of the 2015 Act). Write-up `docs/phase3-intent.md`
- Demo (submission version) — **done** (2026-10-29): `api/app.py` + `api/static/index.html` (`make demo`; `make site` → static export published on the `gh-pages` branch for GitHub Pages)

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
- robots.txt is re-fetched every `ROBOTS_TTL_DAYS` (7) — an exception to "never re-fetch" (the other: listing pages with `max_age`, 3e-ii); RFC 9309 semantics (4xx = allow all, 5xx = disallow)
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
- **Base text of the 1996 Act** (revised 3f-iii; e-Gazette lists the Aug 1996 Gazettes but files are "NOT available"; India Code down): **Bill No. XXX-C of 1995 as passed by Rajya Sabha (22 Jul 1996)**, primary. The LS debate of 2 Aug 1996 records its passage **without amendment** (all clauses, Schedules, enacting formula, Preamble, Title added; the one notice, to cl. 34, not moved), so it is the text passed by both Houses (assent 16 Aug 1996). Bill annexures stay as intermediate-version checks. The **secondary consolidated copy is no longer required** (optional OCR / current-text cross-check, never cited). Re-check e-Gazette / India Code periodically and swap in the Gazette text if available
- Adapters return `DocRef`s and never write to the DB (3e persists). A listing page never decides act linkage or amending-act numbers ("(Amendment) Act, 2015" is Act 3 of 2016): those come from the manifest and the document text. Unclassified links are kept with `doc_type=None`, not guessed. Layout drift raises `LayoutChanged`
- PRS summaries → `external_report` (secondary analysis), never a primary source for what government/Parliament said
- Cache keys use RFC 3986 §6.2.2 normalization (encode disallowed chars, decode %XX only for unreserved, uppercase hex; reserved chars untouched). URLs probed by hand on 2026-10-21 with `%28…%29` / `%2C` differed from adapter keys with `(…)` / `,`: 5 PRS PDFs, re-fetched under the adapter keys in 3e-i (bytes identical), their old rows deleted; the old raw entries stay (never deleted). Manifest URLs are written exactly as the adapters produce them
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
- elibrary.sansad.in search results are noisy (a query matches unrelated discussions): which debate items belong to a bill is a manifest decision, made by checking the item's date falls within the bill's passage (introduction → last stage reached; a discussion can begin days before the stage it ends in: 16 Dec 2015 → passed 17 Dec), never by the adapter. `EparlibAdapter.search` returns `(items, total)` so a page-capped result is visibly partial
- Discovery (3f-ii): `debates-ls` takes several queries, merged by item (one query missed the 2015 introduction within its first 200 hits); a capped query prints `# PARTIAL` in the output, not only on stderr; default `--max-pages` 10. `--files` needs `--only HANDLE…` (items chosen by hand from a listing), so the tool never fetches every item in a passage window. Comments separate items whose title **names** the bill (name up to the year + the year, if the title has one) from those whose date only falls within its passage
- Extractor v4's footnote-marker rule (superscript flag + size ≤ 85% of the body median) was calibrated on PRS/e-Gazette/elibrary PDFs only; unverified on India Code PDFs — re-check if they become available
- sansad getBills filter values are percent-encoded (titles contain `&`)
- Manifest (3e-i): `data/manifest.toml`, TOML (stdlib `tomllib`, no new dependency). Only document files go in it, not listing / API pages. `number` = the instrument's own number (amending act or ordinance) → `amending_act_number/year`
- Masthead check: every e-Gazette PDF, and amending acts / ordinances from other sources (PRS hosts Gazette copies), have their masthead read into `documents.meta` from the extraction just made. If its doc type or number disagrees with the manifest, the entry is refused (rolled back, reported, exit 1), never resolved silently either way. Waivable per entry only with `masthead_check = false` plus a note (for OCR misreads). PRS copy of the 2015 Amendment Act confirmed by its masthead as No. 3 of 2016
- `documents.raw_path` / `text_path` are relative to DATA_DIR (`raw/...`, `text/...`). `documents.meta` is JSONB on Postgres (JSON elsewhere)
- persist commits once per entry: a refused or failing entry never blocks or half-writes the others. Text written for a rolled-back entry is harmless derived data (re-extracted next run)
- Manifest `corrects = "N/YYYY"` for corrigenda, checked against the masthead's target like `number` (a masthead target with no `corrects` in the manifest is refused)
- persist report lists rows needing a hand decision — `not in manifest`, `superseded` (not the latest bytes at its URL), `identical bytes` at several URLs — and never deletes anything. Any exception in one entry (corrupt PDF, OCR crash, I/O) is rolled back and reported for that entry only
- Byte-identical copies (e-Gazette 225832 = PRS copy of Act 3 of 2021) keep one row per URL (provenance). Step 4 parses **one copy per sha256, in the order e-Gazette > sansad.in > PRS**, so clauses are not duplicated
- Per-URL lock (3e-ii) is per raw-store entry (source + url hash = one `meta.json`), taken inside `Cache`, not in tasks, so every caller is covered. Redis `SET NX PX` + token, compare-and-delete release (an expired lock taken over by another worker is never released by its old holder). Fixed expiry `URL_LOCK_SECONDS` (900 s, > worst-case single fetch), no renewal; waiting gives up after `URL_LOCK_WAIT_SECONDS`. Lock order page → robots only (no deadlock)
- Listing refresh: `Cache.get(max_age=…)` re-fetches a copy last confirmed longer ago (identical bytes only bump `last_seen`); a failed refresh **raises**, never serves the stale copy silently. Adapters default to `listing_max_age=None` (never refresh); `fetch(..., listing=True)` marks listing/API calls (sansad, elibrary search + item files, PRS bill-tracker pages). Document PDFs are never refreshed
- Celery: **no Celery-level retries** (the cache already retries with backoff; more would multiply requests to rate-limited sites). Resume = re-run the manifest (idempotent); `task_acks_late` re-delivers a dead worker's task. Acts are created once before the fan-out (parallel entries would race on the unique key). Soft time limit 30 min per entry, hard limit +5 min; broker `visibility_timeout` 2 h (> hard limit, so a running task is never re-delivered). A per-entry Redis lock (`ingest:<url hash>`) serializes tasks for one entry (re-delivery, double enqueue). Only workers build a `Cache` (lazily); the enqueuing process never does. Stale rows outside the manifest are reported, not re-extracted (their settings are unknown)
- getBills duplicate-row disagreements: status (routine API lag, rule: furthest wins) logged at INFO; any other field at WARNING, first row's value kept
- "Superseded" in the persist report = not the raw store's latest version of its URL (read from the store, not from the run's results, which a re-extraction run may fill with older versions)
- Known limitation (3e-ii): two concurrent `force=True` fetches of one URL both hit the network, one after the other (the lock serializes them; force means fetch regardless)
- Discovery (3f-i) prints candidates only; values a listing cannot be trusted for (act, doc_type when unclassified, `number`) are commented prompts unless given (`--act`). Hindi documents filtered out (Hindi errata of the 2015 / 2021 Bills skipped: they correct only the Hindi text)
- Errata `.doc` (decided 3f-i): cached with a row (format `other`), no text, no converter dependency; convert by hand and import as manual if ever needed
- Private Member's Bills (2022 Bill amending s.20, lapsed) are kept as context: proposals attributed to the member, never the government's position
- sansad.in vs PRS copies (3f-i): 2015 / 2019 / 2021 Bills as introduced and the 2015 / 2021 Gazette copies are byte-identical; PRS's 2018 Bill is the "TO BE INTRODUCED" print (first line only differs from sansad's "As INTRODUCED")
- Manifest titles of PRS bill copies carry their stage and "PRS copy" (e.g. "(as introduced, PRS copy)"), so copies of one stage are told apart in reports
- Bill errata are typed `bill` (they belong to the bill's stages on sansad.in) and Gazette corrigenda `notification` (published in the Gazette like one): each follows its source's home
- No parliamentary committee referred any of the 2015–2021 Bills (getBills: no referral dates): no `committee_report` documents exist for this act's amendments — a finding, not a gap
- Debate selection (3f-ii): only the Bills' own proceedings (introduction, explanatory statements on Ordinances, discussion/passage). Procedural items (messages from RS, business statements, Ordinances laid in bulk) skipped: their facts are in the bill stage dates. Ordinance explanatory statements kept: the government's own stated reason for legislating by Ordinance
- RS debates: Floor (as spoken) **and** English version per day where both exist; Hindi and Written Answers / Q&A parts skipped. Each PDF is the whole sitting; locating the Bill's section is Phase 3 work, not the manifest's
- **Earlier amendment attempt found** (3f-ii): Arbitration and Conciliation (Amendment) Bill, **2003** (withdrawn), examined by the **Standing Committee on Personnel, Public Grievances, Law and Justice, 9th Report** (presented LS 4 Aug 2005, elibrary 123456789/421467, evidence 421468). Never enacted, so no genealogy event, but intent context for the 2015 Amendment (Law Commission 176th → 2003 Bill → 246th). Report not located online (3f-iii; context only); the "no committee reports" finding above applies to the enacted amendments only
- **1996 LS debates are not scans** (3f-ii): elibrary serves pre-2009 items as ReportLab-built PDFs carrying the library's own OCR text positioned on the page, with no page images: our OCR cannot run on them and their text quality is the library's. Phase 3's "OCR on 1996 debates" is exercised by the **RS 22 Jul 1996** Floor PDF instead: its 70 image regions are Hindi / Urdu speeches, so Phase 3 needs Tesseract `hin` (and `urd`) — the English proceedings in it are native text
- **Extractor v5** (3f-ii): an image region's OCR text enters the English stream only if it **reads as English**: at least `MIN_FUNCTION_WORDS` (25%) of its Latin tokens are common English function words (the, of, and, shall, …); regions with < 20 tokens are kept only at OCR confidence ≥ 0.8. Why: Hindi / Urdu speeches pasted as images in RS 1996 / 2019 debates OCR (eng) to Latin gibberish that **passes `wordlike_ratio` (0.74–0.90)**. Measured on the corpus: garbage regions 0.04–0.21 function words, English OCR pages 0.32+ (worst 1995 Bill page, conf 0.51), corrigendum body 0.39. **Rejected signals:** OCR confidence (garbage up to 0.72 vs poor English scans from 0.51) and Tesseract OSD (labels poor English scans of the 1995 Bill Arabic / Devanagari / Cyrillic and a table Katakana, at script confidences overlapping real Hindi). Known cost: an English table pasted as an image (few function words) is dropped too — counted and reported, never silent. Whole-page OCR is not filtered: no whole-page-OCR'd page in the corpus is non-English (checked 3f-ii); Hindi scans stay a known gap until `hin`. Dropped regions counted per page (`ocr_regions_dropped`, sidecar) and per document (`documents.ocr_regions_dropped`, migration `092ecb5d2e51`; NULL = extracted before the column → stale), like legacy lines; a page whose regions are all dropped is `native`. persist report lists "image regions left unread": with "scanned", that is the Phase 1 flagging of documents with image-only content (RS 22 Jul 1996: 70 regions; RS 15 Jul 2019: 1 Urdu speech). Why: Hindi / Urdu speeches pasted as images in RS 1996 / 2019 debates OCR (eng) to Latin gibberish that **passes `wordlike_ratio` (0.74–0.90)**, at conf 0.19–0.72; OSD named 62 Devanagari, 5 Arabic and 4 Latin (Hindi tables) of those regions; the Gazette corrigendum body is Latin at 0.95. Whole-page OCR of Hindi scans is unchanged (known gap until `hin`)
- Known extraction gaps (3f-ii): (a) fixed in v5 (above); (b) 1996 LS two-column pages: the other column is read as `[[margin: …]]` notes (80 in the 2 Aug item); (c) RS whole-day PDFs: table figures in written answers become `[[margin: 4.11]]` notes; (d) stray `an>` confirmed on most 2015–2021 elibrary items (first line)
- Bill XXX-C scans (3f-iii) carry the library's own OCR text layer, kept (our Tesseract is no better overall). It drops a leading "1" in numbers ("9." for 19, "2." for 21, "996") and misreads numerals ("4]." for 41, "5l." for 51, "CHAPTER HI"): the base text is **hand-verified against the page images** for ground truth, and the step-4 parser reports gaps / misreads in section numbering as errors, never as text
- Law Commission and expert-committee reports are typed `committee_report` (source `other`): their reasoning is **attributed** (Law Commission, Report N, para …), never stated as fact or as the government's position. Law Commission files are on `cdnbbsr.s3waas.gov.in/s3ca0daec69b5adc880fb464895726dbdf/uploads/YYYY/MM/` (the S3WaaS host behind lawcommissionofindia.nic.in), plain GET
- Report 246 part 1: the one image region left unread is the Chairman's bilingual letterhead — the known cost of v5's English test, harmless
- legalaffairs.gov.in (3f-iii): 403 on robots.txt (WAF), domain blocked by the cache; not worked around. Srikrishna HLC report (`/sites/default/files/Report-HLC.pdf`) is a browser-download manual import
- Supreme Court judgments (*BCCI v Kochi* 2018, *HCC v UoI* 2019): no official copy at a plain URL (SC search needs forms + captcha) → manual import. Third-party copies (IBBI, LawSathi) not used as primary
- Re-check 2026-10-21 (3f-iii): India Code still 403 on robots.txt; eparlib.sansad.in still connection failures (blocked 1 h)
- elibrary.sansad.in also has Bills and Reports collections (source of Bill XXX-C, the 2003 Bill); its Acts collection has nothing for this act
- **Base text is a checked transcription** (4a): the library OCR of Bill XXX-C cannot be parsed (italic markers read as "(7)"/"(0)", lost leading "1"s), so step 4 parses `data/transcriptions/bill-xxx-c-1995.txt`, typed from page images; clauses still cite the scan's `documents` row (79403, row 56) and the transcription's `.toml` records that file's sha256. Format: `[[page: N]]` (1-based PDF page), `[[margin: …]]` before the paragraph it heads (act citations like "5 of 1908." kept as margin lines), headings as printed, straight quotes as printed, printed page and line numbers dropped, 2+ dashes → `—`
- Transcription check = `verified = "model"` (read by the assistant against the images, not yet by a human): every line no witness contains (both library OCR layers, cleaned-OCR drafts of both scans, WIPO typed copy) re-read on the image. A human pass is still needed for ground truth (Phase 2)
- Show-through cleaning for these scans: grey ≤ 90 → black, ≥ 150 → white, linear between (`parse/transcription.clean_level`)
- **Schedules (pp33–41) not typed by hand**: long verbatim reproduction of the treaty text was cut off by the model API's output filter, so they come from the cleaned-OCR draft of scan 159905 token-voted against scan 79403, then corrected line by line against the images and diffed word by word against the WIPO copy (no wording differences left except WIPO typos). Every Schedule page then read in full against the images
- Printed oddities kept as printed, never corrected: pasted correction slips over s.9, s.11(12), s.36–37, s.85(2), s.86; s.43(4) "exclued"; **s.36 is incomplete as printed in both scans** (slip hides the words after "award under"; next line "award shall be endorced"). No conjectural text: the gap is a Phase 2 ground-truth question for a primary source (1996 Gazette), not filled from the WIPO copy
- WIPO Lex text of the Act (`wipo.int/wipolex/en/text/207821`, manifest, `authority = secondary`): witness for transcription checks only, never cited
- Transcription rule (4a): **as printed** — wording, spelling, capitalization and printed errors kept (sic, never corrected). Editorial changes only for format rules (page/line numbers dropped, 2+ dashes → `—`), specks that are not type, and visibly damaged punctuation where the sentence runs on (s.44(b), s.65); each one is listed in the notes' table
- Reviewed witness flags live in `data/transcriptions/{name}.accepted.txt` (exact lines, `#` comments): a flag is closed only by re-reading the line on the image, never by editing the witness. The `.toml`'s `witnesses` are repo-relative paths (data/text is gitignored and rebuilt by persist / `draft`)
- Clause paths (4b): `long_title`, `preamble`, `enacting_formula`; containers `pt1`, `pt1.ch2`; sections `s9`, `s9.ss1`, `.cl(a)`, `.sc(ii)`, provisos `.p1`, Explanations `.expl1` (numbered within their parent); Schedules `sch1`, `sch1.art5`, `sch1.art5.para1` (both "1." and "(1)" in a Schedule are paragraphs), `sch2.para1`. Sections and Schedules are top-level paths whatever Part / Chapter holds them (path survives a Chapter renumbering); `parent_path` and the graph's `CHILD_OF` record the container. "PRELIMINARY"-style group headings go to the next section's `meta.group_heading`
- Parse rules (4b), deterministic, every judgment call recorded in `meta` and the report: a proviso attaches to the **deepest open provision** (4 in the base text attach to a clause / sub-clause: s34(2)(a)(iv), s48(1)(c), s57(2)(c), s64(2)(b)); an Explanation attaches to the open sub-section unless it begins "In this section" (then the section: s47, s56, s65) or names a sub-clause / clause of an open provision ("Without prejudice to the generality of sub-clause (ii)": then that provision — s.34's Explanation is `s34.ss2.cl(b).expl1`, as the 2015 Act cites it, "the Explanation to sub-clause (ii) of clause (b)"); (i)/(v)/(x) are clauses after (h)/(u)/(w); an item continues the nearest open list of its kind, else opens a new list with a warning; a line after one ending in a cross-reference ("sub-section", "of", ") and") is never a marker; margin notes head the next section / sub-section / article, margin act citations go to `meta.margin_citations`; line-end hyphens join without a space. `load` refuses on any parse warning
- Transcription marker `[[flush]]` (4b): the next line is set full out after a list and belongs to the provision that opened the list (s.4 "has not been complied with…"); its text is that node's own text after a line break. `transcription.check` ignores the marker
- Clause versions: `version_label` comes from the transcription's `.toml` (`1996-enacted` for the base text). A load replaces every row and graph node of that (act, version); spans are **character** offsets into `text_path` (relative to DATA_DIR) and valid only while it hashes to `text_sha256`. The act and `source_doc_id` come from the documents row with the transcription's `raw_sha256`, never from the file name
- Amendment ops (4c) are **deterministic rules only**; anything a rule does not read, or reads ambiguously, is `unresolved` with a `reason` for the Genealogy Agent — never guessed. Resolved = the target path exists in the known paths (or, for an insert, is new and of the kind named) and any quoted old words are found in the base text exactly once (or with "wherever they occur"); words in a provision changed since the base are "not checked" (meta), not refused
- Known-paths state (4c): starts from `1996-enacted`; instruments processed by (year, number); each Act's ops are applied before the next instrument is read. Ordinances are checked against the state before them but **not carried** (the replacing Act re-enacts their changes; differences between Ordinance and Act are Phase 2 evidence)
- A substitution whose quoted units differ in number from those it replaces (Explanation → Explanations 1 and 2; clauses (a),(b) → (a),(b),(c)) is **unresolved for every unit**: pairing by label is not evidence of lineage. Equal counts pair by path
- A quoted block that never closes (2015 s.12(5): closing quote lost after "writing.") ends at the next margin note "Amendment/Insertion/Substitution/Omission of…" or next section naming "principal Act", with `meta.quote_unclosed` and a warning
- Ops on other instruments (2019 s.15 → s.26 of Act 3 of 2016) are recorded unresolved ("amends another instrument"); commencement / retrospective wording ("deemed to have come into force on …") is read into `meta.effective_from`; a margin note naming other sections than the op targets is flagged in `reason`
- Findings (4c): Ordinance 14 of 2020 prints its Explanation **outside** the quoted proviso (the 2021 Act inserts it) — flagged, read by hand in Phase 2; Ordinance 9 of 2015 s.23 seems to differ from the Act's s.56 change (read on the scan in Phase 2)
- **Two tracks (2026-10-28, replaces the deadline triage):** (1) **Submission version** for the course, due in 2 days: Phase 1 as built + ground truth for the Acts' ops + Genealogy Agent (unresolved ops only) + Contradiction loop + eval vs single-pass baseline + SOR-only Intent with Verification refusal + a minimal demo (FastAPI + one D3 page for a few sections). (2) **Full version** after submission, for a public launch: Phases 3–5 in full, Phase 4 breadth, React app, deploy, human-verified ground truth, Hindi OCR, India Code footnotes when reachable. The submission version is a **subset of the full one, not a fork**: same repo, schema and rules; nothing built for the deadline is thrown away
- LLM provider (2026-10-28): **Groq free tier**, OpenAI-compatible (`LLM_BASE_URL=https://api.groq.com/openai/v1`); `MODEL_REASONING=openai/gpt-oss-120b`, `MODEL_BULK=openai/gpt-oss-20b` (JSON mode verified). Key only in `.env`. Free-tier limits apply: LLM calls cached on disk, retries with backoff
- Ground truth (Phase 2, 2026-10-28): `data/ground_truth/arbitration-1996.toml`, one `[[op]]` per operation of the three Acts (105; Ordinances excluded), `old → new` = the DESCENDS_FROM edge (absent key = none; words-level change keeps the path). 94 `decided = "rule-checked"` (4c output re-read against each instruction), 11 `decided = "hand"` with a note. Key hand decisions: s.37(1) 2015 — old (a) → new (b), old (b) → new (c), new (a) inserted (**label pairing would be wrong**); ss.34/48/57 — old Explanation → Explanation 1 (keeps its grounds as item (i)), Explanation 2 new; 2021 Explanation → `s36.ss3.p2.expl1` ("the above proviso"); 2019 s.15 → `scope = "other_instrument"`, no edge. `checked = "model"`: a human pass is still needed
- LLM calls (Phase 2): only `agents/llm.py` may import a model client (`tests/test_network_boundary.py`), the counterpart of rule 1 for model endpoints. Every call cached on disk by (base_url, model, messages), temperature 0; the cache is committed so the eval reruns offline
- Genealogy Agent design: the model chooses only among candidates the deterministic layer builds (old units named by the instruction with their pre-amendment text; one slot per quoted unit with its allowed paths, incl. alternative attachments for a quoted Explanation / proviso) and sees the rules' unresolved reasons. Old units no slot claims are omitted. Invalid answers are re-asked with the problems listed, never repaired
- Contradiction loop: detection deterministic, judgment by the model (false alarm vs revise named groups); dismissed conflicts are not raised again; unanswered conflicts revise every group named (fail safe). First pass may use the bulk model; revisions always use the reasoning model

- Intent (Phase 3, submission version): one unit per amending-Act section; tiers searched in order **SOR (+ Notes on Clauses naming the section) → LS passage debate → Law Commission (2015 only)**, all from the corpus (no network). The Intent Agent (bulk model) may only *quote*: a passage id + verbatim text (≤ 80 words) + a one-sentence claim. The Verification Agent blocks unless (1) the quote is found verbatim in the document (whitespace / quote-mark normalized, ≥ 20 chars; gives the span), (2) the document is `primary`, (3) the speaker is not the Chair, and (4) the reasoning-model reviewer finds it states a reason, is about this amendment and the claim adds nothing. A non-verbatim quote is retried once in the tier; a reviewer rejection moves to the next tier. Nothing passes → "intent not determinable from available record" with the tiers searched. Results in `data/intent/arbitration-1996.json` (committed, with the LLM cache)
- Attribution is deterministic, never the model's: SOR / Notes → Government (signed by the minister named in the SOR); report → the committee; debate → the last speaker label before the quote (labels wrap across lines; honorific-led, no lower case). Role `government` only if the label says MINISTER or contains the SOR signatory's full name (letters only: "D. V." = "D.V."; surname alone matched "VARAPRASAD"); otherwise `member` — a member restating the Bill's purpose is reported as that member's statement, not the government's
- Demo (submission version): FastAPI reads the 1996 clause tree (Postgres), the system's lineage (`data/genealogy/arbitration-1996.json`, written by the genealogy eval in `agentic-bulk` mode with each group's instruction text) and the intent results; no model or network calls. Commencement shown as notes per Act (2019 Act commenced in part), not one date: per-provision commencement is not modelled yet. Judicial events (s.87 struck down, *HCC* 2019) are not in the lineage yet (no judgment documents imported)
