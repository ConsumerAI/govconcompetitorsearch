from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import Mock, patch

import requests

from src.constants import ALL_COMPONENTS, ALL_LOCATIONS, ALL_NAICS, ALL_SET_ASIDES
from src.state import FilterSnapshot, fresh_session_state
from src.usaspending import (
    BASE_TRANSACTION_FIELDS,
    TRANSACTION_FIELDS,
    fetch_naics_options,
    fetch_transactions_cached,
    fiscal_year_date_range,
    post_usaspending,
    query_fingerprint,
    set_aside_options,
    transaction_payload,
)


class PayloadRepairTests(unittest.TestCase):
    def test_transaction_payload_matches_parent_schema(self):
        payload = transaction_payload(FilterSnapshot(agency="Department of State", naics="541611 - Administrative Management"))
        self.assertEqual(payload["fields"], TRANSACTION_FIELDS)
        self.assertIn("Transaction Amount", payload["fields"])
        self.assertIn("Primary Place of Performance", payload["fields"])
        self.assertNotIn("Federal Action Obligation", payload["fields"])
        self.assertEqual(payload["filters"]["naics_codes"], {"require": ["541611"]})
        self.assertEqual(payload["filters"]["award_type_codes"], ["A", "B", "C", "D"])
        self.assertEqual(payload["filters"]["award_or_idv_flag"], "AWARD")
        self.assertEqual(payload["limit"], 100)
        self.assertEqual(payload["page"], 1)

    def test_fiscal_year_boundary_is_current_fy(self):
        start_date, end_date = fiscal_year_date_range()
        self.assertRegex(start_date, r"^\d{4}-10-01$")
        self.assertRegex(end_date, r"^\d{4}-\d{2}-\d{2}$")
        self.assertLessEqual(end_date, date.today().isoformat())

    def test_fallback_payload_removes_office_fields(self):
        payload = transaction_payload(FilterSnapshot(agency="Department of State"), include_office_fields=False)
        self.assertEqual(payload["fields"], BASE_TRANSACTION_FIELDS)
        self.assertNotIn("Awarding Office", payload["fields"])

    def test_set_aside_options_populate(self):
        options = set_aside_options()
        self.assertIn(ALL_SET_ASIDES, options)
        self.assertTrue(any(option.startswith("WOSB - ") for option in options))

    def test_option_payload_excludes_exact_selected_dates(self):
        with patch("src.usaspending.post_usaspending", return_value=({"results": []}, None)) as mocked:
            fetch_naics_options(FilterSnapshot(agency="Department of the Interior", start_date="2020-06-15", end_date="2026-06-15"))
        payload = mocked.call_args.args[1]
        self.assertNotEqual(payload["filters"]["time_period"], [{"start_date": "2020-06-15", "end_date": "2026-06-15"}])

    def test_option_fingerprint_excludes_exact_dates(self):
        first = query_fingerprint(FilterSnapshot(agency="Department of the Interior", start_date="2020-06-15", end_date="2026-06-15"), option_category="naics")
        second = query_fingerprint(FilterSnapshot(agency="Department of the Interior", start_date="2025-10-01", end_date="2026-06-15"), option_category="naics")
        self.assertEqual(first, second)


class ApiFailureTests(unittest.TestCase):
    def test_response_body_is_captured_for_400(self):
        response = requests.Response()
        response.status_code = 400
        response._content = b'{"detail":"Field Federal Action Obligation is not supported"}'
        error = requests.exceptions.HTTPError(response=response)
        with patch("src.usaspending.requests.post", side_effect=error):
            data, failure = post_usaspending("/api/v2/search/spending_by_transaction/", {"fields": ["Federal Action Obligation"]})
        self.assertIsNone(data)
        self.assertIsNotNone(failure)
        self.assertEqual(failure.status_code, 400)
        self.assertIn("Federal Action Obligation", failure.response_body)
        self.assertEqual(failure.endpoint, "/api/v2/search/spending_by_transaction/")

    def test_400_does_not_create_zero_result_success(self):
        response = requests.Response()
        response.status_code = 400
        response._content = b'{"detail":"bad fields"}'
        with patch("src.usaspending.requests.post") as mocked_post:
            mocked_post.return_value = response
            response.raise_for_status = Mock(side_effect=requests.exceptions.HTTPError(response=response))
            df, diagnostic = fetch_transactions_cached(
                "Department of State",
                ALL_COMPONENTS,
                ALL_NAICS,
                ALL_SET_ASIDES,
                ALL_LOCATIONS,
                "2025-10-01",
                "2026-06-15",
                query_fingerprint(FilterSnapshot(agency="Department of State")),
                max_pages=1,
            )
        self.assertTrue(df.empty)
        self.assertEqual(diagnostic["error"], "Unable to load the complete selected date range. No new analysis was applied.")
        self.assertGreaterEqual(len(diagnostic["failures"]), 1)
        session = fresh_session_state()
        self.assertIsNone(session["analyzed"])
        self.assertIsNone(session["results"])


class DownloadResilienceTests(unittest.TestCase):
    def test_transient_error_detection(self):
        from src.usaspending import _is_transient_download_error

        self.assertTrue(_is_transient_download_error({"response_body": "RemoteDisconnected"}))
        self.assertFalse(_is_transient_download_error({"response_body": "download appears capped or truncated"}))

    def test_segment_retries_transient_failure(self):
        from src.usaspending import fetch_transactions_uncached

        ok_row = {
            "contract_award_unique_key": "K1",
            "award_id_piid": "P1",
            "transaction_number": "1",
            "action_date": "2025-10-01",
            "federal_action_obligation": "10",
            "recipient_name": "A",
            "awarding_agency_name": "Department of Agriculture",
        }
        ok_diag = {
            "payload": {},
            "diagnostics": {
                "api_reported_total_rows": 1,
                "limit_reached": False,
                "truncation_detected": False,
                "row_count": 1,
            },
        }
        fail_diag = {
            "error": {
                "endpoint": "/api/v2/download/transactions/",
                "response_body": "('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))",
            }
        }
        with patch("src.usaspending.fetch_transaction_download_rows", side_effect=[([], fail_diag), ([ok_row], ok_diag)]):
            with patch("src.usaspending.time.sleep"):
                rows, diag = fetch_transactions_uncached(
                    "Department of Agriculture",
                    "Farm Service Agency",
                    ALL_NAICS,
                    ALL_SET_ASIDES,
                    ALL_LOCATIONS,
                    "2025-10-01",
                    "2026-06-15",
                    query_fingerprint(
                        FilterSnapshot(
                            agency="Department of Agriculture",
                            component="Farm Service Agency",
                            start_date="2025-10-01",
                            end_date="2026-06-15",
                        )
                    )
                    + "-retry-test",
                    max_pages=0,
                )
        self.assertEqual(len(rows), 1)
        self.assertFalse(diag["error"])


if __name__ == "__main__":
    unittest.main()
