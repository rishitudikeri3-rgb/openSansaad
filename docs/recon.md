# Phase 1, Step 2 — Data Recon

Date: 2026-10-19. Candidates: Arbitration and Conciliation Act 1996, Negotiable Instruments Act 1881, Prevention of Corruption Act 1988.

Facts marked **(verify)** come from secondary sources only and must be confirmed against India Code / e-Gazette in step 3.

---

## 1. Source access findings

| Source | Result | Implication for step 3 |
|---|---|---|
| **India Code** (indiacode.nic.in) | `403` to the first two requests (honest UA, Indian residential IP), then **connection timeouts** on every request for 30+ min. Also `403` to an external fetcher. robots.txt itself returns 403. | Aggressive WAF / IP throttling. Main risk: principal-act text and footnotes come from here. The single act needs only a handful of documents, so cache.py should support **manual import** (`import_local(url, path)`), which records the source URL, sha256 and fetch date for a file downloaded by hand in a browser. Automated fetch should run at a very slow rate (≥10 s) and back off for hours, not seconds, after a 403. |
| **eParlib** (eparlib.sansad.in) | TCP connect fails from the local network and the external fetcher. DNS resolves (164.100.166.186). Old domain `eparlib.nic.in` no longer resolves. Search engines index item URLs (`/bitstream/123456789/<id>/1/<n>.pdf`). | Debates and committee reports are blocked until it is reachable. Retry on different days. Search engines can find item URLs. The manual-import path applies here too. Phase 3 depends on this, so it is a risk worth tracking. |
| **e-Gazette** (egazette.gov.in) | Reachable, but the TLS chain is **incomplete**: the server sends only the leaf certificate, without the Let's Encrypt `YR1` intermediate, so verification fails. | Ship the intermediate certificate in a CA bundle for this domain. **Do not** disable verification. |
| **PRS** (prsindia.org) | Works. Server-rendered HTML; PDFs under `/files/bills_acts/bills_parliament/<year>/…`. robots.txt: **`Crawl-delay: 10`**, with only admin/search/user paths disallowed. | The cache must honour a per-domain crawl-delay read from robots.txt (10 s here, not the default 1–2 s). The richest single source of intent documents (committee reports, amendment lists). |
| **sansad.in** | Reachable. robots.txt returns 404, which means no restrictions. The page structure for debates and Lists of Business was not mapped. | Map it in step 3. It may be a JS app backed by a JSON API. |

---

## 2. Candidate assessment

### A. Arbitration and Conciliation Act, 1996 (Act 26 of 1996)

**Amending acts:** Act 3 of 2016 (the 2015 Amendment, preceded by the Oct 2015 Ordinance), Act 33 of 2019, Act 3 of 2021 (preceded by the 2020 Ordinance). Source: the India Code PDF as indexed by search.

**Structural messiness (high):**
- Inserted sections: `29A`, `29B` (2015); Part IA, `43A`–`43M`, Arbitration Council of India (2019); `42A`, `42B`, `87` (2019).
- **`s87` was inserted in 2019 and struck down** by the Supreme Court in *Hindustan Construction Co. v. Union of India* (27 Nov 2019, Art. 14). The court also revived `s26` of the 2015 Amendment Act, which the 2019 Act had repealed. A genealogy event that is not an amending act: a real test of whether the model and the Contradiction loop handle "the text says X, but X is not law".
- Eighth Schedule inserted in 2019 and **omitted in 2021**; `s43J` substituted in 2021 **(verify)**. A genuine insert-then-omit chain.
- Several 2019 provisions (Part IA) apparently **not yet notified (verify)**. The difference between enacted and in force is itself something the genealogy must represent.

**Era:** all amendments after 2009, so all debates are born-digital. **No pre-2009 amending debates → no OCR test** from this act. The 1996 principal-act debates (scanned) could serve that purpose in Phase 3.

**Intent evidence (high):** Law Commission 246th Report (2014; its proposed `s85A` was not adopted, which was raised in the LS debate); B.N. Srikrishna High-Level Committee Report (July 2017), the stated basis for the 2019 Bill; LS debates on the 2015 Ordinance disapproval resolution and the Bill (found on eParlib, e.g. items 754309, 750989); PRS bill pages for 2019 (bill text + act). Standing committee referral for the 2015/2018 Bills **(verify)**.

**Estimated size:** 3 amending acts, about 60–120 operations. Feasible to hand-verify.

### B. Negotiable Instruments Act, 1881 (Act 26 of 1881)

**Amending acts (post-1988):** the 1988 Banking, Public Financial Institutions and NI Laws (Amendment) Act (inserted Chapter XVII, `s138`–`s142`); Act 55 of 2002 (`s143`–`s147` inserted, `s138`–`s142` revised, e-cheque/truncation); Act 26 of 2015 (jurisdiction, `s142(2)`, `s142A`, `s6` e-cheque definition); Act 20 of 2018 (`s143A`, `s148`).

**Problem:** the act dates from 1881. "All amending acts" includes pre-independence amendments, Adaptation of Laws Orders and Repealing and Amending Acts. The 1934 Legislative Assembly select committee material is old scanned material on eParlib. A full trace is too much for a solo build; tracing only Chapter XVII would contradict "one fully traced act".

**Also:** a search-indexed India Code record (handle 13092) says *"Act is under updation and will be uploaded shortly"*, so footnotes may be incomplete. The main record (handle 2189) could not be checked because of the block.

**Strength:** the best era spread (1988 → 2018 crosses the 2009 born-digital line, so OCR gets real use), and Chapter XVII is a clean, well-documented chain.

**Estimated size:** Chapter XVII alone is about 40–60 operations. The full act could be several hundred, with a long tail of trivial colonial-era edits.

### C. Prevention of Corruption Act, 1988 (Act 49 of 1988)

**Amending acts:** Act 16 of 2018 (major). Earlier amendments via the CVC Act 2003 and the Lokpal and Lokayuktas Act 2013 **(verify; not confirmed)**.

**Structural messiness (low–medium):** the 2018 Act is mostly **wholesale substitution** (`s7`–`s10`, `s13` criminal misconduct narrowed, `s17A` prior approval inserted). Substitution maps old → new in one step, so the Genealogy/Contradiction loop has few multi-step chains to reason over. Commentators described it as a rewrite rather than an amendment.

**Intent evidence (very high):** PRS links the Standing Committee report (Feb 2014), the RS Select Committee report (Aug 2016), the Law Commission report (Feb 2015), the 2nd ARC 4th Report (2007), two official lists of government amendments (May and Nov 2015), and the bill as introduced vs as passed. Five years from introduction (2013) to passage (2018).

**Contestation:** `s17A` → SC split verdict, 13 Jan 2026 (Nagarathna J: unconstitutional; Viswanathan J: valid with a Lokpal/Lokayukta condition), referred to a larger bench. Excellent material for *attributed* contested reasons, but the most politically sensitive of the three.

**Estimated size:** 1–3 amending acts, about 30–60 operations.

---

## 3. Scoring

| Criterion | Arbitration 1996 | NI 1881 | PC 1988 |
|---|---|---|---|
| Amendment volume (hand-verifiable) | ✅ good | ❌ too large (full) | ⚠️ thin |
| Structural messiness (needs agents) | ✅✅ insert/omit chains, struck-down `s87`, not-notified parts | ✅ good in Ch. XVII | ⚠️ mostly substitution |
| Era spread / OCR | ⚠️ all post-2009 | ✅✅ | ⚠️ |
| Intent evidence | ✅✅ | ✅ | ✅✅✅ |
| Source availability | ⚠️ (India Code/eParlib risk applies to all) | ❌ India Code "under updation" | ⚠️ |
| Still in force | ✅ | ✅ | ✅ |

## 4. Recommendation

**Arbitration and Conciliation Act, 1996.** It has the strongest case for the agentic core: real insert → omit and insert → struck-down chains where a naive forward pass gets the final state wrong. Its size can be ground-truthed by one person, and every amendment has a documented intent trail (Law Commission → Srikrishna HLC → SOR → debate).

Accepted gap: no pre-2009 amending debates. Phase 3 OCR is exercised on the 1996 principal-act debates instead.

**Runner-up: Prevention of Corruption Act, 1988**, if intent/attribution work matters more to you than genealogy complexity.

## 5. Open issues before step 3

1. **India Code access.** ~~Try opening the act page in a normal browser.~~ **Result (2026-10-21): fails in a normal browser on home broadband AND on mobile data.** Not a client- or network-specific block: the site is down or unreachable from India in general. Manual import is not possible either.
2. **eParlib access.** **Result (2026-10-21): fails in a browser on both networks**, same as above. **Superseded (2026-10-21): the Digital Library now lives at `elibrary.sansad.in` and is reachable — see item 6.**
   → Alternatives to verify in step 3d (not yet confirmed): Legislative Department (legislative.gov.in) act PDFs for principal-act text and footnotes; sansad.in / Lok Sabha documents for LS debates; Rajya Sabha debates repository (rsdebate.nic.in); committee reports via sansad.in and PRS links. Re-check India Code / eParlib periodically: if they come back, they are preferred.
3. **Alternative-source check (2026-10-21, one GET each via the cache):**

   | Site | Result |
   |---|---|
   | legislative.gov.in | Redirects to `www.`; **`403` on robots.txt** (first request). Domain auto-blocked 6 h; no further requests. Same WAF pattern as India Code. Browser check by hand still to do |
   | sansad.in/ls | `200`, Next.js app. `__NEXT_DATA__` holds only CMS content (labels, banners, links); no debate / business data in the HTML. robots.txt `404` (no restrictions) |
   | rsdebate.nic.in | **`302` → `sansad.in/rs/debates/officials`**: the RS debates repository has moved into sansad.in. Page is Next.js with server-side props containing only labels; records are loaded client-side from an API not yet identified |

   Consequences: sansad.in is now the single reachable source for LS + RS debates (and likely committee reports, Lists of Business). Its data API must be identified in 3d-iv (from the JS bundles, a few GETs). Principal-act text has **no confirmed reachable source** yet.
4. **legislative.gov.in, browser (2026-10-21):** opens in a browser (blocks only non-browser clients). Search for the Act lists only two relevant items, both downloaded by the user and checked:
   - `static/uploads/2025/07/c9e37abb1a23bb484298e88c47ca17bb.pdf` (27 pp, scanned), listed as "The Arbitration and Conciliation Act, 1996": actually **Gazette Part VI (13 Sep 2012) carrying the authoritative *Assamese* translation** of the Act. Not usable; not imported. **No English consolidated text on the site.**
   - `static/uploads/2025/07/4a626088198adecc1bec4a662dd8f76d.pdf` (1 p, scanned, OCR conf 0.93): explanatory note, "Amendment to the Fourth Schedule". Corrects the model-fee entry for "Above Rs.10,00,00,000 and up to Rs.20,00,00,000" ("over and above Rs.1,00,00,000" → "Rs.10,00,00,000") **with effect from 23 Oct 2015**, by notification under **s.11A(2)** (draft laid LS 19 Jul 2017, RS 21 Jul 2017). **Imported** (manual, sha256 `039b4cfa…`).
   → New genealogy event type: **delegated amendment of a Schedule by central-government notification, with retrospective effect**. The notification itself (e-Gazette, likely 2017–18) still to be located.
5. **PRS + e-Gazette check (2026-10-21, via the cache):**

   | Document | Source | Result |
   |---|---|---|
   | Act 3 of 2016 (2015 Amendment), Gazette copy, 1 Jan 2016 | PRS `2015/Arbitration_and_Conciliation_Act,_2015_1.pdf` | ✅ 15 pp, born-digital |
   | Act 33 of 2019 | PRS (fixture) | ✅ |
   | Act 3 of 2021, Gazette copy, 11 Mar 2021 | PRS `2021/Arbitration and Conciliation (Amendment) Act, 2021.pdf` | ✅ 2 pp, born-digital |
   | Bill 252 of 2015 (LS, as introduced) | PRS | ✅ 28 pp, born-digital |
   | Bill 100 of 2018 (LS, lapsed) | PRS | ✅ 17 pp, born-digital |
   | Bill 16 of 2021 (LS, as introduced) | PRS | ✅ 7 pp, born-digital |
   | Original 1996 Act; 2015 & 2020 Ordinances; s.11A Fourth Schedule notification | e-Gazette | ⏳ search is an ASP.NET POST form with session IDs in URLs: not GET-able. Browser search needed; browser search (2026-10-21, Ministry of Law, Aug 1996) **lists** the 1996 Gazettes (e.g. Act 26 of 1996 likely Extra Ordinary Part II-Sec 1, 19-Aug-1996, entry 48447; commencement notification under s.1(3) appointing 22 Aug 1996, entry 48743, Part II-Sec 3(i), 22-Aug-1996), but every entry shows Gazette ID "NOT available" and the files do not open or download |

   **e-Gazette direct fetch (2026-10-21):** `https://egazette.gov.in/WriteReadData/{year}/{id}.pdf`, with `{id}` the last part of the masthead Gazette ID (`CG-DL-E-11032021-225832` → `2021/225832.pdf`), is a plain GET (200, `application/pdf`). It returned bytes **identical** (sha256 `00cfc054…`) to PRS's copy of Act 3 of 2021, so PRS's Act PDFs are the e-Gazette originals. Pattern verified for a 2021 ID only; older IDs to be checked as they come up.

   **e-Gazette fetches by ID (2026-10-21, from the user's browser search rows):**

   | Document | Search row | URL `WriteReadData/…` | Result |
   |---|---|---|---|
   | Arbitration and Conciliation (Amendment) **Ordinance No. 9 of 2015**, 23 Oct 2015 | Gazette ID "NOT available", internal no. 166406 | `2015/166406.pdf` | ✅ 24 pp, **scanned** (OCR conf 0.86–0.94). The internal number works as the file id when no Gazette ID is shown |
   | **Ordinance No. 14 of 2020**, 4 Nov 2020 | `CG-DL-E-04112020-222941` | `2020/222941.pdf` | ✅ 2 pp, born-digital |
   | **Corrigendum** to Ordinance 14 of 2020, 5 Nov 2020: s.3 marginal heading "Subsitution" → "Substitution" | `CG-DL-E-05112020-222961` | `2020/222961.pdf` | ✅ 1 p; masthead is text but the **body is an embedded image** (needs OCR) |
   | **S.O. 3154(E)**, 30 Aug 2019 (Dept of Legal Affairs, under s.1(2) of Act 33 of 2019) | Gazette ID "NOT available", internal no. 211902 | `2019/211902.pdf` | ✅ 2 pp, born-digital. Brings into force **ss. 1, 4–9, 11–13 and 15** of the 2019 Act from 30 Aug 2019. **Not commenced by it: ss. 2, 3, 10, 14** (per the Bill: s.2 definitions incl. "arbitral institution", s.11 appointments by arbitral institutions, Part IA Arbitration Council, Eighth Schedule; verify numbering against the Act) |
   | Act 26 of 1996 (internal no. 48447, published 2013) | Gazette ID "NOT available" | `1996/48447.pdf`, `2013/48447.pdf` | ❌ 404 on both. 1996 Gazettes really are unavailable |

   | s.11A Fourth Schedule notification (final, after draft laid LS 19 Jul / RS 21 Jul 2017) | Browser search Jul 2017–Dec 2018: results ignored department/keyword filters and stopped at Jul 2017 (first page only) | — | ⏳ **Not located.** Low priority: the explanatory note (imported) states the change, reason and effective date; retry month by month from Aug 2017 with Dept of Legal Affairs only |
   → Confirms **enacted ≠ in force** from a primary source: the genealogy must carry commencement per provision, from commencement notifications.
   → Genealogy edge case: **corrigenda** correct the published text without changing the law.
   → Extraction gap: a page whose masthead is text but whose body is an image escapes the OCR trigger (enough native chars, image coverage only 0.19). Also: digit-only fragments ("31", "3154", "(1)") are left behind when legacy-font Hindi lines are dropped. **Fixed in extractor v3 (3d-ii).**

   Bills carry an **annexure of extracts from the principal Act** (pre-amendment text of every provision they touch). This gives primary text of the affected 1996 provisions even without the original Act, and an independent check on reconstructed intermediate versions.

   **Annexures are as-in-force snapshots, not 1996 text (checked 2026-10-21).** An annexure prints the Act as it stood when the Bill was introduced: the 2019 Bill's annexure already contains s.11(6A) (inserted by Act 3 of 2016). So only the **2015 Bill** annexure gives 1996 wording (nothing amended the Act before 2015); the 2018, 2019 and 2021 annexures are official snapshots of intermediate versions, for checking reconstructed versions. The 2019 annexure still prints the uncorrected Fourth Schedule fee ("over and above Rs. 1,00,00,000" in the Rs.10–20 crore row): consistent with the s.11A correction not being notified by July 2019, or annexures not reflecting it (**verify**). Elided text is marked by runs of `*` lines: an extract gap, never an omission. Reading annexures belongs to the step-4 clause parser.
6. **sansad.in + elibrary.sansad.in (2026-10-21, ~20 GETs via the cache; robots.txt 404 = allow all).** Pages are Next.js; data comes from public JSON APIs (marked `API_AUTH: false` in the site's own bundles), called with the site's own query strings:

   | What | Endpoint | Finding |
   |---|---|---|
   | Bills (both Houses) | `sansad.in/api_rs/legislation/getBills?…billName=…&page=&size=` | 13 rows for "Arbitration" (duplicate rows per status reached → 11 bills). Per bill: number, house, dates (introduced / passed LS / passed RS / assent), status, act no., and files: **as introduced, as passed by Lok Sabha, as passed by both Houses**, errata, committee report, Gazette copy. "As passed by LS" texts (e.g. `252C_2015`) are new primary evidence of changes made during passage. Status field can lag (2019 Bill "Passed" though assented: assent date + act no. present) |
   | Original 1996 Act's Bill | same | **The Arbitration and Conciliation Bill, 1995** (RS Bill XXX, introduced 16 May 1995; passed RS 22 Jul 1996, LS 2 Aug 1996; assent 16 Aug 1996). As-introduced PDF is a 43-page **scan** (OCR conf 0.51–0.81). Bill as introduced ≠ Act as passed: a primary cross-check for the base text, not a substitute |
   | 2003 amendment attempt | same | Arbitration and Conciliation (Amendment) Bill, 2003 (RS Bill LXXV), **Withdrawn**; no files. Context for intent (Law Commission 176th Report) |
   | Lists of Business | `sansad.in/api_ls/business/listOfBusinessUrls?lobDay=&lobMonth=&lobYear=` | Per sitting day: List of Business (`N`) and Revised List (`R`) links |
   | LS session calendar | `sansad.in/ls/business/agenda` page data (`lokSabhaAndSession`) | 13th–18th Lok Sabha, every session's periods (Budget sessions in parts) and sitting dates |
   | RS debates | `sansad.in/api_rs/debate/date-wise-debate?sessionNo=&date=dd/mm/yyyy` | Per day: Floor (as spoken), English, Hindi, Written Answers PDFs on `cms.rajyasabha.nic.in` (not yet fetched; robots unchecked) |
   | **LS debates** | **`elibrary.sansad.in`** (DSpace 7 REST: `/server/api/discover/search/objects`, `/server/api/core/items/<uuid>/bundles`) | **eParlib is reachable at its new address** (old `eparlib.sansad.in` still unreachable). Debates indexed per discussion with date, LS no., session, members, debate type. Found the 2015 debate (16 Dec 2015, Statutory Resolution + Bill) and 2021 (introduction 4 Feb, combined discussion 12 Feb). Each item: ORIGINAL PDF + server-extracted TEXT |

   → Debate PDFs are HTML printed to PDF: born-digital, but every word sits on a jittered baseline (±1.5pt), so PyMuPDF reported one line per word and set the superscript flag on ordinary words ("2015" → false footnote marker). **Fixed in extractor v4 (3d-iv):** same-row fragments that start with a space are joined; footnote markers also require a size ≤ 85% of the page's median text size. Leftover HTML fragment at page top (`an>`) not yet removed.
3. **Judgments as genealogy events.** `s87` shows that courts change the operative law. The data model needs an event type for judicial strike-down. This is a schema decision for step 3.
4. **Commencement vs enactment.** Provisions enacted but not notified need a status on clauses and operations.
