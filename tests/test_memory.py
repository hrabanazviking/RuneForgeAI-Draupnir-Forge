"""Slice 26 tests: ProjectMemory — the .mythis/ archive."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from draupnir_forge.memory import ProjectMemory

CANONICAL = ["SYSTEM_VISION.md", "ARCHITECTURE.md", "DOMAIN_MAP.md",
             "INTERFACES.md", "INVARIANTS.md", "CONSTRAINTS.md",
             "ROADMAP.md", "DECISIONS.md", "KNOWN_ISSUES.md",
             "CAPABILITY_LEDGER.md", "PROJECT_STATE.json"]


class TestSkeleton(unittest.TestCase):
    def test_ensure_skeleton_creates_all_docs_and_halls(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = ProjectMemory(tmp)
            created = memory.ensure_skeleton()
            mythis = Path(tmp) / ".mythis"
            self.assertTrue(mythis.is_dir())
            for hall in ("evidence", "sessions", "logs"):
                self.assertTrue((mythis / hall).is_dir())
            for name in CANONICAL:
                self.assertTrue((mythis / name).is_file(), name)
            self.assertEqual(sorted(created), sorted(CANONICAL))

    def test_skeleton_docs_have_title_and_description(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = ProjectMemory(tmp)
            memory.ensure_skeleton()
            vision = (Path(tmp) / ".mythis" / "SYSTEM_VISION.md").read_text(
                encoding="utf-8")
            self.assertTrue(vision.startswith("# System Vision"))
            self.assertGreater(len(vision.strip().splitlines()), 1)

    def test_state_seed_is_valid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = ProjectMemory(tmp)
            memory.ensure_skeleton()
            state = json.loads(
                (Path(tmp) / ".mythis" / "PROJECT_STATE.json").read_text(
                    encoding="utf-8"))
            self.assertIn("phase", state)

    def test_ensure_skeleton_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = ProjectMemory(tmp)
            memory.ensure_skeleton()
            vision_path = Path(tmp) / ".mythis" / "SYSTEM_VISION.md"
            vision_path.write_text("# Mine\n\nThe Scribe's own words.\n",
                                   encoding="utf-8")
            created = memory.ensure_skeleton()  # second call: idempotent
            self.assertEqual(created, [])
            self.assertIn("The Scribe's own words",
                          vision_path.read_text(encoding="utf-8"))

    def test_canonical_docs_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = ProjectMemory(tmp)
            self.assertEqual(memory.canonical_docs(), CANONICAL)


class TestReadWrite(unittest.TestCase):
    def test_scribe_writes_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = ProjectMemory(tmp)
            memory.ensure_skeleton()
            memory.write_doc("DECISIONS.md", "# Decisions\n\nWe chose wisely.\n",
                             by_scribe=True)
            self.assertIn("We chose wisely", memory.read_doc("DECISIONS.md"))

    def test_non_scribe_cannot_write_canonical(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = ProjectMemory(tmp)
            memory.ensure_skeleton()
            with self.assertRaises(PermissionError):
                memory.write_doc("DECISIONS.md", "forged words")
            with self.assertRaises(PermissionError):
                memory.write_doc("PROJECT_STATE.json", "{}")

    def test_evidence_and_sessions_are_free_ground(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = ProjectMemory(tmp)
            memory.ensure_skeleton()
            memory.write_doc("evidence/worker-notes.md", "the worker's tale")
            memory.write_doc("sessions/0001.md", "a session's saga")
            self.assertEqual(memory.read_doc("evidence/worker-notes.md"),
                             "the worker's tale")
            self.assertEqual(memory.read_doc("sessions/0001.md"),
                             "a session's saga")

    def test_reserved_files_need_scribe(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = ProjectMemory(tmp)
            memory.ensure_skeleton()
            with self.assertRaises(PermissionError):
                memory.write_doc("roadmap.json", "[]")
            # ...but the Scribe may.
            memory.write_doc("roadmap.json", "[]", by_scribe=True)
            self.assertEqual(memory.read_doc("roadmap.json"), "[]")

    def test_read_missing_doc_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = ProjectMemory(tmp)
            self.assertEqual(memory.read_doc("DECISIONS.md"), "")

    def test_path_escape_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = ProjectMemory(tmp)
            memory.ensure_skeleton()
            with self.assertRaises(ValueError):
                memory.write_doc("../../escape.txt", "nope", by_scribe=True)
            self.assertEqual(memory.read_doc("../../escape.txt"), "")


class TestSnapshot(unittest.TestCase):
    def test_snapshot_holds_all_docs_and_halls(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = ProjectMemory(tmp)
            memory.ensure_skeleton()
            memory.write_doc("DECISIONS.md", "# Decisions\n\nWise.\n",
                             by_scribe=True)
            memory.write_doc("evidence/notes.md", "worker words")
            memory.write_doc("sessions/0001.md", "session words")
            snap = memory.snapshot()
            self.assertIn("Wise.", snap["docs"]["DECISIONS.md"])
            self.assertEqual(len(snap["docs"]), len(CANONICAL))
            self.assertEqual(snap["evidence"]["evidence/notes.md"],
                             "worker words")
            self.assertEqual(snap["sessions"]["sessions/0001.md"],
                             "session words")


if __name__ == "__main__":
    unittest.main()
