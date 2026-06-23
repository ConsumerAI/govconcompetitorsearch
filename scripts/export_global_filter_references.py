"""Export static NAICS and location reference files for global filter dropdowns."""
from __future__ import annotations

import json
from pathlib import Path

from src.constants import COUNTRY_NAMES, STATE_OPTIONS
from src.option_index import INDEX_PATH

ROOT = Path(__file__).resolve().parents[1]
NAICS_PATH = ROOT / "data" / "naics_reference.json"
LOCATION_PATH = ROOT / "data" / "location_reference.json"


def main() -> None:
    import sqlite3

    conn = sqlite3.connect(INDEX_PATH)
    naics_rows = conn.execute(
        """
        SELECT naics_code, MAX(naics_description) AS naics_description
        FROM naics_options
        WHERE naics_code <> ''
        GROUP BY naics_code
        ORDER BY naics_code
        """
    ).fetchall()
    naics = [{"code": row[0], "description": row[1] or row[0]} for row in naics_rows]
    NAICS_PATH.write_text(json.dumps(naics, indent=2, sort_keys=False), encoding="utf-8")
    print(f"Wrote {len(naics)} NAICS codes to {NAICS_PATH}")

    locations: list[dict] = []
    for code, name in sorted(STATE_OPTIONS.items()):
        locations.append({"code": code, "label": f"{code} - {name}", "kind": "state"})
    for code, name in sorted(COUNTRY_NAMES.items()):
        locations.append({"code": code, "label": f"{code} - {name}", "kind": "country"})
    for row in conn.execute(
        """
        SELECT DISTINCT performance_country
        FROM option_sources
        WHERE performance_country <> ''
        ORDER BY performance_country
        """
    ).fetchall():
        code = str(row[0]).upper()
        if code in STATE_OPTIONS or code in COUNTRY_NAMES:
            continue
        locations.append({"code": code, "label": f"{code} - {code}", "kind": "country"})
    LOCATION_PATH.write_text(json.dumps(locations, indent=2, sort_keys=False), encoding="utf-8")
    print(f"Wrote {len(locations)} locations to {LOCATION_PATH}")


if __name__ == "__main__":
    main()
