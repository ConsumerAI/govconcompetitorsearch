from __future__ import annotations

import ast
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_FILES = [PROJECT_ROOT / "app.py", *sorted((PROJECT_ROOT / "src").glob("*.py"))]


class PathGuardTests(unittest.TestCase):
    def test_no_forbidden_runtime_boundaries(self):
        parent_name = "govcon" + "agency" + "trends"
        forbidden_literals = [
            "solicitation" + "_workflow",
            "resolved" + "_signals",
            "package" + "_cache",
            "data" + "/" + "runs",
            "reference" + "_aegis",
            "OPEN" + "AI",
            "package" + "_llm",
            "from " + "extraction",
            "import " + "extraction",
            "." + "." + "/",
            parent_name,
        ]
        for path in PRODUCTION_FILES:
            text = path.read_text(encoding="utf-8")
            for literal in forbidden_literals:
                self.assertNotIn(literal, text, f"{literal} found in {path}")
            tree = ast.parse(text, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertFalse(alias.name.split(".")[0] == "ex" + "traction")
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertFalse(node.module.split(".")[0] == "ex" + "traction")

    def test_no_imports_resolve_outside_project(self):
        allowed_roots = {
            "__future__",
            "src",
            "streamlit",
            "pandas",
            "requests",
            "functools",
            "collections",
            "dataclasses",
            "datetime",
            "urllib",
            "html",
            "csv",
            "io",
            "time",
            "zipfile",
            "hashlib",
            "json",
            "logging",
            "concurrent",
            "contextlib",
            "os",
            "pathlib",
            "sqlite3",
            "tempfile",
            "typing",
            "openpyxl",
        }
        for path in PRODUCTION_FILES:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertIn(alias.name.split(".")[0], allowed_roots)
                if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    self.assertIn(node.module.split(".")[0], allowed_roots)


if __name__ == "__main__":
    unittest.main()
