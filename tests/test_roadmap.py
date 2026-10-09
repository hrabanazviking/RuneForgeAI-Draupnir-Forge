"""Slice 22 tests: TaskGraph — the machine task graph."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from draupnir_forge.roadmap import TaskGraph
from draupnir_forge.tasks import ForgeTask


def _seed(project_dir: Path, tasks) -> Path:
    mythis = project_dir / ".mythis"
    mythis.mkdir(parents=True, exist_ok=True)
    (mythis / "roadmap.json").write_text(json.dumps(tasks), encoding="utf-8")
    return mythis


def _task(task_id, depends_on=(), status="ready"):
    return {
        "task_id": task_id, "title": f"Task {task_id}", "domain": "test",
        "status": status, "depends_on": list(depends_on),
        "goal": "g", "constraints": [], "acceptance": [], "verification": [],
    }


class TestLoad(unittest.TestCase):
    def test_missing_roadmap_loads_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph = TaskGraph(tmp)
            self.assertEqual(graph.task_ids(), [])
            self.assertEqual(graph.ready_tasks(), [])

    def test_corrupt_roadmap_heals_to_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            mythis = Path(tmp) / ".mythis"
            mythis.mkdir()
            (mythis / "roadmap.json").write_text("{not json", encoding="utf-8")
            graph = TaskGraph(tmp)
            self.assertEqual(graph.task_ids(), [])

    def test_loads_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed(Path(tmp), [_task("T-001"), _task("T-002", ["T-001"])])
            graph = TaskGraph(tmp)
            self.assertEqual(graph.task_ids(), ["T-001", "T-002"])
            self.assertIsInstance(graph.get("T-001"), ForgeTask)

    def test_get_unknown_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph = TaskGraph(tmp)
            with self.assertRaises(KeyError):
                graph.get("T-999")


class TestReadyTasks(unittest.TestCase):
    def test_ready_set_correct_after_completes(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed(Path(tmp), [
                _task("T-001"),
                _task("T-002", ["T-001"]),
                _task("T-003", ["T-002"]),
            ])
            graph = TaskGraph(tmp)
            self.assertEqual([t.task_id for t in graph.ready_tasks()], ["T-001"])
            graph.mark_complete("T-001")
            self.assertEqual([t.task_id for t in graph.ready_tasks()], ["T-002"])
            graph.mark_complete("T-002")
            self.assertEqual([t.task_id for t in graph.ready_tasks()], ["T-003"])

    def test_failed_dep_is_not_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed(Path(tmp), [_task("T-001"), _task("T-002", ["T-001"])])
            graph = TaskGraph(tmp)
            graph.mark_failed("T-001")
            self.assertEqual(graph.ready_tasks(), [])

    def test_in_progress_not_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed(Path(tmp), [_task("T-001")])
            graph = TaskGraph(tmp)
            graph.mark_in_progress("T-001")
            self.assertEqual(graph.ready_tasks(), [])
            self.assertEqual(graph.get("T-001").status, "in_progress")


class TestPersistence(unittest.TestCase):
    def test_mutations_persist_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed(Path(tmp), [_task("T-001")])
            graph = TaskGraph(tmp)
            graph.mark_complete("T-001")
            # Re-read from disk: a fresh graph sees the mutation.
            fresh = TaskGraph(tmp)
            self.assertEqual(fresh.get("T-001").status, "done")
            raw = json.loads(
                (Path(tmp) / ".mythis" / "roadmap.json").read_text(
                    encoding="utf-8"))
            self.assertEqual(raw[0]["status"], "done")


class TestAddTask(unittest.TestCase):
    def test_add_assigns_next_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed(Path(tmp), [_task("T-001"), _task("T-002")])
            graph = TaskGraph(tmp)
            added = graph.add_task({"title": "New", "domain": "test"})
            self.assertEqual(added.task_id, "T-003")
            self.assertEqual(graph.get("T-003").title, "New")

    def test_add_duplicate_id_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed(Path(tmp), [_task("T-001")])
            graph = TaskGraph(tmp)
            with self.assertRaises(ValueError):
                graph.add_task({"task_id": "T-001", "title": "Dup",
                                "domain": "test"})


class TestSplitTask(unittest.TestCase):
    def test_split_rewires_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed(Path(tmp), [
                _task("T-001"),
                _task("T-002", ["T-001"]),
                _task("T-003", ["T-002"]),
            ])
            graph = TaskGraph(tmp)
            new_ids = graph.split_task("T-002", [
                {"title": "Part A", "domain": "test"},
                {"title": "Part B", "domain": "test"},
            ])
            self.assertEqual(new_ids, ["T-002-m1", "T-002-m2"])
            # Original is replaced.
            with self.assertRaises(KeyError):
                graph.get("T-002")
            m1 = graph.get("T-002-m1")
            m2 = graph.get("T-002-m2")
            # m1 inherits the original's deps; parts chain sequentially.
            self.assertEqual(m1.depends_on, ["T-001"])
            self.assertEqual(m2.depends_on, ["T-002-m1"])
            self.assertEqual(m1.status, "ready")
            # Original dependents now wait on the last part.
            self.assertEqual(graph.get("T-003").depends_on, ["T-002-m2"])
            # Graph still validates and ordering is preserved.
            graph.validate()
            self.assertEqual(graph.task_ids(),
                             ["T-001", "T-002-m1", "T-002-m2", "T-003"])
            # Ready set: only T-001 (m1 waits on it, others chain behind).
            graph.mark_complete("T-001")
            self.assertEqual([t.task_id for t in graph.ready_tasks()],
                             ["T-002-m1"])

    def test_split_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed(Path(tmp), [_task("T-001")])
            graph = TaskGraph(tmp)
            graph.split_task("T-001", [{"title": "A", "domain": "t"}])
            fresh = TaskGraph(tmp)
            self.assertEqual(fresh.task_ids(), ["T-001-m1"])

    def test_split_empty_parts_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed(Path(tmp), [_task("T-001")])
            graph = TaskGraph(tmp)
            with self.assertRaises(ValueError):
                graph.split_task("T-001", [])

    def test_split_unknown_task_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            graph = TaskGraph(tmp)
            with self.assertRaises(KeyError):
                graph.split_task("T-999", [{"title": "A", "domain": "t"}])


class TestValidate(unittest.TestCase):
    def test_valid_graph(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed(Path(tmp), [_task("T-001"), _task("T-002", ["T-001"])])
            TaskGraph(tmp).validate()  # no raise

    def test_unknown_dep_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed(Path(tmp), [_task("T-001", ["T-404"])])
            with self.assertRaises(ValueError):
                TaskGraph(tmp).validate()

    def test_cycle_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed(Path(tmp), [_task("T-001", ["T-002"]),
                              _task("T-002", ["T-001"])])
            with self.assertRaises(ValueError):
                TaskGraph(tmp).validate()

    def test_self_cycle_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            _seed(Path(tmp), [_task("T-001", ["T-001"])])
            with self.assertRaises(ValueError):
                TaskGraph(tmp).validate()


if __name__ == "__main__":
    unittest.main()
