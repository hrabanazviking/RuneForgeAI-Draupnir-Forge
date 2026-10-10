"""Slice 42 acceptance tests: session memory and replay.

A Session is begun, actions and findings are recorded, and the run is
finished; the files land in .mythis/sessions/<id>/ and replay() returns
a clearly-labeled debugging listing (never re-execution).
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from draupnir_forge.sessions import Session


class TestSessionLifecycle(unittest.TestCase):
    def test_begin_writes_goal_md_and_returns_timestamp_id(self) -> None:
        with TemporaryDirectory() as tmp:
            session = Session(Path(tmp))
            session_id = session.begin("forge a hammer")
            self.assertTrue(session_id)
            goal_path = Path(tmp) / ".mythis" / "sessions" / session_id / "goal.md"
            self.assertTrue(goal_path.is_file())
            self.assertIn("forge a hammer", goal_path.read_text(encoding="utf-8"))

    def test_record_action_appends_jsonl_with_timestamp(self) -> None:
        with TemporaryDirectory() as tmp:
            session = Session(Path(tmp))
            session_id = session.begin("goal")
            session.record_action(
                {"action": "emit_event", "detail": "TASK_STARTED"}
            )
            path = Path(tmp) / ".mythis" / "sessions" / session_id / "actions.jsonl"
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            record = json.loads(lines[0])
            self.assertEqual(record["action"], "emit_event")
            self.assertIn("ts", record)

    def test_record_finding_appends_findings_md(self) -> None:
        with TemporaryDirectory() as tmp:
            session = Session(Path(tmp))
            session_id = session.begin("goal")
            session.record_finding("the forge holds steady")
            path = Path(tmp) / ".mythis" / "sessions" / session_id / "findings.md"
            self.assertIn("the forge holds steady", path.read_text(encoding="utf-8"))

    def test_finish_writes_result_md(self) -> None:
        with TemporaryDirectory() as tmp:
            session = Session(Path(tmp))
            session_id = session.begin("goal")
            session.finish({"outcome": "success", "tasks": 3})
            path = Path(tmp) / ".mythis" / "sessions" / session_id / "result.md"
            text = path.read_text(encoding="utf-8")
            self.assertIn("success", text)
            self.assertIn("3", text)

    def test_two_begins_get_unique_ids(self) -> None:
        with TemporaryDirectory() as tmp:
            session = Session(Path(tmp))
            first = session.begin("one")
            second = session.begin("two")
            self.assertNotEqual(first, second)
            self.assertEqual(sorted(session.list_sessions()), sorted([first, second]))

    def test_record_without_begin_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            session = Session(Path(tmp))
            with self.assertRaises(RuntimeError):
                session.record_action({"action": "noop"})
            with self.assertRaises(RuntimeError):
                session.record_finding("nothing")


class TestReplay(unittest.TestCase):
    def test_replay_returns_numbered_debugging_listing(self) -> None:
        with TemporaryDirectory() as tmp:
            session = Session(Path(tmp))
            session_id = session.begin("goal")
            session.record_action({"action": "start_task", "detail": "task A"})
            session.record_action({"action": "complete_task", "detail": "task A"})
            text = session.replay(session_id)
            self.assertIn("DEBUGGING", text.upper())
            self.assertIn("NOT re-executed", text)
            self.assertIn("1. start_task", text)
            self.assertIn("2. complete_task", text)
            self.assertIn("task A", text)

    def test_replay_unknown_session_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                Session(Path(tmp)).replay("no-such-session")

    def test_replay_empty_session_is_labeled(self) -> None:
        with TemporaryDirectory() as tmp:
            session = Session(Path(tmp))
            session_id = session.begin("goal")
            text = session.replay(session_id)
            self.assertIn("no actions were recorded", text)

    def test_list_sessions_empty_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertEqual(Session(Path(tmp)).list_sessions(), [])


class TestAtomicWrites(unittest.TestCase):
    """Slice 8 (BATCH D): every session write is atomic — temp file in the
    same directory, flush + fsync, then os.replace. No torn files, no
    leftover *.tmp files after normal operation."""

    def test_begin_record_20_actions_finish_leaves_no_tmp_and_valid_files(self) -> None:
        with TemporaryDirectory() as tmp:
            session = Session(Path(tmp))
            session_id = session.begin("atomic saga")
            for i in range(20):
                session.record_action({"action": "forge", "detail": f"action {i}"})
            session.record_finding("all twenty held fast")
            session.finish({"result": "success"})

            sdir = Path(tmp) / ".mythis" / "sessions" / session_id
            # No temp files may remain after normal operation.
            self.assertEqual(list(sdir.glob("*.tmp")), [])
            self.assertEqual(list(sdir.parent.glob("*.tmp")), [])

            # Every line of actions.jsonl parses as JSON, in order.
            lines = (sdir / "actions.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 20)
            for index, line in enumerate(lines):
                record = json.loads(line)
                self.assertEqual(record["action"], "forge")
                self.assertEqual(record["detail"], f"action {index}")
                self.assertIn("ts", record)

            # Other session files round-trip intact.
            goal = (sdir / "goal.md").read_text(encoding="utf-8")
            self.assertIn("atomic saga", goal)
            result = (sdir / "result.md").read_text(encoding="utf-8")
            self.assertIn("success", result)
            findings = (sdir / "findings.md").read_text(encoding="utf-8")
            self.assertIn("all twenty held fast", findings)

    def test_replay_still_reads_atomically_written_session(self) -> None:
        with TemporaryDirectory() as tmp:
            session = Session(Path(tmp))
            session_id = session.begin("replay saga")
            for i in range(20):
                session.record_action({"action": "forge", "detail": f"action {i}"})
            text = session.replay(session_id)
            self.assertIn("1. forge", text)
            self.assertIn("20. forge", text)
            self.assertIn("action 19", text)


if __name__ == "__main__":
    unittest.main()
