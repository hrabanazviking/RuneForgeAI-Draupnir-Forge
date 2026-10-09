"""Slice 29 tests: new-project mode (vision, scaffold, smoke test)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

from draupnir_forge.modes_new import (
    NewProjectMode,
    _readme_content,
    _smoke_test_content,
    slugify_package_name,
)
from draupnir_forge.roles.planner import ForgeTask


class TestSlugify(unittest.TestCase):
    def test_meaningful_words(self):
        self.assertEqual(slugify_package_name("Build a todo list manager"),
                         "todo_list_manager")

    def test_stopwords_dropped(self):
        self.assertEqual(slugify_package_name("Create a new app for notes"),
                         "notes")

    def test_fallback(self):
        self.assertEqual(slugify_package_name("the and of"), "forge_app")
        self.assertEqual(slugify_package_name(""), "forge_app")

    def test_leading_digit_guarded(self):
        name = slugify_package_name("3d model viewer")
        self.assertTrue(name[0].isalpha() or name.startswith("pkg_"))


class TestNewProjectMode(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = os.path.join(self.tmp.name, "proj")
        os.makedirs(self.project)
        self.mode = NewProjectMode(self.project)

    def test_run_returns_contract(self):
        result = self.mode.run("Build a tiny task queue with priorities.")
        self.assertEqual(result["project_dir"], os.path.abspath(self.project))
        self.assertEqual(result["tasks_done"], ["N-001", "N-002", "N-003"])
        self.assertTrue(result["vision_path"].endswith("VISION.md"))
        self.assertTrue(result["smoke_passed"])

    def test_vision_written(self):
        result = self.mode.run("Build a tiny task queue with priorities.")
        with open(result["vision_path"], encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("## Goal", body)
        self.assertIn("task queue", body.lower())

    def test_scaffold_files_exist(self):
        self.mode.run("Build a tiny task queue with priorities.")
        package = slugify_package_name(
            "Build a tiny task queue with priorities.")
        for rel in (os.path.join("src", package, "__init__.py"),
                    os.path.join("tests", "__init__.py"),
                    os.path.join("tests", "test_smoke.py"),
                    "README.md"):
            self.assertTrue(os.path.isfile(os.path.join(self.project, rel)),
                            rel)

    def test_smoke_test_really_passes(self):
        # Independent re-verification: the scaffold's own suite passes.
        self.mode.run("Build a tiny task queue with priorities.")
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover",
             "-s", "tests", "-q"],
            cwd=self.project, capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])

    def test_smoke_test_content_asserts_behavior(self):
        content = _smoke_test_content("demo_pkg")
        self.assertIn("import demo_pkg", content)
        self.assertIn("greet", content)
        self.assertIn("assertIn", content)

    def test_readme_has_quickstart(self):
        content = _readme_content("demo_pkg", "Do the thing.")
        self.assertIn("## Quickstart", content)
        self.assertIn("unittest", content)

    def test_tasks_are_real_forge_tasks(self):
        tasks = self.mode._starter_tasks("demo_pkg")
        self.assertEqual(len(tasks), 3)
        for task in tasks:
            self.assertIsInstance(task, ForgeTask)
        self.assertEqual(tasks[1].depends_on, [tasks[0].task_id])
        self.assertEqual(tasks[2].depends_on, [tasks[1].task_id])

    def test_never_raises_on_empty_goal(self):
        result = self.mode.run("")
        self.assertIn("smoke_passed", result)


if __name__ == "__main__":
    unittest.main()
