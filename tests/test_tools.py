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

    def test_output_truncated_at_default_cap(self):
        # Default cap is tools.max_output_bytes = 1 MiB: printing 2 MiB
        # must truncate with a marker and set truncated=True.
        result = self.executor.run(
            [sys.executable, "-c", "print('x' * 2000000)"]
        )
        cap = 1048576
        marker = "\n[truncated 951425 bytes]"  # 2000001 - 1048576
        self.assertTrue(result.truncated)
        self.assertIn("[truncated ", result.stdout)
        self.assertTrue(result.stdout.endswith(marker))
        self.assertEqual(result.stdout, "x" * cap + marker)
        self.assertLessEqual(len(result.stdout), cap + len(marker))

    def test_small_output_not_truncated(self):
        result = self.executor.run(["echo", "skol"])
        self.assertFalse(result.truncated)
        self.assertNotIn("[truncated ", result.stdout)
        self.assertNotIn("[truncated ", result.stderr)

    def test_output_cap_from_config(self):
        executor = ToolExecutor(
            self.root, _config(**{"tools.max_output_bytes": 100})
        )
        result = executor.run(
            [sys.executable, "-c", "print('x' * 500)"]
        )
        marker = "\n[truncated 401 bytes]"  # 501 - 100
        self.assertTrue(result.truncated)
        self.assertEqual(result.stdout, "x" * 100 + marker)

    def test_stderr_truncated_independently(self):
        result = self.executor.run(
            [sys.executable, "-c",
             "import sys; sys.stdout.write('ok'); "
             "sys.stderr.write('e' * 2000000)"]
        )
        self.assertTrue(result.truncated)
        self.assertEqual(result.stdout, "ok")
        self.assertNotIn("[truncated ", result.stdout)
        self.assertIn("[truncated ", result.stderr)
        self.assertTrue(result.stderr.startswith("e" * 1048576))

    def test_bad_cap_config_falls_back_to_default(self):
        class StubConfig:
            def get(self, key, default=None):
                return {"tools.max_output_bytes": "banana",
                        "tools.allow_exec": True}.get(key, default)

        executor = ToolExecutor(self.root, StubConfig())
        # The bogus value must not crash the run; the 1 MiB default cap
        # applies, so 2 MiB of output is still truncated.
        result = executor.run(
            [sys.executable, "-c", "print('x' * 2000000)"]
        )
        self.assertTrue(result.truncated)
        self.assertIn("[truncated 951425 bytes]", result.stdout)

    def test_negative_cap_config_falls_back_to_default(self):
        class StubConfig:
            def get(self, key, default=None):
                return {"tools.max_output_bytes": -5,
                        "tools.allow_exec": True}.get(key, default)

        executor = ToolExecutor(self.root, StubConfig())
        result = executor.run(["echo", "skol"])
        self.assertFalse(result.truncated)
        self.assertIn("skol", result.stdout)

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

    def test_dry_run_passes_through_to_worker_and_changes_nothing(self):
        """The tools.py entry point accepts dry_run and passes it through;
        a valid dry-run patch reports paths while the tree stays intact."""
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
        changed = apply_patch(self.root, patch, dry_run=True)
        self.assertEqual(changed, ["a.txt"])
        self.assertEqual(self._read("a.txt"), "one\ntwo\nthree\n")

    def test_dry_run_malformed_raises_through_tools(self):
        with self.assertRaises(PatchError):
            apply_patch(self.root, "not a diff\n", dry_run=True)
        # Tree untouched.
        self.assertEqual(os.listdir(self.root), [])


if __name__ == "__main__":
    unittest.main()
