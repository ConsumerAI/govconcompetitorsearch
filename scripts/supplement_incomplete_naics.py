from __future__ import annotations

import json

from src.option_index import (
    BUILD_PROGRESS_PATH,
    INDEX_PATH,
    completeness_report,
    metadata,
    supplement_incomplete_naics_index,
    validate_index,
)


def main() -> None:
    print(f"Build progress will be written to: {BUILD_PROGRESS_PATH}", flush=True)
    print("Monitor with: python -m scripts.show_build_progress", flush=True)
    diagnostics = supplement_incomplete_naics_index(INDEX_PATH)
    validate_index(INDEX_PATH)
    meta = metadata(INDEX_PATH)
    report = completeness_report(INDEX_PATH)
    print("Incomplete NAICS supplement finished", flush=True)
    print(f"components_targeted={diagnostics['components_targeted']}")
    print(f"components_supplemented={len(diagnostics['components_supplemented'])}")
    print(f"components_still_empty={len(diagnostics['components_still_empty'])}")
    print(f"components_unchanged={len(diagnostics.get('components_unchanged', []))}")
    print(f"naics_rows_added={diagnostics['naics_rows_added']}")
    if diagnostics["components_supplemented"]:
        print("supplemented:")
        for item in diagnostics["components_supplemented"][:25]:
            print(
                f"  {item['agency_name']} / {item['component_name']}: "
                f"+{item['naics_added']} NAICS ({item.get('naics_total', '?')} total)"
            )
        if len(diagnostics["components_supplemented"]) > 25:
            print(f"  ... and {len(diagnostics['components_supplemented']) - 25} more")
    if diagnostics["naics_source_errors"]:
        print(f"remaining_naics_source_errors={len(diagnostics['naics_source_errors'])}")
    print(f"generated_at={meta.get('generated_at')}")
    print(f"refresh_status={meta.get('refresh_status')}")
    print(f"naics_options={json.loads(meta.get('row_counts', '{}')).get('naics_options')}")
    usgs = [
        row
        for row in (report.get("components_with_zero_naics_mappings") or [])
        if "Geological" in row.get("component_name", "")
    ]
    print(f"usgs_still_zero_naics={bool(usgs)}")


if __name__ == "__main__":
    main()
