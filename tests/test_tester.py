"""Slice 16 tests: Tester role — discovery, parsing, running, evidence."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from draupnir_forge.failures import FailureClass
from draupnir_forge.roles import tester
from draupnir_forge.roles.base import RoleContext, registry
from draupnir_forge.roles.tester import (
    TestResult,
    detect_command,
    parse_test_output,
    run_tests,
)


PYTEST_Q_OUTPUT = """\
tests/test_alpha.py::test_one PASSED
tests/test_alpha.py::test_two FAILED
F
=================================== FAILURES ===================================
_________________________ test_two _________________________
assert 1 == 2
=========================== short test summary info ============================
FAILED tests/test_alpha.py::test_two - assert 1 == 2
================== 1 failed, 2 passed, 1 skipped in 0.42s ==================
"""

UNITTEST_FAIL_OUTPUT = """\
test_one (test_alpha.TestAlpha) ... ok
test_two (test_alpha.TestAlpha) ... FAIL
test_three (test_alpha.TestAlpha) ... ERROR

======================================================================
FAIL: test_two (test_alpha.TestAlpha)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "...", line 10, in test_two
    self.assertEqual(1, 2)
AssertionError: 1 != 2

======================================================================
ERROR: test_three (test_alpha.TestAlpha)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "...", line 14, in test_three
    raise RuntimeError("boom")
RuntimeError: boom

----------------------------------------------------------------------
Ran 3 tests in 0.001s

FAILED (failures=1, errors=1)
"""

UNITTEST_OK_OUTPUT = """\
...
----------------------------------------------------------------------
Ran 3 tests in 0.001s

OK
"""

UNITTEST_OK_SKIPPED_OUTPUT = """\
..s
----------------------------------------------------------------------
Ran 3 tests in 0.001s

OK (skipped=1)
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_fixture(tests_body: str) -> str:
    """Create a tmp project with a tests/ dir; returns the project path."""
    tmp = tempfile.mkdtemp(prefix="forge_tester_")
    _write(Path(tmp) / "tests" / "__init__.py", "")
    _write(Path(tmp) / "tests" / "test_sample.py", tests_body)
    return tmp


class TestDetectCommand(unittest.TestCase):
    def test_prefers_pytest_when_importable(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(Path(tmp) / "tests" / "__init__.py", "")
            with patch.object(tester, "_pytest_importable", return_value=True):
                cmd = detect_command(tmp)
        self.assertEqual(cmd, [sys.executable, "-m", "pytest", "-q"])

    def test_falls_back_to_unittest(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(Path(tmp) / "tests" / "__init__.py", "")
            with patch.object(tester, "_pytest_importable", return_value=False):
                cmd = detect_command(tmp)
        expected = [sys.executable, "-m", "unittest", "discover", "-s", "tests"]
        self.assertEqual(cmd, expected)

    def test_npm_when_package_json_has_test_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            pkg = {"scripts": {"test": "jest"}}
            _write(Path(tmp) / "package.json", json.dumps(pkg))
            with patch.object(tester, "_pytest_importable", return_value=False):
                with patch(
                    "draupnir_forge.roles.tester.shutil.which",
                    return_value="/usr/bin/npm",
                ):
                    cmd = detect_command(tmp)
        self.assertEqual(cmd, ["npm", "test"])

    def test_none_when_nothing_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(tester, "_pytest_importable", return_value=False):
                self.assertIsNone(detect_command(tmp))

    def test_npm_without_test_script_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            pkg = {"scripts": {"build": "tsc"}}
            _write(Path(tmp) / "package.json", json.dumps(pkg))
            with patch.object(tester, "_pytest_importable", return_value=False):
                self.assertIsNone(detect_command(tmp))


class TestParsePytestOutput(unittest.TestCase):
    def test_counts(self):
        counts, failures, errors = parse_test_output(PYTEST_Q_OUTPUT, ["pytest", "-q"])
        self.assertEqual(counts["failed"], 1)
        self.assertEqual(counts["passed"], 2)
        self.assertEqual(counts["skipped"], 1)
        self.assertEqual(counts["errors"], 0)

    def test_failure_blocks(self):
        counts, failures, errors = parse_test_output(PYTEST_Q_OUTPUT, ["pytest", "-q"])
        self.assertEqual(len(failures), 1)
        self.assertIn("test_two", failures[0])
        self.assertEqual(errors, [])

    def test_no_summary_means_zeros(self):
        counts, failures, errors = parse_test_output("weird output", ["pytest", "-q"])
        self.assertEqual(counts, {"passed": 0, "failed": 0, "errors": 0, "skipped": 0})


class TestParseUnittestOutput(unittest.TestCase):
    def test_failure_and_error_counts(self):
        counts, failures, errors = parse_test_output(
            UNITTEST_FAIL_OUTPUT, ["x", "-m", "unittest"]
        )
        self.assertEqual(counts["failed"], 1)
        self.assertEqual(counts["errors"], 1)
        self.assertEqual(counts["passed"], 1)

    def test_failure_blocks(self):
        counts, failures, errors = parse_test_output(
            UNITTEST_FAIL_OUTPUT, ["x", "-m", "unittest"]
        )
        self.assertEqual(len(failures), 1)
        self.assertIn("FAIL:", failures[0])
        self.assertEqual(len(errors), 1)
        self.assertIn("ERROR:", errors[0])

    def test_ok(self):
        counts, failures, errors = parse_test_output(
            UNITTEST_OK_OUTPUT, ["x", "-m", "unittest"]
        )
        self.assertEqual(counts["passed"], 3)
        self.assertEqual((counts["failed"], counts["errors"]), (0, 0))

    def test_ok_with_skipped(self):
        counts, failures, errors = parse_test_output(
            UNITTEST_OK_SKIPPED_OUTPUT, ["x", "-m", "unittest"]
        )
        self.assertEqual(counts["skipped"], 1)
        self.assertEqual(counts["passed"], 2)


GOOD_BODY = """\
import unittest


class TestSample(unittest.TestCase):
    def test_one(self):
        self.assertEqual(1, 1)

    def test_two(self):
        self.assertTrue(True)
"""

BAD_BODY = """\
import unittest


class TestSample(unittest.TestCase):
    def test_one(self):
        self.assertEqual(1, 1)

    def test_two(self):
        self.assertEqual(1, 2, "deliberate failure")
"""


class TestRunTests(unittest.TestCase):
    def test_green_fixture(self):
        tmp = _make_fixture(GOOD_BODY)
        with patch.object(tester, "_pytest_importable", return_value=False):
            result = run_tests(tmp, timeout=120)
        self.assertIsInstance(result, TestResult)
        self.assertEqual(result.failed, 0)
        self.assertEqual(result.errors, 0)
        self.assertEqual(result.passed, 2)
        self.assertTrue(result.ok)

    def test_red_fixture_classifies_failures(self):
        tmp = _make_fixture(BAD_BODY)
        with patch.object(tester, "_pytest_importable", return_value=False):
            result = run_tests(tmp, timeout=120)
        self.assertEqual(result.failed, 1)
        self.assertEqual(result.passed, 1)
        self.assertFalse(result.ok)
        self.assertEqual(len(result.failures), 1)
        self.assertIsInstance(result.failures[0], FailureClass)

    def test_raw_output_is_tailed(self):
        tmp = _make_fixture(GOOD_BODY)
        with patch.object(tester, "_pytest_importable", return_value=False):
            result = run_tests(tmp, timeout=120)
        self.assertLessEqual(len(result.raw_output), tester.RAW_OUTPUT_TAIL_CHARS)

    def test_evidence_log_written(self):
        tmp = _make_fixture(GOOD_BODY)
        with patch.object(tester, "_pytest_importable", return_value=False):
            run_tests(tmp, timeout=120)
        evidence = Path(tmp) / ".mythis" / "evidence" / "tests"
        logs = list(evidence.glob("*.log"))
        self.assertEqual(len(logs), 1)
        text = logs[0].read_text(encoding="utf-8")
        self.assertIn("Ran 2 tests", text)

    def test_no_command_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(tester, "_pytest_importable", return_value=False):
                result = run_tests(tmp, timeout=30)
        self.assertEqual(result.command, [])
        self.assertEqual(result.total, 0)

    def test_timeout_is_survived(self):
        body = (
            "import time\nimport unittest\n\n\n"
            "class T(unittest.TestCase):\n"
            "    def test_hang(self):\n"
            "        time.sleep(60)\n"
        )
        tmp = _make_fixture(body)
        with patch.object(tester, "_pytest_importable", return_value=False):
            result = run_tests(tmp, timeout=2)
        self.assertIn("TIMEOUT", result.raw_output)
        self.assertGreaterEqual(result.duration_s, 2.0)


class TestTesterRole(unittest.TestCase):
    def test_registered(self):
        self.assertIn("tester", registry.names())
        self.assertIsInstance(registry.create("tester"), tester.Tester)

    def test_run_green(self):
        tmp = _make_fixture(GOOD_BODY)
        with patch.object(tester, "_pytest_importable", return_value=False):
            result = tester.Tester().run(RoleContext(project_dir=tmp))
        self.assertTrue(result.ok)
        self.assertIn("test_result", result.artifacts)
        self.assertIsInstance(result.artifacts["test_result"], TestResult)

    def test_run_red(self):
        tmp = _make_fixture(BAD_BODY)
        with patch.object(tester, "_pytest_importable", return_value=False):
            result = tester.Tester().run(RoleContext(project_dir=tmp))
        self.assertFalse(result.ok)
        self.assertIn("failed", result.summary)

    def test_run_never_raises(self):
        result = tester.Tester().run(RoleContext(project_dir="/nonexistent-dir-xyz"))
        self.assertIsInstance(result.summary, str)


if __name__ == "__main__":
    unittest.main()
