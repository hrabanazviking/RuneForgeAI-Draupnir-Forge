"""Slice 14 tests: ForgeWorker patch application, guardrails, commands."""

from __future__ import annotations

import os
import tempfile
import textwrap
import unittest

from draupnir_forge.roles.base import RoleContext, registry
from draupnir_forge.roles.worker import ForgeWorker, PatchError, apply_patch


def _write(root: str, rel: str, content: str) -> str:
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


def _read(root: str, rel: str) -> str:
    with open(os.path.join(root, rel), encoding="utf-8") as fh:
        return fh.read()


class _FakeTask:
    def __init__(self, task_id: str = "t1", goal: str = "do the thing"):
        self.task_id = task_id
        self.goal = goal


def _ctx(project_dir: str, artifacts: dict) -> RoleContext:
    return RoleContext(project_dir=project_dir, task=_FakeTask(),
                       artifacts=artifacts)


class TestApplyPatch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def test_modify_multiple_hunks(self):
        _write(self.root, "a.txt",
               "line1\nline2\nline3\nline4\nline5\nline6\nline7\nline8\n")
        patch = textwrap.dedent("""\
            --- a/a.txt
            +++ b/a.txt
            @@ -1,3 +1,3 @@
             line1
            -line2
            +LINE2
             line3
            @@ -6,3 +6,3 @@
             line6
            -line7
            +LINE7
             line8
            """)
        changed = apply_patch(self.root, patch)
        self.assertEqual(changed, ["a.txt"])
        self.assertEqual(
            _read(self.root, "a.txt"),
            "line1\nLINE2\nline3\nline4\nline5\nline6\nLINE7\nline8\n")

    def test_new_file(self):
        patch = textwrap.dedent("""\
            --- /dev/null
            +++ b/newdir/hello.txt
            @@ -0,0 +1,2 @@
            +hello
            +world
            """)
        changed = apply_patch(self.root, patch)
        self.assertEqual(changed, ["newdir/hello.txt"])
        self.assertEqual(_read(self.root, "newdir/hello.txt"),
                         "hello\nworld\n")

    def test_delete_file(self):
        _write(self.root, "gone.txt", "bye\n")
        patch = textwrap.dedent("""\
            --- a/gone.txt
            +++ /dev/null
            @@ -1,1 +0,0 @@
            -bye
            """)
        changed = apply_patch(self.root, patch)
        self.assertEqual(changed, ["gone.txt"])
        self.assertFalse(os.path.exists(os.path.join(self.root, "gone.txt")))

    def test_context_mismatch_raises(self):
        _write(self.root, "a.txt", "alpha\nbeta\n")
        patch = textwrap.dedent("""\
            --- a/a.txt
            +++ b/a.txt
            @@ -1,2 +1,2 @@
             alpha
            -GAMMA
            +delta
            """)
        with self.assertRaises(PatchError):
            apply_patch(self.root, patch)
        # Tree untouched after failure.
        self.assertEqual(_read(self.root, "a.txt"), "alpha\nbeta\n")

    def test_malformed_patch_raises(self):
        with self.assertRaises(PatchError):
            apply_patch(self.root, "this is not a diff\n")
        with self.assertRaises(PatchError):
            apply_patch(self.root, "")
        # Hunk body contradicting its @@ counts.
        _write(self.root, "a.txt", "x\n")
        bad = textwrap.dedent("""\
            --- a/a.txt
            +++ b/a.txt
            @@ -1,2 +1,2 @@
             x
            """)
        with self.assertRaises(PatchError):
            apply_patch(self.root, bad)

    def test_no_trailing_newline(self):
        _write(self.root, "a.txt", "one\ntwo")
        patch = textwrap.dedent("""\
            --- a/a.txt
            +++ b/a.txt
            @@ -1,2 +1,2 @@
             one
            -two
            +TWO
            \\ No newline at end of file
            """)
        apply_patch(self.root, patch)
        self.assertEqual(_read(self.root, "a.txt"), "one\nTWO")

    def test_path_traversal_raises(self):
        patch = textwrap.dedent("""\
            --- /dev/null
            +++ b/../../evil.txt
            @@ -0,0 +1,1 @@
            +evil
            """)
        with self.assertRaises(PatchError):
            apply_patch(self.root, patch)

    def test_absolute_path_raises(self):
        patch = textwrap.dedent("""\
            --- /dev/null
            +++ /etc/evil.txt
            @@ -0,0 +1,1 @@
            +evil
            """)
        with self.assertRaises(PatchError):
            apply_patch(self.root, patch)


class TestForgeWorkerRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.addCleanup(self.tmp.cleanup)
        self.worker = ForgeWorker()

    def _patch(self) -> str:
        return textwrap.dedent("""\
            --- /dev/null
            +++ b/src/app.py
            @@ -0,0 +1,2 @@
            +X = 1
            +Y = 2
            """)

    def test_run_applies_patch_and_writes_evidence(self):
        # Evidence file is named after ctx.task.task_id ("t1" here).
        ctx = _ctx(self.root, {"patch": self._patch()})
        result = self.worker.run(ctx)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(result.artifacts["changed_files"], ["src/app.py"])
        evidence = os.path.join(self.root, ".mythis", "evidence",
                                "diffs", "t1.diff")
        self.assertTrue(os.path.isfile(evidence))
        with open(evidence, encoding="utf-8") as fh:
            self.assertIn("+++ b/src/app.py", fh.read())
        self.assertEqual(_read(self.root, "src/app.py"), "X = 1\nY = 2\n")

    def test_run_refuses_path_traversal(self):
        patch = textwrap.dedent("""\
            --- /dev/null
            +++ b/../escape.txt
            @@ -0,0 +1,1 @@
            +x
            """)
        result = self.worker.run(_ctx(self.root, {"patch": patch}))
        self.assertFalse(result.ok)
        self.assertIn("refused", result.summary.lower())

    def test_run_refuses_mythis(self):
        patch = textwrap.dedent("""\
            --- /dev/null
            +++ b/.mythis/canon.md
            @@ -0,0 +1,1 @@
            +forged canon
            """)
        result = self.worker.run(_ctx(self.root, {"patch": patch}))
        self.assertFalse(result.ok)
        self.assertIn(".mythis", result.summary)

    def test_run_refuses_missing_goal(self):
        task = _FakeTask(goal="")
        ctx = RoleContext(project_dir=self.root, task=task,
                          artifacts={"patch": self._patch()})
        result = self.worker.run(ctx)
        self.assertFalse(result.ok)

    def test_run_refuses_missing_patch(self):
        result = self.worker.run(_ctx(self.root, {}))
        self.assertFalse(result.ok)

    def test_run_commands_success(self):
        ctx = _ctx(self.root, {"patch": self._patch(),
                               "commands": ["echo hello-forge"]})
        result = self.worker.run(ctx)
        self.assertTrue(result.ok, result.summary)
        outputs = result.artifacts["command_outputs"]
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0]["returncode"], 0)
        self.assertIn("hello-forge", outputs[0]["stdout"])

    def test_run_commands_stop_on_failure(self):
        ctx = _ctx(self.root, {
            "patch": self._patch(),
            "commands": ["echo first", "exit 3", "echo never-runs"],
        })
        result = self.worker.run(ctx)
        self.assertFalse(result.ok)
        outputs = result.artifacts["command_outputs"]
        self.assertEqual(len(outputs), 2)  # stopped after the failure
        self.assertEqual(outputs[1]["returncode"], 3)
        self.assertIn("exit 3", result.summary)

    def test_run_bad_commands_type(self):
        ctx = _ctx(self.root, {"patch": self._patch(),
                               "commands": "not-a-list"})
        result = self.worker.run(ctx)
        self.assertFalse(result.ok)

    def test_registered(self):
        self.assertIsInstance(registry.create("forge_worker"), ForgeWorker)


if __name__ == "__main__":
    unittest.main()
