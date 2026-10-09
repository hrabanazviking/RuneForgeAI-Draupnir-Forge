"""Slice 41 acceptance tests: the success-metrics dashboard.

Metrics.compute() is exercised on synthetic event logs (no running
forge needed); render() is checked as pure ASCII output; the
``draupnir metrics`` CLI wiring is exercised with captured stdout.
"""

from __future__ import annotations

import contextlib
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from draupnir_forge.budget import Budget
from draupnir_forge.cli import main
from draupnir_forge.events import EventLog, EventType
from draupnir_forge.metrics import Metrics, render


def _emit(log: EventLog, etype: EventType, task_id: str | None = None) -> None:
    """Emit one event with an optional task id in the payload."""
    payload: dict = {}
    if task_id is not None:
        payload["task_id"] = task_id
    log.emit(etype, actor_role="Tester", payload=payload)


def _full_log(project: Path) -> EventLog:
    """Synthetic log covering every metric's corner cases.

    Task A: start -> complete, no intervention (verified: TEST_PASSED).
    Task B: start -> intervention -> complete (not verified).
    Task C: start -> complete -> TEST_FAILED (regression).
    Task D: start -> repair -> complete (recovery, verified).
    Task E: start only (incomplete; never counts).
    """
    log = EventLog(project)
    _emit(log, EventType.PROJECT_CREATED)
    _emit(log, EventType.TASK_STARTED, "A")
    _emit(log, EventType.TASK_COMPLETED, "A")
    _emit(log, EventType.TEST_PASSED, "A")
    _emit(log, EventType.TASK_STARTED, "B")
    _emit(log, EventType.HUMAN_DECISION_REQUESTED, "B")
    _emit(log, EventType.HUMAN_DECISION_RECEIVED, "B")
    _emit(log, EventType.TASK_COMPLETED, "B")
    _emit(log, EventType.TASK_STARTED, "C")
    _emit(log, EventType.TASK_COMPLETED, "C")
    _emit(log, EventType.TEST_FAILED, "C")
    _emit(log, EventType.TASK_STARTED, "D")
    _emit(log, EventType.TASK_REPAIRED, "D")
    _emit(log, EventType.TASK_COMPLETED, "D")
    _emit(log, EventType.TEST_PASSED, "D")
    _emit(log, EventType.TASK_STARTED, "E")
    return log


class TestMetricsCompute(unittest.TestCase):
    def test_tasks_completed_and_without_intervention(self) -> None:
        with TemporaryDirectory() as tmp:
            metrics = Metrics(_full_log(Path(tmp))).compute()
            self.assertEqual(metrics["tasks_completed"], 4)
            self.assertEqual(metrics["tasks_without_intervention"], 3)

    def test_regression_rate(self) -> None:
        with TemporaryDirectory() as tmp:
            metrics = Metrics(_full_log(Path(tmp))).compute()
            self.assertAlmostEqual(metrics["regression_rate"], 0.25)

    def test_failure_recovery_rate(self) -> None:
        with TemporaryDirectory() as tmp:
            metrics = Metrics(_full_log(Path(tmp))).compute()
            self.assertAlmostEqual(metrics["failure_recovery_rate"], 1.0)

    def test_failure_recovery_zero_when_nothing_repaired(self) -> None:
        with TemporaryDirectory() as tmp:
            log = EventLog(Path(tmp))
            _emit(log, EventType.TASK_STARTED, "X")
            _emit(log, EventType.TASK_COMPLETED, "X")
            metrics = Metrics(log).compute()
            self.assertEqual(metrics["failure_recovery_rate"], 0.0)

    def test_autonomous_run_length(self) -> None:
        with TemporaryDirectory() as tmp:
            metrics = Metrics(_full_log(Path(tmp))).compute()
            # Completions in order: A clean, B intervened (breaks the
            # streak), C clean, D clean -> longest streak is 2.
            self.assertEqual(metrics["autonomous_run_length"], 2)

    def test_interventions_per_hour_counts_all_requests(self) -> None:
        with TemporaryDirectory() as tmp:
            metrics = Metrics(_full_log(Path(tmp))).compute()
            self.assertEqual(metrics["total_interventions"], 1)
            # The synthetic log spans a sub-second wall-clock window, so
            # the raw count is reported instead of an annualized rate.
            self.assertAlmostEqual(metrics["interventions_per_hour"], 1.0)

    def test_tokens_per_verified_task_needs_budget(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = _full_log(root)
            budget = Budget(root, max_tokens=10**12, max_cost_usd=10**12)
            budget.charge("gpt-4o-mini", 1000, 1000)  # 1500 tokens recorded
            metrics = Metrics(log, budget).compute()
            # Two verified tasks (A, D); 1000+1000 tokens charged.
            self.assertEqual(metrics["verified_tasks"], 2)
            self.assertAlmostEqual(metrics["tokens_per_verified_task"], 1000.0)

    def test_tokens_per_verified_task_none_without_budget_or_verified(self) -> None:
        with TemporaryDirectory() as tmp:
            metrics = Metrics(_full_log(Path(tmp))).compute()
            self.assertIsNone(metrics["tokens_per_verified_task"])

    def test_empty_log_gives_honest_zeros(self) -> None:
        with TemporaryDirectory() as tmp:
            metrics = Metrics(EventLog(Path(tmp))).compute()
            self.assertEqual(metrics["tasks_completed"], 0)
            self.assertEqual(metrics["tasks_without_intervention"], 0)
            self.assertEqual(metrics["regression_rate"], 0.0)
            self.assertEqual(metrics["autonomous_run_length"], 0)
            self.assertEqual(metrics["failure_recovery_rate"], 0.0)
            self.assertIsNone(metrics["tokens_per_verified_task"])


class TestRender(unittest.TestCase):
    def test_render_is_ascii_table_with_all_rows(self) -> None:
        metrics = {
            "tasks_completed": 4,
            "tasks_without_intervention": 3,
            "interventions_per_hour": 0.5,
            "tokens_per_verified_task": 1234.567,
            "regression_rate": 0.25,
            "autonomous_run_length": 3,
            "failure_recovery_rate": 1.0,
        }
        table = render(metrics)
        for key in metrics:
            self.assertIn(key, table)
        self.assertIn("25.0%", table)  # regression rate formatted
        self.assertIn("1,234.57", table)  # tokens formatted
        self.assertTrue(table.isascii())

    def test_render_empty_dict(self) -> None:
        self.assertEqual(render({}), "no metrics available")


class TestMetricsCLI(unittest.TestCase):
    @staticmethod
    def _run(argv: list) -> tuple:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_metrics_command_prints_table(self) -> None:
        with TemporaryDirectory() as tmp:
            _full_log(Path(tmp))
            code, out, _err = self._run(
                ["--project-dir", tmp, "metrics"]
            )
            self.assertEqual(code, 0)
            self.assertIn("tasks_completed", out)
            self.assertIn("autonomous_run_length", out)

    def test_metrics_command_no_events_message(self) -> None:
        with TemporaryDirectory() as tmp:
            code, out, _err = self._run(
                ["--project-dir", tmp, "metrics"]
            )
            self.assertEqual(code, 0)
            self.assertIn("no events", out.lower())


if __name__ == "__main__":
    unittest.main()
