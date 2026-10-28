# Phase 1 ingestion: what the corpus holds and what it lacks

Date: 2026-10-21 (end of step 3f); updated 2026-10-28 (step 4a). Act: the Arbitration and Conciliation Act, 1996 (Act 26 of 1996).

Every document listed here is in `data/manifest.toml` (the corpus decisions), cached in the raw store, extracted by extractor v5, and stored as a `documents` row. Running `python -m opensansad.ingest.persist --offline` rebuilds the rows from the manifest and the cache, and a second run reports 58 rows unchanged.

## 1. Corpus (58 documents)

| Type | Source | Count | Contents |
|---|---|---|---|
| amending | e-Gazette, PRS, sansad.in | 6 | Act 3 of 2016 (the 2015 Amendment: PRS and sansad.in copies, byte-identical), Act 33 of 2019 (PRS Gazette copy only) and Act 3 of 2021 (e-Gazette, PRS and sansad.in, byte-identical). Step 4 parses one copy per sha256, preferring e-Gazette. |
| ordinance | e-Gazette | 2 | Ordinance 9 of 2015 (scanned) and Ordinance 14 of 2020. |
| notification | e-Gazette, legislative.gov.in | 3 | S.O. 3154(E): commencement of part of Act 33 of 2019. The corrigendum to Ordinance 14 of 2020. The explanatory note on the draft s.11A Fourth Schedule notification (imported by hand). |
| bill | sansad.in, PRS, elibrary | 21 | Every stage of the 2015, 2018 (lapsed), 2019 and 2021 Bills. The 1995 Bill as introduced. **The 1995 Bill as passed by Rajya Sabha** (two scans). The 2003 Bill (withdrawn). The 2022 Private Member's Bill. |
| debate | elibrary (LS), sansad.in (RS) | 21 | Lok Sabha: introductions, Ordinance explanatory statements and the passage debates for 2015, 2018, 2019 and 2021, plus the 1996 debates of 1 and 2 Aug and the 2023 introduction of the Private Member's Bill. Rajya Sabha: whole-day PDFs for 23 Dec 2015, 15 and 18 Jul 2019, 10 Mar 2021 and 22 Jul 1996. |
| committee_report | Law Commission | 4 | Report 246 (2014, in two parts), the Supplementary to Report 246 on public policy (2015), and Report 176 (2001). |

| principal (secondary) | WIPO Lex | 1 | Typed text of the Act (added in step 4a). A witness for checking the base-text transcription only, never cited. |

57 are primary; the WIPO copy is the only secondary document.

## 2. Base text of the 1996 Act: now from a primary source

The 1996 Gazette files are not available on e-Gazette, and India Code is unreachable. Until 3f-iii the plan was to take never-amended provisions from a secondary consolidated copy. The Parliament Digital Library has **Bill No. XXX-C of 1995, as passed by Rajya Sabha on 22 Jul 1996** (items 79403 and 159905). The Lok Sabha debate of 2 Aug 1996 records that it passed this text **without amendment**:

- every clause, both Schedules, the enacting formula, the Preamble and the Title were "added to the Bill";
- the only amendment notice, to clause 34, was not moved;
- "That the Bill be passed" was adopted.

So this Bill is the text passed by both Houses, which received assent on 16 Aug 1996. It is a primary base text for every provision.

Both PDFs are scans with an embedded OCR text layer made by the library. That layer systematically loses a leading "1" ("9." for 19, "2." for 21, "996" for 1996) and misreads Roman numerals ("CHAPTER HI"), so it cannot be parsed. Step 4a replaced it with a **checked transcription**, `data/transcriptions/bill-xxx-c-1995.txt` (PDF pages 6–41: preamble, ss.1–86, three Schedules). How it was made and checked is in its `.toml` and `.notes.md`. In short:

- it was read against the page images line by line;
- every line that no independent reading contains was re-examined (the two library layers, our cleaned-OCR drafts of both scans, the WIPO typed copy);
- the numbering of every part, chapter, section, sub-section, clause and article was checked in sequence.

It is `verified = "model"`: a human pass is still due before the Phase 2 ground truth. **s.36 is incomplete as printed** (a correction slip hides part of it), and is left as printed for a primary source to settle.

## 3. What is missing, and why

| Item | Status |
|---|---|
| 1996 Gazette (Act 26 of 1996), and the 1996 Ordinances (first, second and third) | e-Gazette lists them as "NOT available", and the files return 404. Substituted by Bill XXX-C (§2). |
| India Code (consolidated text and footnotes) | Re-checked on 2026-10-21: robots.txt returns 403 (web firewall). Unavailable, so the footnote-coverage check waits until it is reachable or a manual copy exists. |
| Old eParlib (eparlib.sansad.in) | Re-checked on 2026-10-21: connections fail (domain blocked for 1 h after 4 failures). Its content is on elibrary.sansad.in. |
| Srikrishna High-Level Committee report (2017) | legalaffairs.gov.in returns 403 on robots.txt (web firewall), and the cache blocked the domain. Needs a browser download (manual import). |
| Supreme Court judgments: *BCCI v. Kochi Cricket* (2018) and *Hindustan Construction Co. v. Union of India* (2019) | No official copy at a plain URL; the court's search uses forms and a captcha. Needs a browser download. |
| Parliamentary committee reports on the enacted Bills | None exist: no 2015–2021 Bill was referred to a committee. This is a finding, not a gap. |
| Standing Committee 9th Report (2005, on the withdrawn 2003 Bill) | Not located online. Context only. |
| The final s.11A notification; any other commencement notifications for Act 33 of 2019; the e-Gazette original of Act 3 of 2016; any commencement notification for Act 3 of 2021 | e-Gazette search is a POST form, so these need a browser search. |
| RS English versions of 23 Dec 2015 and 22 Jul 1996 | Not listed by sansad.in; only the Floor version exists. |

## 4. Scanned and partly unread documents

- **OCR'd pages (`ocr_confidence IS NOT NULL`): 9 documents.** The 1995 Bill as introduced. Ordinance 9 of 2015. The corrigendum to Ordinance 14 of 2020 (body image OCR'd in place). The s.11A explanatory note. The two scans of Bill XXX-C (1–2 pages each; the rest is the library's text layer). One page each in the RS 18 Jul 2019 Floor and English versions (a table). The Law Commission Supplementary to Report 246 (all 23 pages).
- **Image regions left unread (`ocr_regions_dropped > 0`): 3 documents.**
  - RS 22 Jul 1996 Floor version: 70 regions, Hindi and Urdu speeches.
  - RS 15 Jul 2019 Floor version: 1 Urdu speech.
  - Law Commission Report 246 part 1: the Chairman's bilingual letterhead.

  These are not read as English by design (extractor v5). Reading them needs Tesseract `hin` and `urd` (Phase 3).
- **Library OCR layers (text from the source, not from our OCR):** the 1996 Lok Sabha debates are ReportLab-built PDFs with no page images, so our OCR cannot run on them. The quality of their text is the library's.

## 5. Known extraction gaps (open)

1. 1996 Lok Sabha two-column pages: the other column is read as `[[margin: …]]` notes (80 in the 2 Aug item).
2. Rajya Sabha whole-day PDFs: figures from tables in written answers become margin notes.
3. A stray `an>` fragment on the first line of most 2015–2021 elibrary debate items.
4. Fake-bold Hindi text layers repeat lines (cover of S.O. 3154); broken Hindi conjuncts.
5. Whole-page OCR of a Hindi scan would produce English-model garbage: none in the corpus (checked), but nothing filters it.
6. Image-region thresholds were calibrated on a few files; image bodies split into small tiles, or images under a real text layer, are missed.

## 6. Phase 1 exit criteria

| Criterion | Status |
|---|---|
| Full stack up | Done: postgres, redis, neo4j in docker; workers via `make worker`. |
| Act chosen and recorded | Done (CLAUDE.md, `docs/recon.md`). |
| Principal + all amending acts cached, extracted, in DB | Amending acts: done (all three, plus both Ordinances). Principal: done from Bill XXX-C (primary, §2); the Gazette itself is unavailable. |
| Scanned docs flagged | Done: `ocr_confidence` plus `ocr_regions_dropped`, both in the persist report. |
| Debates and committee reports for the act fetched | Debates: done (LS and RS, every Bill). Committee reports: none exist for the enacted Bills; Law Commission reports done; the Srikrishna committee report waits for a browser download. |
| sansad.in + PRS access verified, samples cached | Done (3d-i, 3d-iv). |
| Clause tree in Postgres + Neo4j | Done (4b): version `1996-enacted`, 553 clauses, 542 `CHILD_OF` edges, 0 parse warnings. |
| Resolved/unresolved op ratio reported | Done (4c, §7). |
| Footnote coverage assessed | **Not assessable** (checked 2026-10-28): India Code, the only source of footnotes, still unreachable. Not a blocker for Phase 2 — footnotes were only ever a noisy validation signal; ground truth is built from the Gazette texts. Re-check if India Code returns. |
| All tests pass offline | Done: 238 offline, 20 integration (2026-10-28). |

**Phase 1 closed 2026-10-28** with the one criterion above not assessable.

## 7. Amendment operations (step 4c)

Rule-based extraction (`parse/amending_ops.py`) against the `1996-enacted` clause tree, each Act's
ops carried into the next instrument's known paths; Ordinances checked but not carried.

| Instrument | Resolved | Unresolved |
|---|---|---|
| Act 3 of 2016 | 46 | 9 |
| Act 33 of 2019 | 45 | 1 |
| Act 3 of 2021 | 3 | 1 |
| **Acts** | **94** | **11** (90% resolved) |
| Ordinance 9 of 2015 (OCR) | 8 | 29 |
| Ordinance 14 of 2020 | 1 | 2 |

Acts by operation: insert 50/1, substitute 37/9, omit 6/1, renumber 1/0 (resolved/unresolved).
Unresolved Act ops are the judgment cases Phase 2's Genealogy Agent takes: substitutions whose
unit counts change (ss.34/48/57 Explanations, s.37(1) clauses), an op on another instrument
(2019 s.15 → s.26 of Act 3 of 2016), and where a quoted Explanation attaches (2021).
Ordinance 9 of 2015's low rate is its OCR (margin notes mixed into body lines), not the rules.
