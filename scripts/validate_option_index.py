from __future__ import annotations

from src.option_index import INDEX_PATH, completeness_report, metadata, validate_index


def main() -> None:
    validate_index(INDEX_PATH)
    meta = metadata(INDEX_PATH)
    report = completeness_report(INDEX_PATH)
    print(f"Validated option index: {INDEX_PATH}")
    print(f"schema_version={meta.get('schema_version')}")
    print(f"source_period_start={meta.get('source_period_start')}")
    print(f"source_period_end={meta.get('source_period_end')}")
    print(f"generated_at={meta.get('generated_at')}")
    print(f"total_agencies_indexed={report.get('total_agencies_indexed')}")
    print(f"total_agency_components={report.get('total_agency_components')}")
    print(f"total_agency_component_naics_mappings={report.get('total_agency_component_naics_mappings')}")
    print(f"total_set_aside_mappings={report.get('total_set_aside_mappings')}")
    print(f"total_performance_location_mappings={report.get('total_performance_location_mappings')}")


if __name__ == "__main__":
    main()
