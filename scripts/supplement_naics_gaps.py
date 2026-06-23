from __future__ import annotations

import json
import sys

from src.option_index import (
    BUILD_PROGRESS_PATH,
    INDEX_PATH,
    completeness_report,
    metadata,
    supplement_naics_gaps_index,
    validate_index,
)


def main() -> None:
    agency = sys.argv[1] if len(sys.argv) > 1 else None
    print(f"Build progress will be written to: {BUILD_PROGRESS_PATH}", flush=True)
    print("Monitor with: python -m scripts.show_build_progress", flush=True)
    if agency:
        print(f"Agency filter: {agency}", flush=True)
    diagnostics = supplement_naics_gaps_index(INDEX_PATH, agency_name=agency)
    validate_index(INDEX_PATH)
    meta = metadata(INDEX_PATH)
    report = completeness_report(INDEX_PATH)
    print("NAICS gap supplement finished", flush=True)
    print(f"components_targeted={diagnostics['components_targeted']}")
    print(f"components_supplemented={len(diagnostics['components_supplemented'])}")
    print(f"naics_rows_added={diagnostics['naics_rows_added']}")
    print(f"naics_options={json.loads(meta.get('row_counts', '{}')).get('naics_options')}")
    if agency:
        import sqlite3

        conn = sqlite3.connect(INDEX_PATH)
        for comp in ("U.S. Special Operations Command",):
            n = conn.execute(
                "SELECT COUNT(*) FROM naics_options WHERE agency_name=? AND component_name=? AND naics_code='541990'",
                (agency, comp),
            ).fetchone()[0]
            print(f"{comp} has 541990: {bool(n)}")


if __name__ == "__main__":
    main()
