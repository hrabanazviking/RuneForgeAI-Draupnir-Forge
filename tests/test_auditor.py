"""Slice 15 tests: Auditor checks against planted-violation fixtures."""

from __future__ import annotations

import json
import os
import tempfile
import textwrap
import unittest

from draupnir_forge.roles.auditor import (
    Auditor,
    Finding,
    audit,
    check_architecture_drift,
    load_check_config,
)
from draupnir_forge.roles.base import RoleContext, registry


def _write(root: str, rel: str, content: str) -> str:
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(textwrap.dedent(content))
    return path


class _FakeTask:
    task_id = "audit-1"
    goal = "review the diff"


def _ctx(project_dir: str, artifacts: dict) -> RoleContext:
    return RoleContext(project_dir=project_dir, task=_FakeTask(),
                       artifacts=artifacts)


# A fixture file stuffed with planted violations (except drift, which
# needs its own project layout).
VIOLATIONS = '''\
    api_key = "sk-live-abc123"
    password = "hunter2"

    try:
        risky()
    except:
        pass

    print("debug noise")

    # TODO: rewrite this whole saga
    # FIXME later

    def deep():
        if a:
            if b:
                for x in y:
                    while c:
                        if d:
                            print("too deep")
'''


class TestAuditorChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.addCleanup(self.tmp.cleanup)
        _write(self.root, "src/bad.py", VIOLATIONS)

    def _findings(self, rel: str = "src/bad.py"):
        return audit([rel], self.root)

    def _checks(self, rel: str = "src/bad.py"):
        return {f.check for f in self._findings(rel)}

    def test_hardcoded_secrets(self):
        found = [f for f in self._findings()
                 if f.check == "hardcoded_secrets"]
        self.assertEqual(len(found), 2)
        self.assertTrue(all(f.severity == "high" for f in found))
        self.assertEqual({f.line for f in found}, {1, 2})

    def test_bare_except(self):
        found = [f for f in self._findings() if f.check == "bare_except"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].line, 6)

    def test_print_in_library(self):
        found = [f for f in self._findings()
                 if f.check == "print_statements"]
        self.assertEqual({f.line for f in found}, {9, 20})

    def test_print_allowed_in_cli(self):
        _write(self.root, "src/cli.py", 'print("hello")\n')
        found = [f for f in audit(["src/cli.py"], self.root)
                 if f.check == "print_statements"]
        self.assertEqual(found, [])

    def test_todo_markers(self):
        found = [f for f in self._findings() if f.check == "todo_markers"]
        self.assertEqual(len(found), 2)
        self.assertTrue(all(f.severity == "low" for f in found))

    def test_nesting_depth(self):
        found = [f for f in self._findings() if f.check == "nesting_depth"]
        self.assertEqual(len(found), 1)
        self.assertIn("exceeds maximum 4", found[0].message)

    def test_clean_file_no_findings(self):
        _write(self.root, "src/clean.py",
               '"""A tidy module."""\n\n\ndef add(a, b):\n    return a + b\n')
        self.assertEqual(audit(["src/clean.py"], self.root), [])

    def test_bare_except_ignores_strings(self):
        _write(self.root, "src/str.py", 'x = "except:"\n')
        found = [f for f in audit(["src/str.py"], self.root)
                 if f.check == "bare_except"]
        self.assertEqual(found, [])

    def test_finding_severity_validated(self):
        with self.assertRaises(ValueError):
            Finding(severity="critical", check="x", file="f",
                    line=1, message="m")

    def test_finding_to_dict(self):
        f = Finding(severity="low", check="todo_markers", file="a.py",
                    line=3, message="m")
        d = f.to_dict()
        self.assertEqual(d, {"severity": "low", "check": "todo_markers",
                             "file": "a.py", "line": 3, "message": "m"})


class TestDuplicatedBlocks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def test_duplicated_six_line_block(self):
        block = "\n".join(f"    step{i} = compute({i})" for i in range(6))
        _write(self.root, "src/one.py", f"def f():\n{block}\n")
        _write(self.root, "src/two.py", f"def g():\n{block}\n")
        found = [f for f in audit(["src/one.py", "src/two.py"], self.root)
                 if f.check == "duplicated_blocks"]
        self.assertTrue(found)
        self.assertIn("src/one.py", found[0].message)

    def test_short_repetition_ignored(self):
        block = "\n".join(f"    step{i} = compute({i})" for i in range(5))
        _write(self.root, "src/one.py", f"def f():\n{block}\n")
        _write(self.root, "src/two.py", f"def g():\n{block}\n")
        found = [f for f in audit(["src/one.py", "src/two.py"], self.root)
                 if f.check == "duplicated_blocks"]
        self.assertEqual(found, [])


class TestArchitectureDrift(unittest.TestCase):
    ARCH = {
        "domains": {
            "cognition": {
                "paths": ["src/draupnir_forge/roles/"],
                "modules": ["draupnir_forge.roles"],
            },
            "core": {
                "paths": ["src/draupnir_forge/"],
                "modules": ["draupnir_forge"],
            },
        }
    }

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.addCleanup(self.tmp.cleanup)
        _write(self.root, "src/draupnir_forge/roles/scout.py",
               "from draupnir_forge.engine import Engine\n")
        _write(self.root, "src/draupnir_forge/engine.py",
               "from draupnir_forge.roles.worker import ForgeWorker\n")
        with open(os.path.join(self.root, "architecture.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(self.ARCH, fh)

    def test_drift_flagged(self):
        found = [f for f in audit(
            ["src/draupnir_forge/roles/scout.py"], self.root)
            if f.check == "architecture_drift"]
        self.assertEqual(len(found), 1)
        self.assertIn("cognition", found[0].message)
        self.assertIn("core", found[0].message)

    def test_no_drift_within_domain(self):
        _write(self.root, "src/draupnir_forge/roles/clean.py",
               "from draupnir_forge.roles.base import Role\n")
        found = [f for f in audit(
            ["src/draupnir_forge/roles/clean.py"], self.root)
            if f.check == "architecture_drift"]
        self.assertEqual(found, [])

    def test_missing_architecture_json_skips_gracefully(self):
        os.remove(os.path.join(self.root, "architecture.json"))
        found = audit(["src/draupnir_forge/roles/scout.py"], self.root)
        self.assertEqual([f for f in found
                          if f.check == "architecture_drift"], [])


class TestAuditorRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.addCleanup(self.tmp.cleanup)
        self.auditor = Auditor()

    def test_run_audits_changed_files(self):
        _write(self.root, "src/bad.py", VIOLATIONS)
        _write(self.root, "src/ok.py", "X = 1\n")
        ctx = _ctx(self.root, {"changed_files": ["src/bad.py", "src/ok.py"]})
        result = self.auditor.run(ctx)
        self.assertTrue(result.ok)  # findings are advisory
        findings = result.artifacts["findings"]
        self.assertTrue(any(f["check"] == "hardcoded_secrets"
                            for f in findings))
        files = {f["file"] for f in findings}
        self.assertNotIn("src/ok.py", files)

    def test_run_defaults_to_src_tree(self):
        _write(self.root, "src/bad.py", VIOLATIONS)
        ctx = _ctx(self.root, {})
        result = self.auditor.run(ctx)
        self.assertTrue(result.ok)
        self.assertTrue(result.artifacts["findings"])

    def test_run_never_raises(self):
        ctx = _ctx("/nonexistent-dir-xyz", {"changed_files": ["x.py"]})
        result = self.auditor.run(ctx)
        self.assertTrue(result.ok)
        self.assertEqual(result.artifacts["findings"], [])

    def test_registered(self):
        self.assertIsInstance(registry.create("auditor"), Auditor)

    def test_config_loads_from_yaml(self):
        config = load_check_config(self.root)
        self.assertIn("hardcoded_secrets", config["checks"])
        self.assertEqual(
            config["checks"]["nesting_depth"]["max_depth"], 4)

    def test_config_always_has_checks_shape(self):
        # Even for an unknown project dir the loader returns a usable
        # config (repo-level YAML or built-in defaults).
        config = load_check_config("/nonexistent-dir-xyz")
        self.assertIn("checks", config)
        self.assertIn("hardcoded_secrets", config["checks"])


if __name__ == "__main__":
    unittest.main()
