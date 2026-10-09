"""Slice 30 tests: existing-repo mode (map-before-modifying law)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from draupnir_forge.modes_existing import (
    ExistingRepoMode,
    NotMappedError,
)
from draupnir_forge.roles.planner import ForgeTask


def _write(root: str, rel: str, content: str) -> str:
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


def _fixture_repo(root: str) -> str:
    """A small repo with docs, code, a binary blob, and a doc gap."""
    repo = os.path.join(root, "repo")
    _write(repo, "README.md",
           "# Demo\n\nSee `demo/core.py` for the engine.\n\n"
           "Also see `demo/ghost.py` which was deleted.\n")
    _write(repo, "docs/guide.md", "# Guide\n\nUses `demo.core`.\n")
    _write(repo, "demo/__init__.py", "")
    _write(repo, "demo/core.py", "VALUE = 42\n")
    _write(repo, "demo/secret.py", "# undocumented module\n")
    _write(repo, "assets/logo.png", "PNG-DATA")  # binary blob by extension
    return repo


class TestMapBeforeModifyingLaw(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = _fixture_repo(self.tmp.name)
        self.mode = ExistingRepoMode(self.repo)

    def test_not_ready_before_map(self):
        self.assertFalse(self.mode.ready_for_worker())

    def test_ready_after_map(self):
        self.mode.map_repository()
        self.assertTrue(self.mode.ready_for_worker())

    def test_build_roadmap_blocked_pre_map(self):
        with self.assertRaises(NotMappedError):
            self.mode.build_roadmap("do things")

    def test_compare_blocked_pre_map(self):
        with self.assertRaises(NotMappedError):
            self.mode.compare_docs_to_code()

    def test_risks_blocked_pre_map(self):
        with self.assertRaises(NotMappedError):
            self.mode.identify_risks()


class TestDiscovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = _fixture_repo(self.tmp.name)
        self.mode = ExistingRepoMode(self.repo)
        self.domain_map = self.mode.map_repository()

    def test_domain_map_persisted(self):
        path = os.path.join(self.repo, ".mythis", "domain_map.json")
        self.assertTrue(os.path.isfile(path))
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertIn("files", data)
        paths = {f["path"] for f in data["files"]}
        self.assertIn("demo/core.py", paths)

    def test_doc_inventory(self):
        docs = self.mode.inventory_docs()
        self.assertIn("README.md", docs)
        self.assertIn("docs/guide.md", docs)

    def test_doc_gaps(self):
        self.mode.inventory_docs()
        gaps = self.mode.compare_docs_to_code()
        # ghost.py is mentioned in README but missing from the repo.
        self.assertIn("demo/ghost.py", gaps["mentioned_missing"])
        # secret.py exists in code but no doc mentions it.
        self.assertIn("demo/secret.py", gaps["undocumented_modules"])
        # core.py is documented, so it must not be flagged.
        self.assertNotIn("demo/core.py", gaps["undocumented_modules"])

    def test_risks_found(self):
        risks = self.mode.identify_risks()
        ids = {r["id"] for r in risks}
        self.assertIn("no_tests", ids)
        self.assertIn("no_ci", ids)
        self.assertIn("binary_blob", ids)
        blob_risks = [r for r in risks if r["id"] == "binary_blob"]
        self.assertTrue(any("logo.png" in r["detail"] for r in blob_risks))

    def test_no_huge_file_risk_for_small_repo(self):
        risks = self.mode.identify_risks()
        self.assertNotIn("huge_file", {r["id"] for r in risks})

    def test_huge_file_detected(self):
        big = os.path.join(self.repo, "demo", "big.py")
        with open(big, "w", encoding="utf-8") as fh:
            for i in range(5001):
                fh.write(f"# line {i}\n")
        self.mode.map_repository()
        risks = self.mode.identify_risks()
        self.assertIn("huge_file", {r["id"] for r in risks})


class TestRoadmap(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = _fixture_repo(self.tmp.name)
        self.mode = ExistingRepoMode(self.repo)
        self.mode.map_repository()
        self.mode.identify_risks()

    def test_build_roadmap_returns_real_tasks(self):
        tasks = self.mode.build_roadmap("Add caching to the demo engine.")
        self.assertGreaterEqual(len(tasks), 2)  # goal + risk mitigations
        for task in tasks:
            self.assertIsInstance(task, ForgeTask)
        ids = [t.task_id for t in tasks]
        self.assertEqual(ids[0], "E-001")
        # Risk mitigations are chained after the goal task.
        self.assertEqual(tasks[1].depends_on, ["E-001"])
        titles = " ".join(t.title for t in tasks)
        self.assertIn("test harness", titles.lower())

    def test_roadmap_file_written(self):
        self.mode.build_roadmap("Add caching.")
        path = os.path.join(self.repo, ".mythis", "ROADMAP.md")
        self.assertTrue(os.path.isfile(path))
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("# Change Roadmap", body)
        self.assertIn("E-001", body)


class TestRunContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = _fixture_repo(self.tmp.name)

    def test_run_returns_contract(self):
        mode = ExistingRepoMode(self.repo)
        result = mode.run("Add caching to the demo engine.")
        self.assertTrue(result["ready"])
        self.assertIn("files", result["domain_map"])
        self.assertIsInstance(result["risks"], list)
        self.assertIn("mentioned_missing", result["doc_gaps"])
        self.assertTrue(result["roadmap_path"].endswith("ROADMAP.md"))
        self.assertTrue(os.path.isfile(result["roadmap_path"]))

    def test_run_on_large_repo(self):
        # The mode must survive a large repo; generate a synthetic one
        # (dozens of modules) instead of copying a whole checkout.
        big = os.path.join(self.tmp.name, "bigrepo")
        for pkg in range(6):
            for mod in range(10):
                _write(big, f"pkg{pkg}/mod{mod}.py",
                       f'"""Module {mod}."""\nVALUE_{mod} = {mod}\n')
            _write(big, f"pkg{pkg}/__init__.py", "")
        _write(big, "README.md", "# Big\n\nSee `pkg0/mod0.py`.\n")
        _write(big, "tests/test_x.py", "import unittest\n")
        mode = ExistingRepoMode(big)
        result = mode.run("Map this repository.")
        self.assertTrue(result["ready"])
        self.assertGreater(len(result["domain_map"]["files"]), 60)
        self.assertIn("pkg0/mod0.py",
                      [f["path"] for f in result["domain_map"]["files"]])

    def test_run_never_raises(self):
        mode = ExistingRepoMode(os.path.join(self.tmp.name, "missing"))
        result = mode.run("Map nothing.")
        self.assertIn("ready", result)


if __name__ == "__main__":
    unittest.main()
