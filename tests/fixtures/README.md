# Test fixtures

Real documents, fetched through `ingest/cache.py` (PRS, `Crawl-delay: 10` respected;
e-Gazette, Government of India publications).
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

The 2019 files were fetched on 2026-10-20.
