from __future__ import annotations

import unittest

from src.analysis import analyze, filter_transactions
from src.state import FilterSnapshot, active_filter_chips, fresh_session_state, run_new_analysis, snapshots_differ, update_pending_only
from tests.test_analysis import sample_transactions


class StateIsolationTests(unittest.TestCase):
    def test_fresh_session_has_no_results_or_chips(self):
        session = fresh_session_state()
        self.assertIsNone(session["results"])
        self.assertEqual(session["active_chips"], [])
        self.assertIsNone(session["prior_analyzed_filter_snapshot"])

    def test_two_sessions_hold_different_agencies(self):
        one = update_pending_only(fresh_session_state(), FilterSnapshot(agency="Department of State"))
        two = update_pending_only(fresh_session_state(), FilterSnapshot(agency="Department of the Interior"))
        self.assertNotEqual(one["pending"].agency, two["pending"].agency)

    def test_pending_selection_does_not_mutate_results(self):
        df = sample_transactions()
        state_snapshot = FilterSnapshot(agency="Department of State")
        state_results = analyze(filter_transactions(df, state_snapshot), FilterSnapshot())
        session = run_new_analysis(fresh_session_state(), state_snapshot, state_results)
        changed = update_pending_only(session, FilterSnapshot(agency="Department of Housing and Urban Development"))
        self.assertTrue(snapshots_differ(changed["pending"], changed["analyzed"]))
        self.assertEqual(changed["results"]["kpis"]["net_obligations"], 1400.0)
        self.assertIn("Department of State", " ".join(active_filter_chips(changed["analyzed"])))

    def test_pending_date_change_does_not_mutate_results(self):
        df = sample_transactions()
        state_snapshot = FilterSnapshot(agency="Department of State", start_date="2020-06-15", end_date="2026-06-15")
        state_results = analyze(filter_transactions(df, state_snapshot), FilterSnapshot())
        session = run_new_analysis(fresh_session_state(), state_snapshot, state_results)
        changed = update_pending_only(session, FilterSnapshot(agency="Department of State", start_date="2023-06-15", end_date="2026-06-15"))
        self.assertTrue(snapshots_differ(changed["pending"], changed["analyzed"]))
        self.assertEqual(changed["results"]["kpis"]["net_obligations"], 1400.0)
        self.assertIn("Period: 2020-06-15 to 2026-06-15", " ".join(active_filter_chips(changed["analyzed"])))

    def test_new_analysis_atomically_replaces_snapshot(self):
        df = sample_transactions()
        session = fresh_session_state()
        state_snapshot = FilterSnapshot(agency="Department of State")
        session = run_new_analysis(session, state_snapshot, analyze(filter_transactions(df, state_snapshot), FilterSnapshot()))
        hud_snapshot = FilterSnapshot(agency="Department of Housing and Urban Development")
        session = run_new_analysis(session, hud_snapshot, analyze(filter_transactions(df, hud_snapshot), FilterSnapshot()))
        chips = " ".join(active_filter_chips(session["analyzed"]))
        self.assertIn("Department of Housing and Urban Development", chips)
        self.assertNotIn("Department of State", chips)
        self.assertEqual(session["results"]["kpis"]["net_obligations"], 700.0)

    def test_cached_data_returns_data_only(self):
        def fake_cache_return():
            return sample_transactions()

        session = fresh_session_state()
        data = fake_cache_return()
        self.assertEqual(len(data), 5)
        self.assertIsNone(session["results"])
        self.assertEqual(session["active_chips"], [])


if __name__ == "__main__":
    unittest.main()
