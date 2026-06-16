from __future__ import annotations

import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.constants import ALL_COMPONENTS, ALL_LOCATIONS, ALL_NAICS, ALL_SET_ASIDES
from src.option_index import (
    INDEX_PATH,
    build_index_file,
    clear_process_cache,
    component_option_values,
    completeness_report,
    COMPONENT_FIXTURES,
    MAJOR_REQUIRED_AGENCIES,
    get_component_options,
    get_agency_options,
    get_naics_options,
    get_set_aside_options,
    location_option_values,
    metadata,
    naics_option_values,
    refresh_index_atomically,
    set_aside_option_values,
    validate_index,
)
from src.state import FilterSnapshot
from src.usaspending import fetch_transactions_uncached, query_fingerprint


def broad_test_index_data():
    agencies = [{"agency_name": agency, "toptier_code": str(index + 1), "abbreviation": ""} for index, agency in enumerate(MAJOR_REQUIRED_AGENCIES)]
    for index in range(25):
        agencies.append({"agency_name": f"Independent Test Agency {index}", "toptier_code": f"T{index}", "abbreviation": ""})
    component_rows = []
    rows = []
    for agency in MAJOR_REQUIRED_AGENCIES:
        fixture = next((item for item in COMPONENT_FIXTURES if item[0] == agency), None)
        component_name = fixture[1] if fixture else f"{agency} Component"
        dimension = "funding_office" if agency == "Department of State" else "awarding_subagency"
        component_rows.append(
            {
                "agency_name": agency,
                "component_dimension_type": dimension,
                "component_code": "",
                "component_name": component_name,
            }
        )
        rows.append(
            {
                "agency_name": agency,
                "component_dimension_type": dimension,
                "component_code": "",
                "component_name": component_name,
                "naics_code": "541611",
                "naics_description": "Administrative Management and General Management Consulting Services",
                "set_aside_code": "",
                "set_aside_description": "",
                "performance_country": "",
                "performance_state": "",
                "support_awarding_agency_name": agency,
                "support_funding_agency_name": agency if dimension == "funding_office" else "",
            }
        )
    for agency, component in COMPONENT_FIXTURES:
        if not any(row["agency_name"] == agency and row["component_name"] == component for row in component_rows):
            dimension = "funding_office" if agency == "Department of State" else "awarding_subagency"
            component_rows.append({"agency_name": agency, "component_dimension_type": dimension, "component_code": "", "component_name": component})
            rows.append(
                {
                    "agency_name": agency,
                    "component_dimension_type": dimension,
                    "component_code": "",
                    "component_name": component,
                    "naics_code": "541611",
                    "naics_description": "Administrative Management and General Management Consulting Services",
                    "set_aside_code": "",
                    "set_aside_description": "",
                    "performance_country": "",
                    "performance_state": "",
                    "support_awarding_agency_name": agency,
                    "support_funding_agency_name": agency if dimension == "funding_office" else "",
                }
            )
    report = {
        "total_top_tier_agencies_returned": len(agencies),
        "total_agencies_indexed": len(agencies),
        "total_agencies_excluded": 0,
        "excluded_agencies": [],
        "total_agency_components": len(component_rows),
        "total_agency_component_naics_mappings": len(rows),
        "total_set_aside_mappings": 0,
        "total_performance_location_mappings": 0,
        "agencies_with_zero_components": [],
        "components_with_zero_naics_mappings": [],
        "component_counts": {agency["agency_name"]: 1 for agency in agencies},
        "source_errors": {"component": {}, "naics": {}},
    }
    return agencies, component_rows, rows, report


class OptionIndexLookupTests(unittest.TestCase):
    def setUp(self):
        clear_process_cache()
        validate_index()

    def assertFast(self, func, *args):
        started = time.perf_counter()
        result = func(*args)
        elapsed_ms = (time.perf_counter() - started) * 1000
        self.assertLess(elapsed_ms, 250)
        return result

    def test_treasury_component_lookup_uses_local_index(self):
        with patch("src.usaspending.fetch_transaction_download_rows") as mocked_download:
            options, diag = self.assertFast(component_option_values, "Department of the Treasury")
        self.assertFalse(mocked_download.called)
        self.assertIn("Internal Revenue Service", options)
        self.assertEqual(diag["lookup_type"], "Agency Component")

    def test_interior_component_lookup_uses_local_index(self):
        with patch("src.usaspending.fetch_transaction_download_rows") as mocked_download:
            options, _diag = self.assertFast(component_option_values, "Department of the Interior")
        self.assertFalse(mocked_download.called)
        self.assertIn("Bureau of Reclamation", options)

    def test_state_component_lookup_uses_local_index(self):
        with patch("src.usaspending.fetch_transaction_download_rows") as mocked_download:
            options, _diag = self.assertFast(component_option_values, "Department of State")
        self.assertFalse(mocked_download.called)
        self.assertIn("BUREAU OF INTERNATIONAL NARCOTICS AND LAW ENFORCEMENT AFFAIRS", options)

    def test_agency_selection_does_not_call_transaction_download(self):
        with patch("src.usaspending.fetch_transaction_download_rows") as mocked_download:
            component_option_values("Department of the Treasury")
            component_option_values("Department of the Interior")
            component_option_values("Department of State")
            naics_option_values("Department of the Interior", "Bureau of Reclamation")
            naics_option_values(
                "Department of State",
                "BUREAU OF INTERNATIONAL NARCOTICS AND LAW ENFORCEMENT AFFAIRS",
            )
        self.assertFalse(mocked_download.called)

    def test_treasury_options_do_not_contain_other_agency_components(self):
        values = [row["component_name"] for row in get_component_options("Department of the Treasury")]
        self.assertNotIn("Bureau of Reclamation", values)
        self.assertNotIn("Air Force", values)
        self.assertNotIn("BUREAU OF INTERNATIONAL NARCOTICS AND LAW ENFORCEMENT AFFAIRS", values)

    def test_naics_options_are_scoped_by_agency_and_component(self):
        interior = [row["naics_code"] for row in get_naics_options("Department of the Interior", "Bureau of Reclamation")]
        treasury = [row["naics_code"] for row in get_naics_options("Department of the Treasury", "Internal Revenue Service")]
        self.assertIn("561210", interior)
        self.assertIn("237990", interior)
        self.assertNotIn("237990", treasury)
        self.assertIn("541512", treasury)

    def test_naics_lookup_is_fast_and_local(self):
        with patch("src.usaspending.fetch_transaction_download_rows") as mocked_download:
            options, diag = self.assertFast(naics_option_values, "Department of the Interior", "Bureau of Reclamation")
        self.assertFalse(mocked_download.called)
        self.assertIn("561210||Facilities Support Services", options)
        self.assertEqual(diag["lookup_type"], "NAICS")

    def test_optional_lookup_is_fast_and_scoped(self):
        set_asides = self.assertFast(get_set_aside_options, "Department of State", "BUREAU OF INTERNATIONAL NARCOTICS AND LAW ENFORCEMENT AFFAIRS", "541611")
        locations, _diag = self.assertFast(
            location_option_values,
            "Department of State",
            "BUREAU OF INTERNATIONAL NARCOTICS AND LAW ENFORCEMENT AFFAIRS",
            "541611",
            "WOSB",
        )
        self.assertEqual(set_asides[0]["set_aside_code"], "WOSB")
        self.assertIn("IRQ - Iraq", locations)

    def test_set_aside_option_values_use_scoped_live_api_when_index_is_empty(self):
        with patch("src.option_index.fetch_scoped_set_aside_options") as mock_fetch:
            mock_fetch.return_value = (["All Set-Aside Types", "SBA - Small Business Set-Aside"], {"cache_level_used": "live_api"})
            options, diag = set_aside_option_values("Department of Defense", "Department of the Army", "541611")
        mock_fetch.assert_called_once()
        self.assertEqual(options[1], "SBA - Small Business Set-Aside")
        self.assertEqual(diag["cache_level_used"], "live_api")

    def test_location_option_values_use_scoped_live_api_when_index_is_empty(self):
        with patch("src.option_index.fetch_scoped_location_options") as mock_fetch:
            mock_fetch.return_value = (["All Locations", "VA - Virginia"], {"cache_level_used": "live_api"})
            options, diag = location_option_values("Department of Defense", "Department of the Army", "541611", "")
        mock_fetch.assert_called_once()
        self.assertIn("VA - Virginia", options)
        self.assertEqual(diag["cache_level_used"], "live_api")

    def test_index_metadata_is_validated(self):
        meta = metadata()
        self.assertEqual(meta["schema_version"], "2")
        self.assertEqual(meta["source_period_start"], "2020-10-01")
        self.assertIn("row_counts", meta)

    def test_agency_dropdown_contains_substantially_more_than_fixtures(self):
        agencies = [row["agency_name"] for row in get_agency_options()]
        self.assertGreater(len(agencies), 40)
        self.assertGreater(len(agencies), 5)

    def test_agency_dropdown_source_is_indexed_agency_table(self):
        agencies = get_agency_options()
        report = completeness_report()
        self.assertEqual(len(agencies), report["total_agencies_indexed"])

    def test_fixture_agencies_do_not_define_universe(self):
        agencies = {row["agency_name"] for row in get_agency_options()}
        fixture_agencies = {agency for agency, _component in COMPONENT_FIXTURES}
        self.assertGreater(len(agencies - fixture_agencies), 20)

    def test_major_agencies_appear_in_dropdown(self):
        agencies = {row["agency_name"] for row in get_agency_options()}
        for agency in [
            "Department of Labor",
            "Department of Health and Human Services",
            "Department of Homeland Security",
            "Department of Veterans Affairs",
            "Department of Agriculture",
            "Department of Justice",
            "National Aeronautics and Space Administration",
            "Department of the Treasury",
            "Department of State",
            "Department of the Interior",
            "Department of Defense",
            "General Services Administration",
        ]:
            self.assertIn(agency, agencies)

    def test_every_agency_option_has_an_index_record(self):
        agencies = {row["agency_name"] for row in get_agency_options()}
        self.assertEqual(len(agencies), len(get_agency_options()))

    def test_every_component_belongs_to_indexed_agency(self):
        agencies = {row["agency_name"] for row in get_agency_options()}
        for agency in agencies:
            for component in get_component_options(agency):
                self.assertIn(component["agency_name"], agencies)

    def test_every_naics_mapping_has_supporting_source_rows(self):
        validate_index()

    def test_date_changes_do_not_change_index_lookup_scope(self):
        first = query_fingerprint(
            FilterSnapshot(agency="Department of the Interior", start_date="2020-06-15", end_date="2026-06-15"),
            option_category="naics",
        )
        second = query_fingerprint(
            FilterSnapshot(agency="Department of the Interior", start_date="2025-10-01", end_date="2026-06-15"),
            option_category="naics",
        )
        self.assertEqual(first, second)


class OptionIndexRefreshTests(unittest.TestCase):
    def test_refresh_uses_temporary_output_and_atomic_replacement(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "option_index.sqlite"
            agencies, component_rows, rows, report = broad_test_index_data()
            with patch("src.option_index.collect_option_index_data", return_value=(agencies, component_rows, rows, report)):
                path = refresh_index_atomically(target)
            self.assertEqual(path, target)
            validate_index(target)
            leftovers = list(Path(tmpdir).glob("option_index_*.sqlite"))
            self.assertEqual(leftovers, [])

    def test_failed_refresh_preserves_old_index(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "option_index.sqlite"
            agencies, component_rows, rows, report = broad_test_index_data()
            build_index_file(target, agencies=agencies, component_rows=component_rows, rows=rows, report=report)
            old_bytes = target.read_bytes()
            with patch("src.option_index.build_index_file", side_effect=RuntimeError("boom")):
                with self.assertRaises(RuntimeError):
                    refresh_index_atomically(target)
            self.assertEqual(target.read_bytes(), old_bytes)
            validate_index(target)

    def test_external_copy_contains_index_and_imports(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            copy_root = Path(tmpdir) / "copy"
            ignore = shutil.ignore_patterns(".venv", "__pycache__", "*.pyc")
            shutil.copytree(Path(__file__).resolve().parents[1], copy_root, ignore=ignore)
            copied_index = copy_root / "data" / "option_index.sqlite"
            self.assertTrue(copied_index.exists())
            validate_index(copied_index)


class FinalAnalysisPathTests(unittest.TestCase):
    def test_final_analysis_still_uses_uncapped_segmented_downloads(self):
        calls = []

        def fake_download(snapshot):
            calls.append(snapshot)
            return (
                [
                    {
                        "contract_award_unique_key": f"K{len(calls)}",
                        "award_id_piid": f"P{len(calls)}",
                        "transaction_number": str(len(calls)),
                        "action_date": snapshot.start_date,
                        "federal_action_obligation": "10",
                        "recipient_name": "A",
                        "awarding_agency_name": "Department of State",
                    }
                ],
                {
                    "payload": {"filters": {"time_period": [{"start_date": snapshot.start_date, "end_date": snapshot.end_date}]}},
                    "diagnostics": {"api_reported_total_rows": 1, "limit_reached": False, "truncation_detected": False},
                },
            )

        with patch("src.usaspending.fetch_transaction_download_rows", side_effect=fake_download):
            rows, diag = fetch_transactions_uncached(
                "Department of State",
                ALL_COMPONENTS,
                ALL_NAICS,
                "All Set-Aside Types",
                "All Locations",
                "2025-09-30",
                "2025-10-01",
                query_fingerprint(FilterSnapshot(agency="Department of State", start_date="2025-09-30", end_date="2025-10-01")) + "-final-path",
                max_pages=0,
            )
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(rows), 2)
        self.assertFalse(diag["error"])


if __name__ == "__main__":
    unittest.main()
