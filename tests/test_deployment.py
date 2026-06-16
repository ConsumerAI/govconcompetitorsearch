from __future__ import annotations

import inspect
import os
import shutil
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from src import ui
from src.option_index import (
    DEFAULT_INDEX_PATH,
    INDEX_PATH,
    PROJECT_ROOT,
    SCHEMA_VERSION,
    OptionIndexError,
    completeness_report,
    index_deployment_diagnostics,
    metadata,
    validate_index,
)


class DeploymentPathTests(unittest.TestCase):
    def test_index_path_resolves_from_file_not_cwd(self):
        self.assertEqual(INDEX_PATH, DEFAULT_INDEX_PATH)
        self.assertEqual(INDEX_PATH, PROJECT_ROOT / "data" / "option_index.sqlite")
        self.assertEqual(PROJECT_ROOT, Path(__file__).resolve().parents[1])

    def test_changing_cwd_does_not_change_resolved_index_path(self):
        original = os.getcwd()
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                os.chdir(tmpdir)
                self.assertEqual(INDEX_PATH, PROJECT_ROOT / "data" / "option_index.sqlite")
            finally:
                os.chdir(original)

    def test_linux_style_repository_path_works(self):
        linux_root = PurePosixPath("/mount/src/govconcompetitorsearch")
        linux_index = linux_root / "data" / "option_index.sqlite"
        self.assertEqual(linux_index.parts[-2:], ("data", "option_index.sqlite"))

    def test_exact_lowercase_data_path_is_required(self):
        self.assertEqual(INDEX_PATH.parent.name, "data")
        self.assertEqual(INDEX_PATH.name, "option_index.sqlite")
        self.assertNotEqual(INDEX_PATH.parent.name, "Data")
        self.assertNotEqual(INDEX_PATH.name, "Option_Index.sqlite")


class DeploymentValidationTests(unittest.TestCase):
    def test_missing_file_raises_controlled_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            missing = Path(tmpdir) / "data" / "option_index.sqlite"
            with self.assertRaises(OptionIndexError) as ctx:
                validate_index(missing)
            self.assertIn("Option index not found", str(ctx.exception))

    def test_git_lfs_pointer_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pointer = Path(tmpdir) / "option_index.sqlite"
            pointer.write_text(
                "version https://git-lfs.github.com/spec/v1\n"
                "oid sha256:deadbeef\n"
                "size 23838720\n",
                encoding="utf-8",
            )
            with self.assertRaises(OptionIndexError) as ctx:
                validate_index(pointer)
            self.assertIn("Git LFS pointer", str(ctx.exception))

    def test_real_bundled_index_validates(self):
        validate_index(INDEX_PATH)

    def test_schema_version_two_validates(self):
        meta = metadata(INDEX_PATH)
        self.assertEqual(meta["schema_version"], SCHEMA_VERSION)
        self.assertEqual(meta["schema_version"], "2")

    def test_full_agency_universe_is_present(self):
        report = completeness_report(INDEX_PATH)
        self.assertEqual(report["total_agencies_indexed"], 111)
        self.assertEqual(report["total_agency_components"], 218)
        self.assertEqual(report["total_agency_component_naics_mappings"], 35622)

    def test_deployment_diagnostics_include_path_context(self):
        diagnostics = index_deployment_diagnostics(INDEX_PATH)
        self.assertTrue(diagnostics["file_exists"])
        self.assertEqual(diagnostics["resolved_index_path"], str(INDEX_PATH))
        self.assertEqual(diagnostics["project_root"], str(PROJECT_ROOT))
        self.assertFalse(diagnostics["is_git_lfs_pointer"])
        self.assertEqual(diagnostics["schema_version"], "2")
        self.assertIn("option_index.sqlite", diagnostics["parent_directory_contents"])

    def test_external_copy_standalone_includes_database(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            copy_root = Path(tmpdir) / "copy"
            ignore = shutil.ignore_patterns(".venv", "__pycache__", "*.pyc")
            shutil.copytree(PROJECT_ROOT, copy_root, ignore=ignore)
            copied_index = copy_root / "data" / "option_index.sqlite"
            self.assertTrue(copied_index.exists())
            validate_index(copied_index)


class DeploymentUiTests(unittest.TestCase):
    def test_ui_catches_option_index_error_without_traceback(self):
        source = inspect.getsource(ui.render_filters)
        self.assertIn("except OptionIndexError", source)
        self.assertIn("INDEX_DEPLOYMENT_ERROR", source)
        self.assertIn("st.error(INDEX_DEPLOYMENT_ERROR)", source)
        self.assertNotIn("st.exception", source)
        self.assertEqual(
            ui.INDEX_DEPLOYMENT_ERROR,
            "Competitor filters are temporarily unavailable because the option index was not included in this deployment.",
        )

    def test_ui_logs_developer_diagnostics_on_index_failure(self):
        source = inspect.getsource(ui.render_filters)
        self.assertIn("index_deployment_diagnostics", source)
        self.assertIn("Developer diagnostics", source)

    def test_render_filters_returns_disabled_state_on_index_failure(self):
        with patch("src.ui.validate_index", side_effect=OptionIndexError("Option index not found: missing")):
            with patch("src.ui.st.error") as mocked_error:
                with patch("src.ui.st.expander"):
                    with patch("src.ui.st.json"):
                        with patch("src.ui.st.session_state", new=type("State", (), {"pending_snapshot": ui.FilterSnapshot()})()):
                            snapshot, ready, diagnostics, guide_step, guide_hint = ui.render_filters()
        mocked_error.assert_called_once_with(ui.INDEX_DEPLOYMENT_ERROR)
        self.assertFalse(ready)
        self.assertIn("index", diagnostics)
        self.assertEqual(guide_step, "agency")


if __name__ == "__main__":
    unittest.main()
