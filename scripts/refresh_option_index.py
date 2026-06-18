from __future__ import annotations

from src.option_index import BUILD_PROGRESS_PATH, INDEX_PATH, MAJOR_REQUIRED_AGENCIES, completeness_report, metadata, refresh_index_atomically, validate_index


def main() -> None:
    print(f"Build progress will be written to: {BUILD_PROGRESS_PATH}", flush=True)
    print("Monitor with: python -m scripts.show_build_progress", flush=True)
    path = refresh_index_atomically(INDEX_PATH)
    validate_index(path)
    meta = metadata(path)
    report = completeness_report(path)
    print(f"Refreshed option index: {path}", flush=True)
    print(f"schema_version={meta.get('schema_version')}")
    print(f"source_period_start={meta.get('source_period_start')}")
    print(f"source_period_end={meta.get('source_period_end')}")
    print(f"generated_at={meta.get('generated_at')}")
    print(f"row_counts={meta.get('row_counts')}")
    print(f"total_top_tier_agencies_returned={report.get('total_top_tier_agencies_returned')}")
    print(f"total_agencies_indexed={report.get('total_agencies_indexed')}")
    print(f"total_agencies_excluded={report.get('total_agencies_excluded')}")
    print(f"excluded_agencies={report.get('excluded_agencies')}")
    print(f"total_agency_components={report.get('total_agency_components')}")
    print(f"total_agency_component_naics_mappings={report.get('total_agency_component_naics_mappings')}")
    print(f"total_set_aside_mappings={report.get('total_set_aside_mappings')}")
    print(f"total_performance_location_mappings={report.get('total_performance_location_mappings')}")
    print(f"agencies_with_zero_components={report.get('agencies_with_zero_components')}")
    print(f"components_with_zero_naics_mappings={report.get('components_with_zero_naics_mappings')}")
    component_counts = report.get("component_counts") or {}
    for agency in MAJOR_REQUIRED_AGENCIES:
        print(f"component_count[{agency}]={component_counts.get(agency, 0)}")


if __name__ == "__main__":
    main()
