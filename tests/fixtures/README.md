# Test fixtures

Real documents, fetched through `ingest/cache.py` (PRS, `Crawl-delay: 10` respected).
Kept small and used only to test extraction. The PRS bill-tracker page is PRS's own work;
review fixture licensing before making the repository public.

| File | Source URL | Original sha256 | Notes |
|---|---|---|---|
| `prs_arbitration_bill_2019.pdf` | https://prsindia.org/files/bills_acts/bills_parliament/2019/THE%20ARBITRATION%20AND%20CONCILIATION%20%28AMENDMENT%29%20BILL%2C%202019.pdf | `4a5279e8bb18aa87ddc6ddb67ffc2e9a411c23f65f23d974ff65094d979b2f24` | Unmodified. Bill as introduced in Rajya Sabha; margin notes and margin line numbers; English only. |
| `gazette_arbitration_act_2019_p1-3.pdf` | https://prsindia.org/files/bills_acts/bills_parliament/2019/Arbitration%20and%20Conciliation%20%28Amendment%29%20Act%2C%202019.pdf | `c3d5d211026976159a36d1ea5134fc45e41b4e151c153c5c0f8ec4d2bc65ddeb` | **Pages 1–3 only** (trimmed with PyMuPDF to keep the repo small). Gazette copy of Act 33 of 2019: legacy-font Hindi masthead, alternating running headers, margin notes. |
| `prs_billtrack_arbitration_2019.html` | https://prsindia.org/billtrack/the-arbitration-and-conciliation-amendment-bill-2019 | `921b37ff350d9792e860b1e6814370696422d7835444a15ff6966dc9e82ff855` | Unmodified PRS bill-tracker page. |
| `prs_billtrack_arbitration_2015.html` | https://prsindia.org/billtrack/the-arbitration-and-conciliation-amendment-bill-2015 | `02281a8ff2d7a4cbc3452fd7577a0c3c9b7b00ee9209ef219c1625a7105b740b` | Unmodified, fetched 2026-10-21. Hindi bill link; Act listed under "External Links". |
| `prs_billtrack_arbitration_2018.html` | https://prsindia.org/billtrack/the-arbitration-and-conciliation-amendment-bill-2018 | `71b100367d3e15952fbc1070cfa7d3c8d47b29d2d56bf365330ee10b2d6f53da` | Unmodified, fetched 2026-10-21. Lapsed bill (passed LS only, no Act). |

The 2019 files were fetched on 2026-10-20.
