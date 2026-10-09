"""Slice 12 tests: Architect role."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from draupnir_forge.roles.architect import (
    Architect,
    Architecture,
    Domain,
    Interface,
)
from draupnir_forge.roles.base import RoleContext, registry


def _sample_project(root: Path) -> dict:
    """Build a tiny two-domain sample project; return a domain_map."""
    alpha = root / "alpha"
    beta = root / "beta"
    alpha.mkdir()
    beta.mkdir()
    (alpha / "__init__.py").write_text("", encoding="utf-8")
    (alpha / "core.py").write_text(
        "def build():\n    return True\n\n"
        "class Engine:\n    pass\n\n"
        "def _secret():\n    return False\n",
        encoding="utf-8",
    )
    (alpha / "broken.py").write_text("def oops(:\n", encoding="utf-8")
    (alpha / "notes.txt").write_text("not python", encoding="utf-8")
    (beta / "__init__.py").write_text("", encoding="utf-8")
    (beta / "worker.py").write_text(
        "from alpha.core import build\n\ndef run():\n    return build()\n",
        encoding="utf-8",
    )
    return {
        "alpha": {
            "owner": "worker",
            "files": [str(alpha / "core.py"), str(alpha / "broken.py")],
        },
        "beta": {"owner": "worker", "files": [str(beta / "worker.py")]},
    }


class TestDesign(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.domain_map = _sample_project(self.root)
        self.role = Architect()

    def tearDown(self):
        self.tmp.cleanup()

    def test_design_builds_domains(self):
        arch = self.role.design({"name": "Sample"}, self.domain_map,
                                project_dir=str(self.root))
        self.assertIsInstance(arch, Architecture)
        self.assertEqual([d.name for d in arch.domains], ["alpha", "beta"])
        self.assertEqual(arch.domains[0].owner, "worker")

    def test_interfaces_only_public_names(self):
        arch = self.role.design({}, self.domain_map, project_dir=str(self.root))
        by_module = {i.module: i.functions for i in arch.interfaces}
        core = by_module.get("alpha.core")
        self.assertIsNotNone(core)
        self.assertIn("build", core)
        self.assertIn("Engine", core)
        self.assertNotIn("_secret", core)
        # Unparseable and non-python files yield no interface entry.
        self.assertFalse(any(m.endswith("broken") for m in by_module))
        self.assertFalse(any(m.endswith("notes") for m in by_module))

    def test_invariants_seeded_and_generated(self):
        arch = self.role.design({}, self.domain_map, project_dir=str(self.root))
        self.assertTrue(arch.invariants)
        self.assertTrue(
            any("event log is append-only" in inv for inv in arch.invariants),
            "seeded defaults must be present",
        )
        self.assertTrue(
            any("domain 'alpha' owns files" in inv for inv in arch.invariants),
            "generated per-domain invariants must be present",
        )

    def test_dependency_direction_from_real_imports(self):
        arch = self.role.design({}, self.domain_map, project_dir=str(self.root))
        self.assertEqual(arch.dependency_direction["beta"], ["alpha"])
        self.assertEqual(arch.dependency_direction["alpha"], [])

    def test_empty_domain_map_rejected(self):
        with self.assertRaises(ValueError):
            self.role.design({}, {})

    def test_roundtrip(self):
        arch = self.role.design({}, self.domain_map, project_dir=str(self.root))
        clone = Architecture.from_dict(json.loads(json.dumps(arch.to_dict())))
        self.assertEqual(clone.to_dict(), arch.to_dict())

    def test_dataclass_roundtrips(self):
        d = Domain.from_dict(Domain("x", "worker", ["a.py"]).to_dict())
        self.assertEqual(d.name, "x")
        i = Interface.from_dict(Interface("m", ["f"]).to_dict())
        self.assertEqual(i.functions, ["f"])


class TestRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.domain_map = _sample_project(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def _ctx(self, artifacts):
        return RoleContext(project_dir=str(self.root), artifacts=artifacts)

    def test_run_writes_mythis_docs(self):
        role = Architect()
        result = role.run(
            self._ctx({"vision": {"name": "Sample"},
                       "domain_map": self.domain_map})
        )
        self.assertTrue(result.ok, result.summary)
        mythis = self.root / ".mythis"
        for name in ("ARCHITECTURE.md", "INTERFACES.md",
                     "INVARIANTS.md", "architecture.json"):
            self.assertTrue((mythis / name).is_file(), name)
        data = json.loads((mythis / "architecture.json").read_text("utf-8"))
        self.assertEqual(len(data["domains"]), 2)
        self.assertTrue(data["invariants"])
        self.assertIn("architecture", result.artifacts)
        self.assertTrue(result.artifacts["architecture_path"].endswith("architecture.json"))

    def test_run_derives_domains_from_directories(self):
        role = Architect()
        result = role.run(self._ctx({"vision": {"name": "Sample"}}))
        self.assertTrue(result.ok, result.summary)
        names = {d["name"] for d in result.artifacts["architecture"]["domains"]}
        self.assertEqual(names, {"alpha", "beta"})

    def test_run_fails_gracefully_on_empty_project(self):
        empty = self.root / "empty"
        empty.mkdir()
        role = Architect()
        result = role.run(RoleContext(project_dir=str(empty), artifacts={}))
        self.assertFalse(result.ok)

    def test_registered(self):
        self.assertIsInstance(registry.create("architect"), Architect)


if __name__ == "__main__":
    unittest.main()
