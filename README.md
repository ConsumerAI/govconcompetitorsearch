# GovCon Competitor Finder

A focused Streamlit app for identifying contractors already winning in a selected federal market.

## What It Does

Choose an Agency, Agency Component, and NAICS, then optionally refine by Set-Aside or Performance Location. The app returns top competitors, net obligations, market share, top supporting awards, market concentration, and verification links to USAspending.

The app is manually driven and keeps pending selections separate from analyzed results. Changing a filter does not change displayed results until you run a new analysis.

## Run Locally

```powershell
cd path\to\govconcompetitorsearch
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

No Streamlit secrets are required for normal operation. Network access to USAspending is required for live data.

## Option Index

Dropdown option discovery is served from `data/option_index.sqlite`. This compact local index is separate from the final competitor-analysis data path, so changing Agency, Component, NAICS, Set-Aside, or Performance Location does not start a full USAspending transaction download.

The bundled index covers `2020-10-01` through `2026-06-16` and stores:

- Agency component options by agency and component dimension.
- NAICS options by agency and component.
- Scoped set-aside and performance-location refinements where available.
- Metadata including schema version, source period, generation timestamp, row counts, and refresh status.

Refresh the index with:

```powershell
python -m scripts.refresh_option_index
```

The refresh command writes a temporary SQLite database, validates required schema and fixture relationships, then atomically replaces `data/option_index.sqlite`. A failed refresh preserves the prior working index.

## Verification

```powershell
python -m compileall -q .
python -c "import app; print('APP_IMPORT_OK')"
python -m unittest discover -s tests -v
python -m scripts.validate_option_index
python -m streamlit run app.py --server.headless true --server.port 8511
```

## Product Boundary

This clean app contains no solicitation extraction, document upload, AI mapping, prior-run recovery, broad trend workbench, PSC filter, contract-type filter, contracting-office filter, or separate funding-office filter.
