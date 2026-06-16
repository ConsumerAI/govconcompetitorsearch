# Implementation Audit

## Parent Application Inventory

The parent application is a feature-rich Streamlit dashboard centered in a large `app.py` plus an extraction package and `solicitation_workflow.py`. It mixes live USAspending API access, solicitation extraction, filter mapping, broad spending analysis, diagnostics, downloads, and UI rendering in one runtime module.

## Retained Functionality

| Parent functionality | Source pattern | Transitive imports | External dependencies | State/cache/filesystem dependency | Decision |
|---|---|---|---|---|---|
| Money, count, date, option formatting | `format_money`, `format_full_money`, `decode_option`, `first_present`, `parse_action_date` | stdlib, pandas for date normalization | pandas | none | Rewritten as small pure helpers in `src/utils.py`. |
| USAspending top-tier agencies | `fetch_toptier_agencies` | requests | requests | Streamlit cache in parent | Rewritten in `src/usaspending.py`; app caches returned data only. |
| USAspending subagency lookup | `fetch_subagencies` | requests | requests | Streamlit cache in parent | Rewritten for default agency component options. |
| USAspending transaction search | `build_transaction_payload`, `fetch_transaction_page`, `fetch_transaction_pages` | requests, pandas | requests, pandas | parent cache and UI progress | Rewritten to return data only, no UI mutation. |
| Transaction normalization | `normalize_transaction_response` and row helpers | pandas, stdlib | pandas | none | Rewritten with only fields needed by competitor search. |
| Place-of-performance parsing | `transaction_pop_location_parts`, filter helpers | stdlib | none | none | Rewritten for state and country options. |
| Award links | `usaspending_award_url`, `award_drilldown_dataframe` | urllib, pandas | pandas | none | Rewritten and preserved for award verification links. |
| Market concentration | `calculate_market_concentration`, `market_concentration_summary` | pandas | pandas | none | Rewritten preserving positive-obligation denominator and uncapped math. |
| Contractor grouping | Parent groups by normalized contractor display name in transaction-derived tables | pandas | pandas | none | Rewritten as canonical contractor key/name normalization without expanding scope. |
| Contractor detail drilldown | Parent had transaction-derived award drilldowns | pandas | pandas | session-selected contractor | Rewritten as a functional drawer/table using analyzed rows only. |

## Omitted Functionality

| Feature | Reason |
|---|---|
| Solicitation package upload and scope review | Excluded by product reset. |
| AI extraction and mapping | Excluded; also required OpenAI and extraction modules at import time. |
| PDF/OCR/document parsing | Extraction-only. |
| Prior run recovery and resolved signal loading | Violates clean state goal. |
| PSC, contract type, contracting office, separate funding office controls | Excluded from normal competitor workflow. |
| Trends, lane mix, negative obligation cards, summary downloads | Not core to competitor lookup. |
| Current award value and ceiling leaderboard modes | Excluded; net obligations are the ranking basis. |
| Broad diagnostics and developer tools | Not part of standalone product. |

## Minimal Standalone Architecture

`app.py` is a thin Streamlit entrypoint. Runtime code lives under `src/`:

- `src/constants.py`: default labels, USAspending constants, state/country helper data.
- `src/utils.py`: small pure parsing and formatting helpers.
- `src/agency_components.py`: explicit agency component registry and option builder.
- `src/usaspending.py`: USAspending HTTP payloads and data fetchers; cached functions return data only.
- `src/analysis.py`: transaction normalization, filtering, competitor leaderboard, market concentration, awards, and contractor detail rows.
- `src/state.py`: pending/analyzed snapshot helpers with no global visible state.
- `src/ui.py`: focused Streamlit UI for the competitor finder.
- `tests/`: unit and boundary tests, including stale-state isolation and physical path guards.

## Dependency Plan

Retained dependencies:

- `streamlit`: application UI.
- `pandas`: table normalization and grouped calculations.
- `requests`: USAspending HTTP calls.

Removed dependencies:

- OpenAI client packages.
- PDF, OCR, Word, spreadsheet, and document parsing packages.
- Plotly and chart-only packages.
- dotenv and parent environment loading.

## Runtime Boundary

The standalone app does not import parent modules, load parent data, read parent environment files, use parent caches, modify Python import paths, or rely on any absolute parent path. All source code derives resources from the standalone project root only.

