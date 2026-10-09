"""Slice 38 tests: reground() — re-map, stale tasks, drift, event."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from draupnir_forge.drift import DriftKind
from draupnir_forge.reground import reground


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _domain_map(files: list) -> dict:
    return {
        "files": [{"path": f, "language": "python", "size": 10}
                  for f in files],
        "entry_points": [],
        "test_dirs": [],
        "dep_files": [],
        "import_graph": {},
        "cycles": [],
        "domains": {},
    }


def _architecture() -> dict:
    return {
        "domains": [
            {"name": "core", "owner": "worker",
             "files": ["src/core/a.py"]},
            {"name": "gone", "owner": "worker",
             "files": ["gone/x.py"]},
        ],
        "interfaces": [],
        "invariants": [],
        "dependency_direction": {"core": [], "gone": []},
    }


def _roadmap() -> list:
    return [
        {
            "task_id": "T-001",
            "title": "carve the old rune",
            "domain": "core",
            "status": "done",
            "depends_on": [],
            "goal": "Finish src/old.py and wire it into the build.",
            "constraints": [],
            "acceptance": ["src/old.py exists"],
            "verification": [],
        },
        {
            "task_id": "T-002",
            "title": "raise the new mast",
            "domain": "core",
            "status": "ready",
            "depends_on": [],
            "goal": "Extend src/a.py with the new mast logic.",
            "constraints": [],
            "acceptance": ["src/a.py updated"],
            "verification": [],
        },
    ]


class RegroundTest(unittest.TestCase):
    """A deleted file must stale its task; drift must be reported."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _write(self.root / "src" / "a.py", "VALUE = 1\n")
        _write(self.root / "src" / "core" / "a.py", "VALUE = 2\n")
        _write(self.root / ".mythis" / "domain_map.json",
               json.dumps(_domain_map(["src/a.py", "src/old.py"])))
        _write(self.root / ".mythis" / "architecture.json",
               json.dumps(_architecture()))
        _write(self.root / ".mythis" / "roadmap.json",
               json.dumps(_roadmap()))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_reground_flags_stale_task_after_deletion(self) -> None:
        result = reground(self.root)
        self.assertTrue(result["regrounded"])
        self.assertIn("T-001", result["stale_tasks"])
        self.assertNotIn("T-002", result["stale_tasks"])
        # The note was persisted into roadmap.json.
        roadmap = json.loads(
            (self.root / ".mythis" / "roadmap.json").read_text(
                encoding="utf-8"))
        t001 = next(t for t in roadmap if t["task_id"] == "T-001")
        self.assertIn("src/old.py", t001["notes"])
        self.assertIn("re-grounding", t001["notes"])
        t002 = next(t for t in roadmap if t["task_id"] == "T-002")
        self.assertEqual(t002["notes"], "")

    def test_reground_diffs_domain_map(self) -> None:
        result = reground(self.root)
        self.assertIn("src/core/a.py", result["added_files"])
        self.assertIn("src/old.py", result["removed_files"])
        self.assertNotIn("src/a.py", result["added_files"])

    def test_reground_reports_drift_and_needs_architect(self) -> None:
        result = reground(self.root)
        kinds = {f["kind"] for f in result["drift"]}
        self.assertIn(DriftKind.MISSING_DOMAIN.value, kinds)
        self.assertTrue(result["needs_architect"])

    def test_reground_emits_project_regrounded(self) -> None:
        reground(self.root)
        events_path = self.root / ".mythis" / "events.jsonl"
        self.assertTrue(events_path.is_file())
        types = [json.loads(line)["type"] for line in
                 events_path.read_text(encoding="utf-8").splitlines()
                 if line.strip()]
        self.assertIn("PROJECT_REGROUNDED", types)

    def test_reground_without_baselines_still_returns(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            root = Path(other)
            _write(root / "src" / "a.py", "X = 1\n")
            result = reground(root)
            self.assertTrue(result["regrounded"])
            self.assertEqual(result["stale_tasks"], [])
            self.assertFalse(result["needs_architect"])

    def test_mark_stale_is_idempotent(self) -> None:
        reground(self.root)
        first = (self.root / ".mythis" / "roadmap.json").read_text(
            encoding="utf-8")
        reground(self.root)
        second = (self.root / ".mythis" / "roadmap.json").read_text(
            encoding="utf-8")
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
