"""Tests for the Tool executor (slice 24)."""

import os
import shutil
import sys
import tempfile
import time
import unittest

from draupnir_forge.config import ForgeConfig
from draupnir_forge.tools import (
    AuthorityDenied,
    PatchError,
    ToolExecutor,
    ToolResult,
    apply_patch,
)


def _config(**extra):
    overrides = {
        "tools.allow_exec": True,
        "tools.allow_install": False,
        "tools.allow_network": False,
        "tools.allow_destructive": False,
        "tools.default_timeout_s": 60,
    }
    overrides.update(extra)
    return ForgeConfig.load(overrides=overrides)


class ToolExecutorTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="forge-tools-")
        self.executor = ToolExecutor(self.root, _config())

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_run_echo_captures_output(self):
        result = self.executor.run(["echo", "skol"])
        self.assertIsInstance(result, ToolResult)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("skol", result.stdout)
        self.assertFalse(result.timed_out)
        self.assertGreaterEqual(result.duration_s, 0.0)

    def test_run_nonzero_exit(self):
        result = self.executor.run(
            [sys.executable, "-c", "import sys; sys.exit(3)"]
        )
        self.assertEqual(result.exit_code, 3)
        self.assertFalse(result.timed_out)

    def test_cwd_pinned_to_project_dir(self):
        result = self.executor.run(
            [sys.executable, "-c", "import os; print(os.getcwd())"]
        )
        self.assertEqual(result.stdout.strip(), os.path.realpath(self.root))

    def test_destructive_denied_by_default(self):
        with self.assertRaises(AuthorityDenied):
            self.executor.run(["echo", "hi"], kind="destructive")

    def test_network_denied_by_default(self):
        with self.assertRaises(AuthorityDenied):
            self.executor.run(["echo", "hi"], kind="network")

    def test_install_denied_by_default(self):
        with self.assertRaises(AuthorityDenied):
            self.executor.run(["echo", "hi"], kind="install")

    def test_allowed_when_config_enables_it(self):
        executor = ToolExecutor(
            self.root, _config(**{"tools.allow_network": True})
        )
        result = executor.run(["echo", "hi"], kind="network")
        self.assertEqual(result.exit_code, 0)

    def test_denied_before_anything_spawns(self):
        # A denied kind must raise even when the binary does not exist.
        with self.assertRaises(AuthorityDenied):
            self.executor.run(["no-such-binary-xyz"], kind="destructive")

    def test_unknown_kind_rejected(self):
        with self.assertRaises(ValueError):
            self.executor.run(["echo", "hi"], kind="teleport")

    def test_cmd_must_be_a_list(self):
        with self.assertRaises(ValueError):
            self.executor.run("echo hi")
        with self.assertRaises(ValueError):
            self.executor.run([])
        with self.assertRaises(ValueError):
            self.executor.run(["echo", 42])

    def test_missing_binary_returns_127_not_crash(self):
        result = self.executor.run(["no-such-binary-xyz-123"])
        self.assertEqual(result.exit_code, 127)
        self.assertFalse(result.timed_out)

    def test_timeout_kills_process(self):
        start = time.monotonic()
        result = self.executor.run(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            timeout=1,
        )
        elapsed = time.monotonic() - start
        self.assertTrue(result.timed_out)
        self.assertLess(elapsed, 20)

    def test_output_capped_at_100kb(self):
        result = self.executor.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.stdout.write('x' * 300000)",
            ]
        )
        self.assertEqual(len(result.stdout), 100 * 1024)

    def test_kind_exec_default_allowed(self):
        result = self.executor.run(["echo", "ok"], kind="exec")
        self.assertEqual(result.exit_code, 0)

    def test_read_write_kinds_always_allowed(self):
        result = self.executor.run(["echo", "r"], kind="read")
        self.assertEqual(result.exit_code, 0)
        result = self.executor.run(["echo", "w"], kind="write")
        self.assertEqual(result.exit_code, 0)


class ApplyPatchTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="forge-patch-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _read(self, name):
        with open(os.path.join(self.root, name), encoding="utf-8") as handle:
            return handle.read()

    def test_new_file(self):
        patch = (
            "--- /dev/null\n"
            "+++ b/new.txt\n"
            "@@ -0,0 +1,2 @@\n"
            "+line one\n"
            "+line two\n"
        )
        changed = apply_patch(self.root, patch)
        self.assertEqual(changed, ["new.txt"])
        self.assertEqual(self._read("new.txt"), "line one\nline two\n")

    def test_modify_existing(self):
        _path = os.path.join(self.root, "a.txt")
        with open(_path, "w", encoding="utf-8") as handle:
            handle.write("one\ntwo\nthree\n")
        patch = (
            "--- a/a.txt\n"
            "+++ b/a.txt\n"
            "@@ -1,3 +1,3 @@\n"
            " one\n"
            "-two\n"
            "+TWO\n"
            " three\n"
        )
        changed = apply_patch(self.root, patch)
        self.assertEqual(changed, ["a.txt"])
        self.assertEqual(self._read("a.txt"), "one\nTWO\nthree\n")

    def test_delete_file(self):
        _path = os.path.join(self.root, "gone.txt")
        with open(_path, "w", encoding="utf-8") as handle:
            handle.write("bye\n")
        patch = "--- a/gone.txt\n+++ /dev/null\n@@ -1 +0,0 @@\n-bye\n"
        changed = apply_patch(self.root, patch)
        self.assertEqual(changed, ["gone.txt"])
        self.assertFalse(os.path.exists(_path))

    def test_malformed_patch_raises(self):
        with self.assertRaises(PatchError):
            apply_patch(self.root, "this is not a diff\n")
        with self.assertRaises(PatchError):
            apply_patch(self.root, "")

    def test_traversal_raises(self):
        patch = (
            "--- /dev/null\n"
            "+++ b/../escape.txt\n"
            "@@ -0,0 +1 @@\n"
            "+evil\n"
        )
        with self.assertRaises(PatchError):
            apply_patch(self.root, patch)

    def test_patch_error_is_shared_with_worker(self):
        from draupnir_forge.roles.worker import PatchError as WorkerPatchError

        self.assertIs(PatchError, WorkerPatchError)


if __name__ == "__main__":
    unittest.main()
