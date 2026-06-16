from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

from src.agency_components import build_agency_component_options
from src.analysis import analyze, award_table, filter_transactions, market_concentration, normalize_transactions
from src.constants import ALL_COMPONENTS, ALL_LOCATIONS, ALL_NAICS, ALL_SET_ASIDES
from src.state import FilterSnapshot
from src.usaspending import (
    fetch_transactions_cached,
    fetch_transaction_download_rows,
    federal_fiscal_year_segments,
    period_metadata,
    query_fingerprint,
    transaction_download_payload,
)


class LiveDataIntegrityTests(unittest.TestCase):
    def test_state_options_only_from_state_funding_office(self):
        df = normalize_transactions(
            [
                {"awarding_agency_name": "Department of State", "funding_agency_name": "Department of State", "funding_office_name": "BUREAU OF INTERNATIONAL NARCOTICS"},
                {"awarding_agency_name": "Department of Defense", "funding_agency_name": "Department of Defense", "funding_office_name": "AIR FORCE MATERIAL COMMAND"},
                {"awarding_agency_name": "Department of State", "funding_agency_name": "Department of State", "awarding_office_name": "ACQUISITIONS - INL", "funding_office_name": "BUREAU OF CONSULAR AFFAIRS"},
                {"awarding_agency_name": "Department of State", "funding_agency_name": "Department of Homeland Security", "funding_office_name": "OFFICE OF INTERNATIONAL AFFAIRS"},
            ]
        )
        state_rows = filter_transactions(df, FilterSnapshot(agency="Department of State"))
        options = [option["value"] for option in build_agency_component_options(state_rows, "Department of State")]
        self.assertIn("BUREAU OF INTERNATIONAL NARCOTICS", options)
        self.assertIn("BUREAU OF CONSULAR AFFAIRS", options)
        self.assertNotIn("AIR FORCE MATERIAL COMMAND", options)
        self.assertNotIn("ACQUISITIONS - INL", options)
        self.assertNotIn("OFFICE OF INTERNATIONAL AFFAIRS", options)

    def test_cache_fingerprint_includes_agency_component_field_and_period(self):
        state = query_fingerprint(FilterSnapshot(agency="Department of State", component="BUREAU OF INTERNATIONAL NARCOTICS"))
        interior = query_fingerprint(FilterSnapshot(agency="Department of the Interior", component="Bureau of Reclamation"))
        self.assertNotEqual(state, interior)
        self.assertIn("start_date", period_metadata())
        self.assertIn("end_date", period_metadata())

    def test_cache_fingerprint_includes_exact_date_range(self):
        short = query_fingerprint(FilterSnapshot(agency="Department of State", start_date="2025-10-01", end_date="2026-06-15"))
        long = query_fingerprint(FilterSnapshot(agency="Department of State", start_date="2020-06-15", end_date="2026-06-15"))
        self.assertNotEqual(short, long)

    def test_authoritative_download_payload_is_uncapped(self):
        payload = transaction_download_payload(FilterSnapshot(agency="Department of State", start_date="2020-06-15", end_date="2026-06-15"))
        self.assertNotIn("limit", payload)
        self.assertEqual(payload["filters"]["time_period"], [{"start_date": "2020-06-15", "end_date": "2026-06-15"}])

    def test_date_range_splits_into_fiscal_year_segments(self):
        self.assertEqual(
            federal_fiscal_year_segments("2020-06-15", "2026-06-15"),
            [
                {"start_date": "2020-06-15", "end_date": "2020-09-30"},
                {"start_date": "2020-10-01", "end_date": "2021-09-30"},
                {"start_date": "2021-10-01", "end_date": "2022-09-30"},
                {"start_date": "2022-10-01", "end_date": "2023-09-30"},
                {"start_date": "2023-10-01", "end_date": "2024-09-30"},
                {"start_date": "2024-10-01", "end_date": "2025-09-30"},
                {"start_date": "2025-10-01", "end_date": "2026-06-15"},
            ],
        )

    def test_truncated_download_fails_closed(self):
        fake_rows = [{"contract_award_unique_key": "A"}]
        diagnostics = {
            "endpoint": "/api/v2/download/transactions/",
            "method": "POST",
            "payload": {},
            "headers": {},
            "status_code": 200,
            "response_body": "",
            "status_poll_responses": [{"status": "finished", "total_rows": 2}],
            "files_returned": ["Contracts_PrimeTransactions.csv"],
            "prime_files_loaded": ["Contracts_PrimeTransactions.csv"],
            "rows_per_file": {"Contracts_PrimeTransactions.csv": 1},
            "row_count": 1,
            "api_reported_total_rows": 2,
            "limit_reached": False,
            "truncation_detected": True,
        }
        with patch("src.usaspending.fetch_transaction_download_rows", return_value=([], {"error": diagnostics})):
            rows, diag = fetch_transactions_cached(
                "Department of State",
                ALL_COMPONENTS,
                ALL_NAICS,
                ALL_SET_ASIDES,
                ALL_LOCATIONS,
                "2025-10-01",
                "2026-06-15",
                query_fingerprint(FilterSnapshot(agency="Department of State")) + "-truncate-test",
                max_pages=0,
            )
        self.assertEqual(len(rows), 0)
        self.assertTrue(diag["failures"][0]["truncation_detected"])
        self.assertEqual(diag["error"], "Unable to load the complete selected date range. No new analysis was applied.")

    def test_exact_duplicate_rows_are_removed_after_segments(self):
        first = [
            {
                "contract_award_unique_key": "K1",
                "award_id_piid": "P1",
                "modification_number": "0",
                "transaction_number": "1",
                "action_date": "2025-10-01",
                "federal_action_obligation": "10",
                "recipient_name": "A",
                "awarding_agency_name": "Department of State",
            }
        ]
        second = [dict(first[0])]
        ok_diag = {
            "payload": {},
            "diagnostics": {
                "api_reported_total_rows": 1,
                "limit_reached": False,
                "truncation_detected": False,
                "row_count": 1,
                "files_returned": ["Contracts_PrimeTransactions.csv"],
                "prime_files_loaded": ["Contracts_PrimeTransactions.csv"],
                "rows_per_file": {"Contracts_PrimeTransactions.csv": 1},
            },
        }
        with patch("src.usaspending.fetch_transaction_download_rows", side_effect=[(first, ok_diag), (second, ok_diag)]):
            rows, diag = fetch_transactions_cached(
                "Department of State",
                ALL_COMPONENTS,
                ALL_NAICS,
                ALL_SET_ASIDES,
                ALL_LOCATIONS,
                "2025-09-30",
                "2025-10-01",
                query_fingerprint(FilterSnapshot(agency="Department of State", start_date="2025-09-30", end_date="2025-10-01")) + "-dedupe-test",
                max_pages=0,
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(diag["dedupe"]["rows_before_exact_dedupe"], 2)
        self.assertEqual(diag["dedupe"]["exact_duplicate_rows_removed"], 1)

    def test_inl_filter_and_naics_order(self):
        df = normalize_transactions(
            [
                {"awarding_agency_name": "Department of State", "funding_office_name": "BUREAU OF INTERNATIONAL NARCOTICS", "naics_code": "541611", "federal_action_obligation": "10", "recipient_name": "A", "contract_award_unique_key": "K1", "award_id_piid": "P1"},
                {"awarding_agency_name": "Department of State", "funding_office_name": "BUREAU OF INTERNATIONAL NARCOTICS", "naics_code": "541612", "federal_action_obligation": "20", "recipient_name": "B", "contract_award_unique_key": "K2", "award_id_piid": "P2"},
            ]
        )
        scoped = filter_transactions(df, FilterSnapshot(agency="Department of State", component="BUREAU OF INTERNATIONAL NARCOTICS", naics="541611 - Administrative Management"))
        self.assertEqual(len(scoped), 1)
        self.assertEqual(scoped["naics_code"].iloc[0], "541611")

    def test_exact_calculations_and_award_scope(self):
        df = normalize_transactions(
            [
                {"awarding_agency_name": "Department of State", "funding_office_name": "BUREAU OF INTERNATIONAL NARCOTICS", "naics_code": "541611", "federal_action_obligation": "100", "recipient_name": "A LLC", "contract_award_unique_key": "K1", "award_id_piid": "P1"},
                {"awarding_agency_name": "Department of State", "funding_office_name": "BUREAU OF INTERNATIONAL NARCOTICS", "naics_code": "541611", "federal_action_obligation": "-25", "recipient_name": "A, LLC", "contract_award_unique_key": "K1", "award_id_piid": "P1"},
                {"awarding_agency_name": "Department of State", "funding_office_name": "BUREAU OF INTERNATIONAL NARCOTICS", "naics_code": "541611", "federal_action_obligation": "50", "recipient_name": "B Inc.", "contract_award_unique_key": "K2", "award_id_piid": "P2"},
            ]
        )
        result = analyze(df, FilterSnapshot())
        self.assertEqual(result["kpis"]["net_obligations"], 125.0)
        self.assertEqual(result["kpis"]["unique_awards"], 2)
        self.assertAlmostEqual(market_concentration(df, top_n=1)["top_share"], 100 / 150)
        awards = award_table(df)
        self.assertEqual(float(awards[awards["Award ID"] == "P1"]["Obligations in Scope"].iloc[0]), 75.0)


if __name__ == "__main__":
    unittest.main()
