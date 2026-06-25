from __future__ import annotations

import unittest
from datetime import date

from src.state import FilterSnapshot, add_calendar_years
from src.ui import _validate_date_range, analysis_disabled


class DateRangeTests(unittest.TestCase):
    def test_default_range_is_six_calendar_years(self):
        snapshot = FilterSnapshot()
        self.assertEqual(snapshot.start_date, add_calendar_years(date.today(), -6).isoformat())
        self.assertEqual(snapshot.end_date, date.today().isoformat())

    def test_recent_wins_period_is_independent_twelve_month_window(self):
        from src.state import recent_wins_period

        start_date, end_date = recent_wins_period()
        self.assertEqual(start_date, add_calendar_years(date.today(), -1).isoformat())
        self.assertEqual(end_date, date.today().isoformat())

    def test_validation_rejects_inverted_future_and_too_long_ranges(self):
        self.assertEqual(_validate_date_range("2026-06-15", "2020-06-15"), "Start date must be on or before the end date.")
        self.assertEqual(_validate_date_range("2020-06-15", "9999-01-01"), "The end date cannot be in the future.")
        self.assertEqual(_validate_date_range("2000-01-01", date.today().isoformat()), "Select a period of 10 years or less.")

    def test_invalid_dates_disable_analysis(self):
        pending = FilterSnapshot(agency="Department of State", start_date="2026-06-16", end_date="2020-06-16")
        date_error = _validate_date_range(pending.start_date, pending.end_date)
        self.assertEqual(date_error, "Start date must be on or before the end date.")
        self.assertTrue(analysis_disabled(pending, options_ready=True, date_error=date_error))
        self.assertTrue(analysis_disabled(FilterSnapshot(), options_ready=True))
        self.assertFalse(analysis_disabled(FilterSnapshot(agency="Department of State"), options_ready=True))


if __name__ == "__main__":
    unittest.main()
