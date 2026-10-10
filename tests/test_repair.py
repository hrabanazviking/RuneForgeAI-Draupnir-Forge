"""Slice 37 tests: RepairEngine — bounded self-repair, give-up, evidence."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from draupnir_forge.failures import (
    FailureClass,
    FailureRecord,
    classify_failure,
)
from draupnir_forge.repair import (
    MAX_ATTEMPTS,
    RepairEngine,
    RepairResult,
    _within_bounds,
    select_hint,
)
from draupnir_forge.tasks import ForgeTask


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _task(task_id: str = "T-001") -> ForgeTask:
    return ForgeTask(task_id=task_id, title="mend the sail",
                     domain="core", status="in_progress")


def _name_error_failure(task_id: str = "T-001") -> FailureRecord:
    detail = (
        'Traceback (most recent call last):\n'
        '  File "tests/test_app.py", line 9, in test_run\n'
        '    self.assertEqual(run(), 42)\n'
        '  File "app.py", line 2, in run\n'
        '    return helper()\n'
        "NameError: name 'helper' is not defined\n"
    )
    return FailureRecord(
        failure_class=classify_failure(context=detail),
        task_id=task_id,
        detail=detail,
        attempt=1,
    )


class RepairEngineTest(unittest.TestCase):
    """A planted NameError must be fixed; budgets and bounds respected."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _write(self.root / "app.py",
               "def run():\n    return helper()\n")
        _write(self.root / "util.py",
               "def helper():\n    return 42\n")
        _write(self.root / "tests" / "test_app.py",
               "import os\n"
               "import sys\n"
               "import unittest\n"
               "sys.path.insert(0, os.path.dirname(os.path.dirname(\n"
               "    os.path.abspath(__file__))))\n"
               "from app import run\n\n\n"
               "class TestApp(unittest.TestCase):\n"
               "    def test_run(self):\n"
               "        self.assertEqual(run(), 42)\n")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_fixes_planted_name_error(self) -> None:
        engine = RepairEngine(self.root)
        result = engine.attempt(_task(), _name_error_failure(), {})
        self.assertIsInstance(result, RepairResult)
        self.assertTrue(result.repaired, "the NameError should be repaired")
        self.assertIsNotNone(result.patch)
        self.assertIn("from util import helper", result.patch)
        self.assertIsNone(result.new_failure)
        self.assertEqual(result.attempts, 1)
        # The file on disk really carries the fix.
        self.assertIn("from util import helper",
                      (self.root / "app.py").read_text(encoding="utf-8"))

    def test_history_and_evidence_written(self) -> None:
        engine = RepairEngine(self.root)
        engine.attempt(_task(), _name_error_failure(), {})
        history_path = self.root / ".mythis" / "repair_history.jsonl"
        self.assertTrue(history_path.is_file())
        records = [json.loads(line) for line in
                   history_path.read_text(encoding="utf-8").splitlines()
                   if line.strip()]
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0]["repaired"])
        self.assertEqual(records[0]["task_id"], "T-001")
        evidence_dir = self.root / ".mythis" / "evidence" / "repair"
        self.assertTrue(any(evidence_dir.iterdir()))

    def test_gives_up_after_max_attempts(self) -> None:
        history_path = self.root / ".mythis" / "repair_history.jsonl"
        history_path.parent.mkdir(parents=True, exist_ok=True)
        with open(history_path, "w", encoding="utf-8") as handle:
            for i in range(MAX_ATTEMPTS):
                handle.write(json.dumps({
                    "task_id": "T-001", "attempt": i + 1,
                    "repaired": False}) + "\n")
        engine = RepairEngine(self.root)
        before = (self.root / "app.py").read_text(encoding="utf-8")
        result = engine.attempt(_task(), _name_error_failure(), {})
        self.assertFalse(result.repaired)
        self.assertIsNone(result.patch)
        self.assertEqual(result.attempts, MAX_ATTEMPTS)
        # Nothing was touched: the budget was already spent.
        self.assertEqual((self.root / "app.py").read_text(encoding="utf-8"),
                         before)

    def test_never_edits_test_files(self) -> None:
        patch = (
            "--- a/tests/test_app.py\n"
            "+++ b/tests/test_app.py\n"
            "@@ -8,3 +8,3 @@\n"
            " class TestApp(unittest.TestCase):\n"
            "     def test_run(self):\n"
            "-        self.assertEqual(run(), 42)\n"
            "+        self.assertEqual(run(), 41)\n"
        )
        engine = RepairEngine(self.root)
        result = engine.attempt(_task(), _name_error_failure(),
                                {"patch": patch})
        self.assertFalse(result.repaired)
        self.assertIn("test file", (result.new_failure.detail
                                    if result.new_failure else ""))
        content = (self.root / "tests" / "test_app.py").read_text(
            encoding="utf-8")
        self.assertIn("assertEqual(run(), 42)", content)

    def test_unrepairable_class_burns_no_attempts(self) -> None:
        failure = FailureRecord(
            failure_class=FailureClass.USER_DECISION_REQUIRED,
            task_id="T-001",
            detail="needs approval from the human",
            attempt=1,
        )
        engine = RepairEngine(self.root)
        result = engine.attempt(_task(), failure, {})
        self.assertFalse(result.repaired)
        self.assertEqual(result.attempts, 0)
        self.assertFalse(
            (self.root / ".mythis" / "repair_history.jsonl").exists())

    def test_adds_missing_third_party_requirement(self) -> None:
        failure = FailureRecord(
            failure_class=FailureClass.DEPENDENCY_FAILURE,
            task_id="T-002",
            detail="ModuleNotFoundError: No module named 'requests'",
            attempt=1,
        )
        engine = RepairEngine(self.root)
        result = engine.attempt(_task("T-002"), failure, {})
        # requirements.txt did not exist: the strategy creates it, but
        # the fixture's test suite still fails on the NameError, so the
        # repair is not "repaired" — yet the bounded patch was applied.
        self.assertIsNotNone(result.patch)
        req = self.root / "requirements.txt"
        self.assertTrue(req.is_file())
        self.assertIn("requests", req.read_text(encoding="utf-8"))

    def test_select_hint_is_data_driven(self) -> None:
        hint, strategy = select_hint(
            FailureClass.IMPLEMENTATION_ERROR,
            "NameError: name 'helper' is not defined")
        self.assertEqual(strategy, "add_missing_import")
        self.assertIn("helper", hint)
        hint2, strategy2 = select_hint(
            FailureClass.DEPENDENCY_FAILURE,
            "ModuleNotFoundError: No module named 'requests'")
        self.assertEqual(strategy2, "add_requirement")
        self.assertIn("requests", hint2)


class PreRepairSnapshotTest(unittest.TestCase):
    """The mend must be preceded by a git snapshot of the tree."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _write(self.root / "app.py",
               "def run():\n    return helper()\n")
        _write(self.root / "util.py",
               "def helper():\n    return 42\n")
        _write(self.root / "tests" / "test_app.py",
               "import os\n"
               "import sys\n"
               "import unittest\n"
               "sys.path.insert(0, os.path.dirname(os.path.dirname(\n"
               "    os.path.abspath(__file__))))\n"
               "from app import run\n\n\n"
               "class TestApp(unittest.TestCase):\n"
               "    def test_run(self):\n"
               "        self.assertEqual(run(), 42)\n")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args], cwd=str(self.root),
            capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def _history(self):
        history_path = self.root / ".mythis" / "repair_history.jsonl"
        return [json.loads(line) for line in
                history_path.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    def test_snapshot_ref_is_a_real_git_ref(self) -> None:
        # A git repo with the fixture committed; the tree is dirty so
        # the snapshot has something to commit.
        self._git("init")
        # The checkpointer commits: give the repo a git identity.
        self._git("config", "user.email", "forge@test")
        self._git("config", "user.name", "forge-test")
        self._git("add", "-A")
        self._git("-c", "user.email=t@t", "-c", "user.name=t",
                  "commit", "-m", "fixture")
        _write(self.root / "scratch.txt", "uncommitted work\n")

        engine = RepairEngine(self.root)
        result = engine.attempt(_task(), _name_error_failure(), {})
        self.assertTrue(result.repaired, "the NameError should be repaired")

        records = self._history()
        self.assertEqual(len(records), 1)
        snapshot_ref = records[0].get("snapshot_ref")
        self.assertIsNotNone(snapshot_ref,
                             "attempt history must carry the snapshot ref")
        # It must be a real commit created by the checkpointer.
        self.assertEqual(
            self._git("cat-file", "-t", snapshot_ref), "commit")
        self.assertIn("pre-repair snapshot",
                      self._git("log", "-1", "--format=%B", snapshot_ref))
        self.assertIn("Forge-Task: T-001",
                      self._git("log", "-1", "--format=%B", snapshot_ref))
        # The repair applied its patch without committing, so HEAD is
        # still the snapshot commit.
        self.assertEqual(self._git("rev-parse", "HEAD"), snapshot_ref)

    def test_snapshot_none_outside_git_and_repair_proceeds(self) -> None:
        # Not a git repository: the snapshot degrades to None and the
        # repair must proceed normally anyway.
        engine = RepairEngine(self.root)
        result = engine.attempt(_task(), _name_error_failure(), {})
        self.assertTrue(result.repaired,
                        "repair must proceed without a git repo")
        records = self._history()
        self.assertEqual(len(records), 1)
        self.assertIsNone(records[0].get("snapshot_ref"))


class PatchBoundsTest(unittest.TestCase):
    """The Forge mends with a needle, not a broadsword."""

    def test_too_many_files_refused(self) -> None:
        parts = []
        for i in range(4):
            parts.append(
                f"--- a/f{i}.py\n+++ b/f{i}.py\n"
                f"@@ -1 +1 @@\n-X = {i}\n+X = {i + 1}\n")
        ok, reason = _within_bounds("".join(parts))
        self.assertFalse(ok)
        self.assertIn("4 files", reason)

    def test_too_many_lines_refused(self) -> None:
        body = "".join(f"-old{i}\n+new{i}\n" for i in range(30))
        patch = f"--- a/big.py\n+++ b/big.py\n@@ -1,30 +1,30 @@\n{body}"
        ok, reason = _within_bounds(patch)
        self.assertFalse(ok)
        self.assertIn("60 lines", reason)

    def test_test_file_refused(self) -> None:
        patch = ("--- a/tests/test_x.py\n+++ b/tests/test_x.py\n"
                 "@@ -1 +1 @@\n-X = 1\n+X = 2\n")
        ok, reason = _within_bounds(patch)
        self.assertFalse(ok)
        self.assertIn("test file", reason)

    def test_small_patch_accepted(self) -> None:
        patch = ("--- a/app.py\n+++ b/app.py\n"
                 "@@ -1,2 +1,3 @@\n"
                 "+from util import helper\n"
                 " def run():\n"
                 "     return helper()\n")
        ok, reason = _within_bounds(patch)
        self.assertTrue(ok, reason)


if __name__ == "__main__":
    unittest.main()
