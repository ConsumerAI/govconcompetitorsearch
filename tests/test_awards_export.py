from __future__ import annotations

import unittest
from unittest import mock

import pandas as pd

from src.analysis import contractors_combined_detail, filter_transactions_for_contractors
from src.awards_export import awards_export_csv, awards_export_xlsx, build_awards_export_frame
from src.state import FilterSnapshot
from tests.test_analysis import sample_transactions


class ContractorSelectionTests(unittest.TestCase):
    def test_filter_transactions_for_multiple_contractors(self):
        scoped = filter_transactions_for_contractors(
            sample_transactions(),
            ["Acme Global LLC", "Beta Services Inc."],
        )
        self.assertEqual(int(scoped["canonical_contractor"].nunique()), 2)
        self.assertEqual(float(scoped["federal_action_obligation"].sum()), 1400.0)

    def test_combined_detail_aggregates_selected_contractors(self):
        detail = contractors_combined_detail(
            sample_transactions(),
            ["Acme Global LLC", "Beta Services Inc."],
        )
        self.assertEqual(detail["unique_awards"], 2)
        self.assertEqual(detail["obligations"], 1400.0)


class AwardsExportTests(unittest.TestCase):
    def test_export_frame_includes_profile_and_award_urls(self):
        from src.analysis import award_table, filter_transactions

        awards = award_table(filter_transactions(sample_transactions(), FilterSnapshot(agency="Department of State")))
        export_df = build_awards_export_frame(awards)
        self.assertIn("Recipient Profile URL", export_df.columns)
        self.assertIn("Award URL", export_df.columns)
        self.assertTrue(export_df["Award URL"].str.startswith("https://www.usaspending.gov/award/").any())

    def test_export_files_are_generated(self):
        from src.analysis import award_table, filter_transactions

        awards = award_table(filter_transactions(sample_transactions(), FilterSnapshot(agency="Department of State")))
        export_df = build_awards_export_frame(awards)
        with mock.patch("src.awards_export.usaspending_recipient_profile_url", return_value="https://www.usaspending.gov/recipient/test/latest"):
            export_df = build_awards_export_frame(awards)
        csv_bytes = awards_export_csv(export_df)
        xlsx_bytes = awards_export_xlsx(export_df)
        self.assertTrue(csv_bytes.startswith(b"\xef\xbb\xbfContractor,"))
        self.assertTrue(xlsx_bytes.startswith(b"PK"))


if __name__ == "__main__":
    unittest.main()
