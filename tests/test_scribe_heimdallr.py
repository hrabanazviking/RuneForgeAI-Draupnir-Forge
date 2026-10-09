"""Slice 18 tests: Scribe (canonical docs) + Heimdallr (loop watchman)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from draupnir_forge.roles import heimdallr, scribe
from draupnir_forge.roles.base import RoleContext, registry
from draupnir_forge.roles.heimdallr import Heimdallr
from draupnir_forge.roles.scribe import (
    Scribe,
    log_capability,
    note_issue,
    record_decision,
    update_roadmap_status,
)


def _doc(tmp: str, name: str) -> Path:
    return Path(tmp) / ".mythis" / name


class TestScribeDecisions(unittest.TestCase):
    def test_record_decision_creates_file_with_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = record_decision(
                tmp,
                decision="Use pytest for the test gate",
                reason="suite already uses it",
                alternatives=["unittest only", "no tests"],
                evidence="suite green under pytest",
                consequences="tester prefers pytest",
                role="tester",
            )
            text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# Decisions"))
        self.assertIn("Use pytest for the test gate", text)
        self.assertIn("Role: tester", text)
        self.assertIn("unittest only", text)

    def test_record_decision_is_append_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(2):
                record_decision(tmp, f"decision {i}", "r", [], "e", "c", "scribe")
            text = _doc(tmp, "DECISIONS.md").read_text(encoding="utf-8")
        self.assertIn("decision 0", text)
        self.assertIn("decision 1", text)
        self.assertEqual(text.count("# Decisions"), 1)


class TestScribeRoadmap(unittest.TestCase):
    def test_updates_existing_task_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            roadmap = _doc(tmp, "ROADMAP.md")
            roadmap.parent.mkdir(parents=True, exist_ok=True)
            roadmap.write_text(
                "# Roadmap\n\n- [ ] T-001: build the forge\n- [ ] T-002: test it\n",
                encoding="utf-8",
            )
            update_roadmap_status(tmp, "T-001", "done")
            text = roadmap.read_text(encoding="utf-8")
        self.assertIn("- [x] T-001: build the forge", text)
        self.assertIn("- [ ] T-002: test it", text)

    def test_appends_unknown_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            update_roadmap_status(tmp, "T-099", "in-progress")
            text = _doc(tmp, "ROADMAP.md").read_text(encoding="utf-8")
        self.assertIn("T-099", text)
        self.assertIn("in-progress", text)

    def test_creates_roadmap_with_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            update_roadmap_status(tmp, "T-001", "done")
            text = _doc(tmp, "ROADMAP.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# Roadmap"))


class TestScribeIssuesAndCapabilities(unittest.TestCase):
    def test_note_issue(self):
        with tempfile.TemporaryDirectory() as tmp:
            note_issue(tmp, "flaky test in cartographer")
            text = _doc(tmp, "KNOWN_ISSUES.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# Known Issues"))
        self.assertIn("flaky test in cartographer", text)

    def test_log_capability(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_capability(tmp, "can parse pytest output")
            text = _doc(tmp, "CAPABILITY_LEDGER.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# Capability Ledger"))
        self.assertIn("can parse pytest output", text)

    def test_append_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            note_issue(tmp, "first")
            note_issue(tmp, "second")
            text = _doc(tmp, "KNOWN_ISSUES.md").read_text(encoding="utf-8")
        self.assertIn("first", text)
        self.assertIn("second", text)


class TestScribeRun(unittest.TestCase):
    def test_run_processes_ops(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = RoleContext(
                project_dir=tmp,
                artifacts={
                    "scribe_ops": [
                        {"op": "decision", "decision": "d1", "reason": "r",
                         "alternatives": [], "evidence": "e",
                         "consequences": "c", "role": "architect"},
                        {"op": "roadmap", "task_id": "T-001", "status": "done"},
                        {"op": "issue", "issue": "a wrinkle"},
                        {"op": "capability", "capability": "writes docs"},
                    ]
                },
            )
            result = Scribe().run(ctx)
            self.assertTrue(result.ok)
            self.assertIn("4 scribe op(s) applied", result.summary)
            self.assertIn("d1", _doc(tmp, "DECISIONS.md").read_text(encoding="utf-8"))
            self.assertIn("T-001", _doc(tmp, "ROADMAP.md").read_text(encoding="utf-8"))

    def test_run_empty_ops(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = Scribe().run(RoleContext(project_dir=tmp))
        self.assertTrue(result.ok)

    def test_run_unknown_op_fails_gracefully(self):
        with tempfile.TemporaryDirectory() as tmp:
            ctx = RoleContext(
                project_dir=tmp,
                artifacts={"scribe_ops": [{"op": "teleport"}]},
            )
            result = Scribe().run(ctx)
        self.assertFalse(result.ok)
        self.assertIn("teleport", result.summary)

    def test_registered(self):
        self.assertIn("scribe", registry.names())
        self.assertIsInstance(registry.create("scribe"), Scribe)


class TestHeimdallrCheck(unittest.TestCase):
    def setUp(self):
        self.watchman = Heimdallr()

    def test_healthy_history(self):
        history = [
            {"task_id": "T-001", "outcome": "ok"},
            {"task_id": "T-002", "outcome": "ok"},
        ]
        self.assertEqual(self.watchman.check(history), [])

    def test_empty_history_is_healthy(self):
        self.assertEqual(self.watchman.check([]), [])

    def test_three_failures_flagged(self):
        history = [{"task_id": "T-007", "outcome": "failed"} for _ in range(3)]
        escalations = self.watchman.check(history)
        self.assertEqual(len(escalations), 1)
        self.assertIn("T-007", escalations[0])
        self.assertIn("3 times", escalations[0])

    def test_two_failures_not_flagged(self):
        history = [{"task_id": "T-007", "outcome": "failed"} for _ in range(2)]
        history.append({"task_id": "T-007", "outcome": "ok"})
        self.assertEqual(self.watchman.check(history), [])

    def test_no_progress_flagged(self):
        history = [{"task_id": f"T-00{i}", "outcome": "failed"} for i in range(5)]
        escalations = self.watchman.check(history, no_progress_cycles=5)
        self.assertTrue(any("no progress" in e for e in escalations))

    def test_no_progress_custom_window(self):
        history = [{"task_id": "T-001", "outcome": "failed"} for _ in range(2)]
        self.assertEqual(self.watchman.check(history, no_progress_cycles=5), [])
        escalations = self.watchman.check(history, no_progress_cycles=2)
        self.assertTrue(any("no progress" in e for e in escalations))

    def test_recent_ok_breaks_streak(self):
        # Distinct task ids so the repeated-failure rule stays quiet.
        history = [{"task_id": f"T-00{i}", "outcome": "failed"} for i in range(9)]
        history.append({"task_id": "T-010", "outcome": "ok"})
        history.append({"task_id": "T-011", "outcome": "failed"})
        self.assertEqual(self.watchman.check(history, no_progress_cycles=5), [])

    def test_budget_exceeded_flagged(self):
        history = [
            {"task_id": "T-001", "outcome": "ok"},
            {"budget_exceeded": True},
        ]
        escalations = self.watchman.check(history)
        self.assertTrue(any("budget exceeded" in e for e in escalations))

    def test_multiple_escalations(self):
        history = [{"task_id": "T-009", "outcome": "failed"} for _ in range(4)]
        history.append({"budget_exceeded": True})
        escalations = self.watchman.check(history, no_progress_cycles=3)
        self.assertGreaterEqual(len(escalations), 3)


class TestHeimdallrRun(unittest.TestCase):
    def test_run_healthy(self):
        ctx = RoleContext(
            project_dir="/tmp",
            artifacts={"history": [{"task_id": "T-001", "outcome": "ok"}]},
        )
        result = Heimdallr().run(ctx)
        self.assertTrue(result.ok)
        self.assertIsNone(result.escalation)

    def test_run_escalates(self):
        ctx = RoleContext(
            project_dir="/tmp",
            artifacts={
                "history": [
                    {"task_id": "T-001", "outcome": "failed"}
                    for _ in range(3)
                ]
            },
        )
        result = Heimdallr().run(ctx)
        self.assertFalse(result.ok)
        self.assertIn("T-001", result.escalation)
        self.assertIn("T-001", result.artifacts["escalations"][0])

    def test_registered(self):
        self.assertIn("heimdallr", registry.names())
        self.assertIsInstance(registry.create("heimdallr"), Heimdallr)


if __name__ == "__main__":
    unittest.main()
