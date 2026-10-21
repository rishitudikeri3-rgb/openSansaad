# Test fixtures

Real documents, fetched through `ingest/cache.py` (PRS, `Crawl-delay: 10` respected;
e-Gazette, sansad.in and elibrary.sansad.in: Government of India / Parliament publications).
Kept small and used only to test extraction. The PRS bill-tracker page is PRS's own work;
review fixture licensing before making the repository public.

| File | Source URL | Original sha256 | Notes |
|---|---|---|---|
| `prs_arbitration_bill_2019.pdf` | https://prsindia.org/files/bills_acts/bills_parliament/2019/THE%20ARBITRATION%20AND%20CONCILIATION%20%28AMENDMENT%29%20BILL%2C%202019.pdf | `4a5279e8bb18aa87ddc6ddb67ffc2e9a411c23f65f23d974ff65094d979b2f24` | Unmodified. Bill as introduced in Rajya Sabha; margin notes and margin line numbers; English only. |
| `gazette_arbitration_act_2019_p1-3.pdf` | https://prsindia.org/files/bills_acts/bills_parliament/2019/Arbitration%20and%20Conciliation%20%28Amendment%29%20Act%2C%202019.pdf | `c3d5d211026976159a36d1ea5134fc45e41b4e151c153c5c0f8ec4d2bc65ddeb` | **Pages 1–3 only** (trimmed with PyMuPDF to keep the repo small). Gazette copy of Act 33 of 2019: legacy-font Hindi masthead, alternating running headers, margin notes. |
| `prs_billtrack_arbitration_2019.html` | https://prsindia.org/billtrack/the-arbitration-and-conciliation-amendment-bill-2019 | `921b37ff350d9792e860b1e6814370696422d7835444a15ff6966dc9e82ff855` | Unmodified PRS bill-tracker page. |
| `prs_billtrack_arbitration_2015.html` | https://prsindia.org/billtrack/the-arbitration-and-conciliation-amendment-bill-2015 | `02281a8ff2d7a4cbc3452fd7577a0c3c9b7b00ee9209ef219c1625a7105b740b` | Unmodified, fetched 2026-10-21. Hindi bill link; Act listed under "External Links". |
| `prs_billtrack_arbitration_2018.html` | https://prsindia.org/billtrack/the-arbitration-and-conciliation-amendment-bill-2018 | `71b100367d3e15952fbc1070cfa7d3c8d47b29d2d56bf365330ee10b2d6f53da` | Unmodified, fetched 2026-10-21. Lapsed bill (passed LS only, no Act). |
| `egazette_act3_2021_p1.pdf` | https://egazette.gov.in/WriteReadData/2021/225832.pdf | `00cfc054f4e6a32ec65a29e13807694bbdf9fe140848bf3b4e8838617f2b79c9` | **Page 1 only** (trimmed with PyMuPDF). Act 3 of 2021; masthead with Gazette ID. Fetched 2026-10-21. |
| `egazette_ordinance14_2020_p1.pdf` | https://egazette.gov.in/WriteReadData/2020/222941.pdf | `d11e86f0960f975d3ffa93c1e26645475b531cbfe66f3dab3300b41c90d03666` | **Page 1 only** (trimmed). Ordinance 14 of 2020. Fetched 2026-10-21. |
| `egazette_corrigendum_ord14_2020.pdf` | https://egazette.gov.in/WriteReadData/2020/222961.pdf | `8a30d0580708e768ebb98c77ecb8f2bd9ac2e56c709297a75a9aa33680726da3` | Unmodified, fetched 2026-10-21. Text masthead, body pasted in as an image (image-region OCR). |
| `egazette_so3154e_2019.pdf` | https://egazette.gov.in/WriteReadData/2019/211902.pdf | `76a935fd9a36c9f2100aaea11159b31e2e3a06fb4966e68c36585337ca0639f4` | Unmodified, fetched 2026-10-21. S.O. 3154(E): Hindi cover page, English on page 2; commences only some sections of Act 33 of 2019. |
| `sansad_getbills_arbitration.json` | https://sansad.in/api_rs/legislation/getBills?loksabha=&sessionNo=&billName=Arbitration&house=&ministryName=&billType=&billCategory=&billStatus=&introductionDateFrom=&introductionDateTo=&passedInLsDateFrom=&passedInLsDateTo=&passedInRsDateFrom=&passedInRsDateTo=&page=1&size=50&locale=en&sortOn=billIntroducedDate&sortBy=desc | `483dd7fddb6a001d1a59f92badff8d37c48c37f17f4c83920c6a61fc2c3cb2b1` | Unmodified, fetched 2026-10-21. 13 rows incl. duplicates per status. |
| `sansad_lob_2021-02-12.json` | https://sansad.in/api_ls/business/listOfBusinessUrls?lobDay=12&lobMonth=2&lobYear=2021&locale=en | `960f0a125f952c87521473e4a6061e159232c9a7e3d57a5527f5817937a9e0e7` | Unmodified, fetched 2026-10-21. |
| `sansad_rs_datewise_249_2019-07-18.json` | https://sansad.in/api_rs/debate/date-wise-debate?sessionNo=249&date=18/07/2019 | `930ba160c5b048d0d90db3b087ae4ce289e6eda809a14b8158a29789f85a9f0c` | Unmodified, fetched 2026-10-21. |
| `sansad_ls_sessions.json` | https://sansad.in/ls/business/agenda | `8c0c1d5869ece2679b959f92191e4967ab2f3611d444a5f8eb00e901ddf65d9e` | **Extracted**: the `props.pageProps.lokSabhaAndSession` value of the page's `__NEXT_DATA__` (page itself 320 KB), re-serialized as JSON. Fetched 2026-10-21. |
| `elibrary_search_arbitration_bill.json` | https://elibrary.sansad.in/server/api/discover/search/objects?query=%22Arbitration%20and%20Conciliation%20%28Amendment%29%20Bill%22&scope=03c67f26-e547-4c6f-914d-e4b443281a8c&page=0&size=10 | `7ab6ca3664c128022189c12de5a36130ddd92960b1b09a30579a88c3a8e8c768` | Unmodified, fetched 2026-10-21. First page of 29 hits. |
| `elibrary_bundles_67076016.json` | https://elibrary.sansad.in/server/api/core/items/67076016-fcc0-400e-93d5-9bbbb8e87cba/bundles?embed=bitstreams | `380bc77609b65d8dbffb2bee848977bcc91dad078c8a5c492e7b3668a11b12b4` | Unmodified, fetched 2026-10-21. |
| `eparlib_debate_2015-12-16_p1.pdf` | https://elibrary.sansad.in/server/api/core/bitstreams/c7efc397-699e-4af7-8482-3b27e93ded2f/content | `198a783f64d47d96db4ecefa31c9474a9ec308643165bf50d21ad6692e811d00` | **Page 1 only** (trimmed). LS debate 16 Dec 2015 on the Statutory Resolution and the 2015 Bill; HTML printed to PDF, jittered baselines. Fetched 2026-10-21. |

The 2019 files were fetched on 2026-10-20.
