"""Slice 13 tests: Planner role and ForgeTask."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from draupnir_forge.roles.base import RoleContext, registry
from draupnir_forge.roles.planner import (
    ForgeTask,
    Planner,
    compile_roadmap,
    next_ready,
)
from draupnir_forge.tasks import ForgeTask as ReexportedForgeTask


def _arch(*domains: str) -> dict:
    return {"domains": [{"name": d, "owner": "worker", "files": []}
                        for d in domains]}


def _diamond_specs() -> list:
    return [
        {"title": "lay the keel", "domain": "core"},
        {"title": "raise the mast", "domain": "core", "depends_on": ["T-001"]},
        {"title": "weave the sail", "domain": "core", "depends_on": ["T-001"]},
        {"title": "launch the ship", "domain": "core",
         "depends_on": ["T-002", "T-003"]},
    ]


class TestCompileRoadmap(unittest.TestCase):
    def test_diamond_resolves_in_order(self):
        tasks = compile_roadmap(_arch("core"), {}, _diamond_specs())
        ids = [t.task_id for t in tasks]
        self.assertEqual(ids[0], "T-001")
        self.assertEqual(ids[-1], "T-004")
        self.assertLess(ids.index("T-001"), ids.index("T-002"))
        self.assertLess(ids.index("T-001"), ids.index("T-003"))
        self.assertLess(ids.index("T-002"), ids.index("T-004"))
        self.assertLess(ids.index("T-003"), ids.index("T-004"))

    def test_ids_assigned_and_statuses(self):
        tasks = compile_roadmap(_arch("core"), {}, _diamond_specs())
        by_id = {t.task_id: t for t in tasks}
        self.assertEqual(by_id["T-001"].status, "ready")
        self.assertEqual(by_id["T-002"].status, "blocked")
        self.assertEqual(by_id["T-002"].depends_on, ["T-001"])

    def test_index_style_deps_resolve(self):
        specs = [
            {"title": "first", "domain": "core"},
            {"title": "second", "domain": "core", "depends_on": [0]},
        ]
        tasks = compile_roadmap(_arch("core"), {}, specs)
        self.assertEqual(tasks[1].depends_on, ["T-001"])

    def test_cycle_detected(self):
        specs = [
            {"title": "a", "domain": "core", "depends_on": ["T-002"]},
            {"title": "b", "domain": "core", "depends_on": ["T-001"]},
        ]
        with self.assertRaises(ValueError):
            compile_roadmap(_arch("core"), {}, specs)

    def test_self_dependency_rejected(self):
        specs = [{"title": "a", "domain": "core", "depends_on": ["T-001"]}]
        with self.assertRaises(ValueError):
            compile_roadmap(_arch("core"), {}, specs)

    def test_unknown_dep_rejected(self):
        specs = [{"title": "a", "domain": "core", "depends_on": ["T-999"]}]
        with self.assertRaises(ValueError):
            compile_roadmap(_arch("core"), {}, specs)

    def test_unknown_domain_rejected(self):
        specs = [{"title": "a", "domain": "nope"}]
        with self.assertRaises(ValueError):
            compile_roadmap(_arch("core"), {}, specs)

    def test_empty_specs_rejected(self):
        with self.assertRaises(ValueError):
            compile_roadmap(_arch("core"), {}, [])

    def test_missing_title_rejected(self):
        with self.assertRaises(ValueError):
            compile_roadmap(_arch("core"), {}, [{"domain": "core"}])


class TestNextReady(unittest.TestCase):
    def test_first_ready_returned(self):
        tasks = compile_roadmap(_arch("core"), {}, _diamond_specs())
        ready = next_ready(tasks)
        self.assertIsNotNone(ready)
        self.assertEqual(ready.task_id, "T-001")

    def test_advances_as_deps_complete(self):
        tasks = compile_roadmap(_arch("core"), {}, _diamond_specs())
        by_id = {t.task_id: t for t in tasks}
        by_id["T-001"].status = "done"
        by_id["T-002"].status = "ready"
        by_id["T-003"].status = "ready"
        ready = next_ready(tasks)
        self.assertEqual(ready.task_id, "T-002")

    def test_stale_ready_skipped(self):
        tasks = compile_roadmap(_arch("core"), {}, _diamond_specs())
        by_id = {t.task_id: t for t in tasks}
        by_id["T-001"].status = "in_progress"
        # T-002 marked ready but its dep is not done: must be skipped.
        by_id["T-002"].status = "ready"
        self.assertIsNone(next_ready(tasks))

    def test_none_when_nothing_runnable(self):
        tasks = compile_roadmap(_arch("core"), {}, _diamond_specs())
        for t in tasks:
            t.status = "blocked"
        self.assertIsNone(next_ready(tasks))


class TestForgeTask(unittest.TestCase):
    def test_bad_status_rejected(self):
        with self.assertRaises(ValueError):
            ForgeTask(task_id="T-001", title="x", domain="core",
                      status="someday")

    def test_roundtrip(self):
        task = ForgeTask(
            task_id="T-007", title="carve runes", domain="core",
            status="in_progress", depends_on=["T-001"], goal="g",
            constraints=["c"], acceptance=["a"], verification=["v"],
        )
        clone = ForgeTask.from_dict(json.loads(json.dumps(task.to_dict())))
        self.assertEqual(clone.to_dict(), task.to_dict())

    def test_reexport_is_same_class(self):
        self.assertIs(ReexportedForgeTask, ForgeTask)


class TestPlannerRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_run_writes_roadmap(self):
        role = Planner()
        result = role.run(RoleContext(
            project_dir=str(self.root),
            artifacts={
                "vision": {"name": "Longship"},
                "architecture": _arch("core", "ui"),
                "task_specs": [
                    {"title": "lay the keel", "domain": "core",
                     "goal": "hull floats",
                     "acceptance": ["it floats"],
                     "verification": ["launch it"]},
                    {"title": "paint the sail", "domain": "ui",
                     "depends_on": ["T-001"]},
                ],
            },
        ))
        self.assertTrue(result.ok, result.summary)
        mythis = self.root / ".mythis"
        self.assertTrue((mythis / "roadmap.json").is_file())
        self.assertTrue((mythis / "ROADMAP.md").is_file())
        data = json.loads((mythis / "roadmap.json").read_text("utf-8"))
        self.assertEqual([t["task_id"] for t in data], ["T-001", "T-002"])
        md = (mythis / "ROADMAP.md").read_text("utf-8")
        self.assertIn("lay the keel", md)
        self.assertIn("| T-001 |", md)
        self.assertEqual(len(result.artifacts["tasks"]), 2)
        self.assertTrue(result.artifacts["roadmap_path"].endswith("roadmap.json"))

    def test_run_generates_starter_tasks(self):
        role = Planner()
        result = role.run(RoleContext(
            project_dir=str(self.root),
            artifacts={"architecture": _arch("core", "ui")},
        ))
        self.assertTrue(result.ok, result.summary)
        tasks = result.artifacts["tasks"]
        self.assertEqual(len(tasks), 3)
        titles = " ".join(t["title"] for t in tasks).lower()
        self.assertIn("scaffold", titles)
        self.assertIn("test", titles)
        self.assertIn("document", titles)
        self.assertEqual(
            [t["task_id"] for t in tasks], ["T-001", "T-002", "T-003"])
        self.assertEqual(tasks[1]["depends_on"], ["T-001"])
        self.assertEqual(tasks[2]["depends_on"], ["T-002"])
        # Starter tasks must be real: goals, acceptance, verification.
        for task in tasks:
            self.assertTrue(task["goal"], task["task_id"])
            self.assertTrue(task["acceptance"], task["task_id"])
            self.assertTrue(task["verification"], task["task_id"])

    def test_run_rejects_bad_specs_gracefully(self):
        role = Planner()
        result = role.run(RoleContext(
            project_dir=str(self.root),
            artifacts={
                "architecture": _arch("core"),
                "task_specs": [
                    {"title": "a", "domain": "core", "depends_on": ["T-002"]},
                    {"title": "b", "domain": "core", "depends_on": ["T-001"]},
                ],
            },
        ))
        self.assertFalse(result.ok)
        self.assertIn("cycle", result.summary.lower())

    def test_run_requires_architecture(self):
        role = Planner()
        result = role.run(RoleContext(project_dir=str(self.root),
                                      artifacts={}))
        self.assertFalse(result.ok)

    def test_registered(self):
        self.assertIsInstance(registry.create("planner"), Planner)


if __name__ == "__main__":
    unittest.main()
