from __future__ import annotations

import json

from src.option_index import BUILD_PROGRESS_PATH, INDEX_PATH, completeness_report, metadata, supplement_zero_naics_index, validate_index


def main() -> None:
    print(f"Build progress will be written to: {BUILD_PROGRESS_PATH}", flush=True)
    print("Monitor with: python -m scripts.show_build_progress", flush=True)
    diagnostics = supplement_zero_naics_index(INDEX_PATH)
    validate_index(INDEX_PATH)
    meta = metadata(INDEX_PATH)
    report = completeness_report(INDEX_PATH)
    print("Zero-NAICS supplement finished", flush=True)
    print(f"components_targeted={diagnostics['components_targeted']}")
    print(f"components_supplemented={len(diagnostics['components_supplemented'])}")
    print(f"components_still_empty={len(diagnostics['components_still_empty'])}")
    print(f"naics_rows_added={diagnostics['naics_rows_added']}")
    if diagnostics["components_supplemented"]:
        print("supplemented:")
        for item in diagnostics["components_supplemented"]:
            print(f"  {item['agency_name']} / {item['component_name']}: {item['naics_added']} NAICS")
    if diagnostics["components_still_empty"]:
        print("still_empty:")
        for item in diagnostics["components_still_empty"]:
            print(f"  {item['agency_name']} / {item['component_name']}")
    if diagnostics["naics_source_errors"]:
        print(f"naics_source_errors={json.dumps(diagnostics['naics_source_errors'], indent=2)}")
    print(f"generated_at={meta.get('generated_at')}")
    print(f"refresh_status={meta.get('refresh_status')}")
    print(f"naics_options={json.loads(meta.get('row_counts', '{}')).get('naics_options')}")
    print(f"components_with_zero_naics_mappings={len(report.get('components_with_zero_naics_mappings') or [])}")


if __name__ == "__main__":
    main()
