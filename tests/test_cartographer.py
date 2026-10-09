"""Slice 11 tests: Cartographer repository mapper."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest

from draupnir_forge.roles.base import RoleContext, registry
from draupnir_forge.roles.cartographer import (
    BINARY_EXTENSIONS,
    LANGUAGE_BY_EXTENSION,
    SKIP_DIRS,
    Cartographer,
    DomainMap,
    FileInfo,
    map_repo,
)

import draupnir_forge.roles.cartographer as cart_module  # noqa: E402


def _write(root: str, rel: str, content: str = "x = 1\n") -> None:
    full = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as handle:
        handle.write(content)


def _write_bytes(root: str, rel: str, data: bytes) -> None:
    full = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as handle:
        handle.write(data)


def _fixture_repo() -> str:
    root = tempfile.mkdtemp(prefix="carto-fixture-")
    _write(root, "src/app/__main__.py",
           "import os\nimport json\nfrom helpers import util\nprint('hi')\n")
    _write(root, "src/app/helpers.py",
           "import sys\nimport requests\nfrom src.app import util\n")
    _write(root, "src/app/util.py", "import os\nfrom src.app import helpers\n")
    _write(root, "src/cli.py", "import argparse\n")
    _write(root, "tests/test_app.py", "import unittest\n")
    _write(root, "pyproject.toml", "[project]\nname = 'x'\n")
    _write(root, "requirements.txt", "requests\n")
    _write(root, "README.md", "# hi\n")
    _write(root, "assets/logo.png", "")  # placeholder text; real binary below
    _write_bytes(root, "assets/logo.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    _write(root, ".git/HEAD", "ref: refs/heads/main\n")
    _write(root, "node_modules/dep/index.js", "module.exports = {};\n")
    _write(root, "src/__pycache__/app.cpython-310.pyc", "x")
    return root


class TestMapRepo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = _fixture_repo()
        cls.chart = map_repo(cls.root)

    def test_files_charted(self):
        paths = {f.path for f in self.chart.files}
        self.assertIn("src/app/__main__.py", paths)
        self.assertIn("README.md", paths)

    def test_skips_forbidden_dirs(self):
        paths = {f.path for f in self.chart.files}
        self.assertFalse(any(p.startswith(".git/") for p in paths))
        self.assertFalse(any("node_modules" in p for p in paths))
        self.assertFalse(any("__pycache__" in p for p in paths))

    def test_skips_binary(self):
        paths = {f.path for f in self.chart.files}
        self.assertNotIn("assets/logo.png", paths)

    def test_language_detected(self):
        by_path = {f.path: f for f in self.chart.files}
        self.assertEqual(by_path["src/app/__main__.py"].language, "python")
        self.assertEqual(by_path["pyproject.toml"].language, "toml")
        self.assertEqual(by_path["README.md"].language, "markdown")

    def test_file_info_fields(self):
        for info in self.chart.files:
            self.assertIsInstance(info, FileInfo)
            self.assertGreaterEqual(info.size, 0)

    def test_entry_points(self):
        self.assertIn("src/app/__main__.py", self.chart.entry_points)
        self.assertIn("src/cli.py", self.chart.entry_points)

    def test_test_dirs(self):
        self.assertIn("tests", self.chart.test_dirs)

    def test_dep_files(self):
        self.assertIn("pyproject.toml", self.chart.dep_files)
        self.assertIn("requirements.txt", self.chart.dep_files)

    def test_import_graph_stdlib_only(self):
        graph = self.chart.import_graph
        self.assertIn("src.app.__main__", graph)
        self.assertIn("os", graph["src.app.__main__"])
        self.assertIn("json", graph["src.app.__main__"])
        # third-party "requests" and local "helpers" are not stdlib
        helpers = graph["src.app.helpers"]
        self.assertIn("sys", helpers)
        self.assertNotIn("requests", helpers)

    def test_cycle_detected(self):
        # src.app.helpers <-> src.app.util form a real import cycle.
        cycles = self.chart.cycles
        self.assertTrue(len(cycles) >= 1, f"expected cycles, got {cycles}")
        joined = " ".join(" ".join(c) for c in cycles)
        self.assertIn("src.app.helpers", joined)
        self.assertIn("src.app.util", joined)

    def test_domains(self):
        self.assertIn("src", self.chart.domains)
        self.assertIn("tests", self.chart.domains)
        self.assertIn("root", self.chart.domains)
        self.assertIn("README.md", self.chart.domains["root"])

    def test_no_crash_on_missing_dir(self):
        missing = tempfile.mkdtemp(prefix="carto-will-delete-")
        shutil.rmtree(missing)  # now it truly does not exist
        chart = map_repo(missing)
        self.assertIsInstance(chart, DomainMap)
        self.assertEqual(chart.files, [])

    def test_module_data_present(self):
        self.assertIn(".py", LANGUAGE_BY_EXTENSION)
        self.assertIn(".git", SKIP_DIRS)
        self.assertIn(".png", BINARY_EXTENSIONS)


class TestCartographerRun(unittest.TestCase):
    def setUp(self):
        self.root = _fixture_repo()
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.root, ignore_errors=True))

    def test_run_writes_both_maps(self):
        ctx = RoleContext(project_dir=self.root)
        result = Cartographer().run(ctx)
        self.assertTrue(result.ok, result.summary)
        md_path = os.path.join(self.root, ".mythis", "DOMAIN_MAP.md")
        json_path = os.path.join(self.root, ".mythis", "domain_map.json")
        self.assertEqual(result.artifacts["domain_map_path"], md_path)
        self.assertEqual(result.artifacts["domain_map_json"], json_path)
        self.assertTrue(os.path.isfile(md_path))
        self.assertTrue(os.path.isfile(json_path))

        with open(md_path, encoding="utf-8") as handle:
            md = handle.read()
        self.assertIn("# Domain Map", md)
        self.assertIn("## Import cycles", md)
        self.assertIn("src/app/__main__.py", md)

        with open(json_path, encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertIn("files", data)
        self.assertIn("import_graph", data)
        self.assertIn("cycles", data)
        self.assertIn("domains", data)
        self.assertIsInstance(result.artifacts["domain_map"], DomainMap)

    def test_run_never_crashes(self):
        missing = tempfile.mkdtemp(prefix="carto-run-will-delete-")
        shutil.rmtree(missing)  # now it truly does not exist
        self.addCleanup(shutil.rmtree, missing, True)
        ctx = RoleContext(project_dir=missing)
        result = Cartographer().run(ctx)
        # empty chart is still a successful mapping of an empty repo
        self.assertIsInstance(result.ok, bool)

    def test_registered(self):
        self.assertIn("cartographer", registry.names())
        self.assertEqual(registry.create("cartographer").name, "cartographer")


if __name__ == "__main__":
    unittest.main()
