# Live Data Validation

## Scope Under Review

- Selected Agency: Department of State
- Configured component dimension:
  - field code: `funding_office_code`
  - field name: `funding_office_name`
  - dimension type: `funding_office`
- Selected component: `BUREAU OF INTERNATIONAL NARCOTICS`
- Selected NAICS: `541611`

## Functions Involved

- Agency list: `src.usaspending.fetch_toptier_agencies`
- State base transaction dataset: `src.usaspending.fetch_transactions_for_snapshot` -> `fetch_transactions_cached` -> `fetch_transaction_download_rows`
- Department of State component options: `src.ui._load_agency_transactions` -> `src.agency_components.build_agency_component_options`
- Scoped NAICS options: `src.ui._option_sets`; for State this currently derives from downloaded/normalized State rows, then `src.analysis.build_naics_options`
- Final competitor analysis request: `src.ui.main` -> `fetch_transactions_for_snapshot`
- Download request body: `src.usaspending.transaction_download_payload`
- Download polling and CSV parsing: `src.usaspending.fetch_transaction_download_rows`
- Transaction normalization: `src.analysis.normalize_transactions`
- Contractor grouping: `src.analysis.canonical_contractor_name`, `src.analysis.competitor_leaderboard`
- Unique awards: `src.analysis.competitor_leaderboard`, `src.analysis.analyze`, using `contract_award_unique_key`
- Market concentration: `src.analysis.market_concentration`

## Exact Current Request Scope

- Endpoint: `/api/v2/download/transactions/`
- Method: `POST`
- Headers: `Accept: application/json`, `User-Agent: govcon-competitor-finder/1.0`
- Award type codes: `["A", "B", "C", "D"]`
- Award/idv flag: `AWARD`
- Start date: `2025-10-01`
- End date: `2026-09-30`
- Fiscal-year interpretation: FY2026 full fiscal-year range as currently implemented.
- YTD cutoff logic: none in current code; the request uses the full FY end date even though current date is 2026-06-15.
- Agency filter: `{"type": "awarding", "tier": "toptier", "name": "Department of State"}`
- Component filter: not sent to USAspending for Department of State; applied client-side as `funding_office_name == "BUREAU OF INTERNATIONAL NARCOTICS"`.
- NAICS filter for final scope: `{"naics_codes": {"require": ["541611"]}}`
- Current production download limit: `10000`
- Download completion condition: status payload `status == "finished"`
- Files returned in capped diagnostic: one empty subaward CSV and one prime transaction CSV.
- Files loaded in capped diagnostic: one prime transaction CSV.

Current capped download body:

```json
{
  "filters": {
    "agencies": [{"type": "awarding", "tier": "toptier", "name": "Department of State"}],
    "award_type_codes": ["A", "B", "C", "D"],
    "award_or_idv_flag": "AWARD",
    "time_period": [{"start_date": "2025-10-01", "end_date": "2026-09-30"}]
  },
  "columns": [
    "contract_award_unique_key",
    "award_id_piid",
    "modification_number",
    "transaction_number",
    "transaction_description",
    "federal_action_obligation",
    "total_dollars_obligated",
    "current_total_value_of_award",
    "potential_total_value_of_award",
    "action_date",
    "action_type",
    "recipient_name",
    "recipient_uei",
    "naics_code",
    "naics_description",
    "product_or_service_code",
    "product_or_service_code_description",
    "awarding_office_code",
    "awarding_office_name",
    "funding_office_code",
    "funding_office_name"
  ],
  "file_format": "csv",
  "limit": 10000
}
```

## 10,000 Row Finding

The 10,000-row State base load is not proof that the complete State dataset contains exactly 10,000 rows. It is an application-imposed authoritative cap.

- Current capped status payload reported `total_rows: 10000`.
- Current capped raw rows parsed: 10,000.
- Current capped rows after normalization: 10,000.
- Current capped rows after Agency filter: 10,000.
- Search found production limit: `transaction_download_payload(..., limit: int = 10_000)`.
- No `head(10000)` or `[:10000]` production cap was found.

Uncapped diagnostic, same State request with `limit` omitted:

- Status: finished.
- File name: `SubawardsAndPrimeTransactions_2026-06-15_H22M03S18509334.zip`
- API-reported total rows: 20,901.
- Total files generated: 2.
- Files parsed for prime transactions: 1.
- Rows in `Contracts_PrimeTransactions_2026-06-15_H22M03S25_1.csv`: 20,901.
- Total rows before normalization: 20,901.

Defect: authoritative component options and analysis currently use the capped 10,000-row dataset when no narrower filters are applied.

## Current Capped INL Row Counts

Using the current capped State dataset:

- All downloaded transactions: 10,000.
- After award-type filtering: enforced by USAspending request.
- After date filtering: enforced by USAspending request.
- After Department of State Agency filter: 10,000.
- After funding-office filter `BUREAU OF INTERNATIONAL NARCOTICS`: 351.
- After NAICS 541611 filter: 102.
- After duplicate-row handling: no explicit duplicate-row removal is currently performed.
- Final analyzed transaction rows: 102.

Distinct final-row values:

- `awarding_agency_name`: `Department of State`
- `awarding_sub_agency_name`: blank in normalized download rows
- `awarding_office_name`: `ACQUISITIONS - AQM MOMENTUM`, `ACQUISITIONS - INL`, `AMERICAN EMBASSY BOGOTA - NAS`, `AMERICAN EMBASSY MEXICO - NAS`
- `funding_office_name`: `BUREAU OF INTERNATIONAL NARCOTICS`
- `naics_code`: `541611`

Current capped exact calculations:

- Net obligations: `4720671.21`
- Gross positive obligations: `5518530.460000001`
- Gross negative obligations: `-797859.25`
- Transaction count: `102`
- Distinct canonical awards: `79`
- Distinct PIIDs: `79`
- Distinct contractor groups: `12`
- Earliest action date: `2025-11-05`
- Latest action date: `2026-02-23`
- Top five positive obligations: `5491858.34`
- Total positive obligations: `5518530.460000001`
- Market concentration percentage: `99.58916381517986%`

These capped calculations do not match the previously displayed `$10,180,789.15 / 15 contractors / 123 awards / 92.6%`, which was produced by a different filter/order path from the capped State rows. The authoritative result must be recomputed after the cap is removed.

## Current Capped Award Table Sample

First ten award rows under the current capped final rows:

| Award ID | Filtered obligation total | USAspending link |
|---|---:|---|
| 19AQMM25F0987 | 2325585.37 | https://www.usaspending.gov/award/CONT_AWD_19AQMM25F0987_1900_19AQMR24D0003_1900 |
| 191NLE24F7007 | 277753.40 | https://www.usaspending.gov/award/CONT_AWD_191NLE24F7007_1900_191NLE19A0003_1900 |
| 191NLE26F7010 | 244990.40 | https://www.usaspending.gov/award/CONT_AWD_191NLE26F7010_1900_191NLE25A0067_1900 |
| 191NLE26F7012 | 226877.40 | https://www.usaspending.gov/award/CONT_AWD_191NLE26F7012_1900_191NLE25A0069_1900 |
| 191NLE26F7003 | 223143.61 | https://www.usaspending.gov/award/CONT_AWD_191NLE26F7003_1900_191NLE25A0067_1900 |
| 191NLE22F7003 | 219381.68 | https://www.usaspending.gov/award/CONT_AWD_191NLE22F7003_1900_191NLE19A0003_1900 |
| 191NLE26F7014 | 218886.72 | https://www.usaspending.gov/award/CONT_AWD_191NLE26F7014_1900_191NLE25A0068_1900 |
| 191NLE26F7005 | 212246.00 | https://www.usaspending.gov/award/CONT_AWD_191NLE26F7005_1900_191NLE25A0069_1900 |
| 191NLE25F7002 | 156660.00 | https://www.usaspending.gov/award/CONT_AWD_191NLE25F7002_1900_191NLE19A0005_1900 |
| 191NLE26F7016 | 145641.60 | https://www.usaspending.gov/award/CONT_AWD_191NLE26F7016_1900_191NLE25A0068_1900 |

## Component Option Contamination Finding

The suspicious visible State component values are not caused by another Agency's cached options or by `awarding_office_name`. In the capped diagnostic, they occur in `funding_office_name` on rows whose normalized awarding agency is Department of State.

Examples:

| Option label | Source column | Source row count | Awarding agency values represented | Funding agency values represented |
|---|---|---:|---|---|
| AIR FORCE MATERIAL COMMAND | `funding_office_name` | 3 | Department of State | not requested in current download columns |
| 0028 IN HHC 02 HEADQUARTERS IN | `funding_office_name` | 1 | Department of State | not requested in current download columns |
| 0096 CA BN CIVIL AFFAIRS B | `funding_office_name` | 2 | Department of State | not requested in current download columns |
| ACQUISITIONS - AQM MOMENTUM | `funding_office_name` | 42 | Department of State | not requested in current download columns |
| ACQUISITIONS - INL | `funding_office_name` | 83 | Department of State | not requested in current download columns |

Defect: the current download columns do not include `funding_agency_name` or `funding_sub_agency_name`, so the app cannot show whether suspicious `funding_office_name` values are funded by Department of State or by another funding agency. This must be added to diagnostics and normalization.

## Component Option Diagnostic Table

This table reflects the currently visible capped State component dropdown. Every option listed below came from `funding_office_name` on normalized rows where `awarding_agency_name == "Department of State"`.

| Option label | Source column | Source row count | Awarding agency values represented | Funding agency values represented |
|---|---|---:|---|---|
| 0028 IN HHC 02 HEADQUARTERS IN | funding_office_name | 1 | Department of State | not requested |
| 0096 CA BN CIVIL AFFAIRS B | funding_office_name | 2 | Department of State | not requested |
| ACCTG DISB STA NR 387700 | funding_office_name | 1 | Department of State | not requested |
| ACQUISITIONS - AQM MOMENTUM | funding_office_name | 42 | Department of State | not requested |
| ACQUISITIONS - INL | funding_office_name | 83 | Department of State | not requested |
| AIR FORCE MATERIAL COMMAND | funding_office_name | 3 | Department of State | not requested |
| AMERICAN CONSULATE WUHAN | funding_office_name | 1 | Department of State | not requested |
| BUREAU OF INTERNATIONAL NARCOTICS | funding_office_name | 351 | Department of State | not requested |
| BUREAU OF OVERSEAS BUILDINGS OPS | funding_office_name | 1483 | Department of State | not requested |
| BUREAU OF DIPLOMATIC SECURITY | funding_office_name | 1170 | Department of State | not requested |

The full currently visible dropdown contained 203 non-default component options. The source rule is deterministic: `distinct_nonblank(state_rows["funding_office_name"])`.

## Defects To Repair

1. Remove the authoritative `limit: 10000` cap from transaction downloads.
2. Fail closed if the download diagnostics indicate truncation or if parsed prime rows are fewer than API-reported total rows.
3. Include period metadata in request diagnostics, analyzed state, and visible applied-filter scope.
4. Include `funding_agency_name` and `funding_sub_agency_name` in download columns/normalization when available.
5. Expand cache keys to include period, award type codes, component dimension type, component source field, and data query fingerprint.

