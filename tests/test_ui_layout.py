from __future__ import annotations

import inspect
import time
import unittest
from unittest import mock

from src import ui
from src.constants import ALL_COMPONENTS, ALL_NAICS


class UiLayoutTests(unittest.TestCase):
    def test_search_control_order_places_date_after_optional_refinements_before_button(self):
        source = inspect.getsource(ui.render_filters) + inspect.getsource(ui.main)
        self.assertLess(source.index('"Agency"'), source.index('"NAICS"'))
        self.assertLess(source.index('"NAICS"'), source.index('"Optional refinements"'))
        self.assertLess(source.index('"Optional refinements"'), source.index("render_date_range"))
        self.assertLess(source.index("render_date_range"), source.index('"Find Competitors"'))

    def test_no_quick_fill_buttons_are_visible(self):
        source = inspect.getsource(ui.render_date_range)
        self.assertNotIn('st.button("Current FY")', source)
        self.assertNotIn('st.button("3 Years")', source)
        self.assertNotIn('st.button("6 Years")', source)
        self.assertNotIn('st.button("10 Years")', source)

    def test_date_values_remain_editable(self):
        source = inspect.getsource(ui.render_date_range)
        self.assertIn('st.date_input("From"', source)
        self.assertIn('st.date_input("Through"', source)

    def test_internal_messages_are_removed(self):
        source = inspect.getsource(ui.render_filters) + inspect.getsource(ui.main)
        self.assertNotIn("Select a NAICS for a more opportunity-specific competitor view.", source)
        self.assertNotIn("Broad search", source)
        self.assertNotIn("No prior results are restored", source)

    def test_option_selection_paths_do_not_call_full_transaction_downloads(self):
        source = inspect.getsource(ui._option_sets) + inspect.getsource(ui.render_filters)
        self.assertNotIn("fetch_transactions_for_snapshot", source)
        self.assertNotIn("fetch_transaction_download_rows", source)
        self.assertNotIn("fetch_transactions_cached", source)

    def test_five_second_timeout_helper_exits_loading_state(self):
        started = time.perf_counter()
        result, timed_out = ui._run_with_deadline(lambda: time.sleep(0.05), timeout_seconds=0.01)
        elapsed = time.perf_counter() - started
        self.assertIsNone(result)
        self.assertTrue(timed_out)
        self.assertLess(elapsed, 0.1)

    def test_filter_guide_skips_component_when_agency_has_no_subagencies(self):
        step, _hint = ui._filter_guide_step(
            "Department of Example",
            [ALL_COMPONENTS],
            ALL_COMPONENTS,
            ALL_NAICS,
            True,
        )
        self.assertEqual(step, "naics")

    def test_filter_guide_highlights_component_when_subagencies_exist(self):
        step, _hint = ui._filter_guide_step(
            "Department of State",
            [ALL_COMPONENTS, "Bureau of Example"],
            ALL_COMPONENTS,
            ALL_NAICS,
            True,
            component_label="Subagency / Bureau",
        )
        self.assertEqual(step, "component")

    def test_filter_guide_advances_to_submit_when_required_filters_are_set(self):
        step, _hint = ui._filter_guide_step(
            "Department of State",
            [ALL_COMPONENTS],
            ALL_COMPONENTS,
            "541611||Administrative Management",
            True,
        )
        self.assertEqual(step, "submit")

    def test_filter_guide_highlights_submit_when_all_naics_is_selected(self):
        step, hint = ui._filter_guide_step(
            "Department of State",
            [ALL_COMPONENTS],
            ALL_COMPONENTS,
            ALL_NAICS,
            True,
        )
        self.assertEqual(step, "naics")
        self.assertIn("Find Competitors", hint)
        self.assertFalse(ui._submit_guide_active(step))

    def test_filter_guide_does_not_include_dates_step(self):
        source = inspect.getsource(ui._filter_guide_step)
        self.assertNotIn('"dates"', source)

    def test_guide_suppressed_when_results_match_pending_filters(self):
        pending = ui.FilterSnapshot(agency="Department of State", naics="541611||Administrative Management")
        analyzed = ui.FilterSnapshot(agency="Department of State", naics="541611||Administrative Management")
        session = type("State", (), {"analysis_results": {"leaderboard": []}, "analyzed_snapshot": analyzed})()
        with mock.patch("src.ui.st.session_state", new=session):
            self.assertTrue(ui._guide_suppressed(pending))

    def test_guide_not_suppressed_when_filters_changed_after_results(self):
        pending = ui.FilterSnapshot(agency="Department of the Interior")
        analyzed = ui.FilterSnapshot(agency="Department of State")
        session = type("State", (), {"analysis_results": {"leaderboard": []}, "analyzed_snapshot": analyzed})()
        with mock.patch("src.ui.st.session_state", new=session):
            self.assertFalse(ui._guide_suppressed(pending))

    def test_loading_and_timeout_messages_include_upstream_selection(self):
        self.assertEqual(ui._loading_message("component", "Department of State"), "Loading bureaus / funding offices for Department of State...")
        self.assertEqual(ui._loading_message("component", "Department of the Interior"), "Loading bureaus for Department of the Interior...")
        self.assertIn("Bureau of Reclamation", ui._loading_message("naics", "Department of the Interior", "Bureau of Reclamation"))
        self.assertIn("Retry", inspect.getsource(ui._session_cached_lookup))
        self.assertIn("[UNAVAILABLE]", inspect.getsource(ui._session_cached_lookup))

    def test_optional_refinements_allow_default_only_options(self):
        source = inspect.getsource(ui._option_sets)
        self.assertIn("allow_default_only=True", source)

    def test_sync_selectbox_state_uses_first_option_when_preferred_missing(self):
        session = {"filter_naics": "541611||Old Code"}
        with mock.patch("src.ui.st.session_state", new=session):
            ui._sync_selectbox_state("filter_naics", [ui.UNAVAILABLE], ui.ALL_NAICS)
        self.assertEqual(session["filter_naics"], ui.UNAVAILABLE)

    def test_successful_option_diagnostics_do_not_show_warning(self):
        diagnostics = {
            "component": {"lookup_type": "Agency Component", "elapsed_ms": 1.2},
            "set_aside": {"lookup_type": "Set-Aside", "rows_returned": 0},
            "location": {"lookup_type": "Performance Location", "cache_level_used": "persistent_index"},
        }
        self.assertEqual(ui._option_diagnostic_errors(diagnostics), {})
        self.assertEqual(ui._option_diagnostic_errors({"set_aside": {"error": "timed out"}}), {})
        self.assertEqual(
            ui._option_diagnostic_errors({"component": {"error": "timed out"}}),
            {"component": {"error": "timed out"}},
        )


if __name__ == "__main__":
    unittest.main()
