"""metrics.py — the skald's tally of the Forge's work (slice 41).

Spec §33: the research value of the Forge is measurable. This module
computes the success metrics straight from the event ledger (events.py),
so every number on the dashboard is earned by events, not invented.

Metrics computed by :class:`Metrics.compute`:

- ``tasks_completed`` — TASK_COMPLETED events.
- ``tasks_without_intervention`` — completions with no
  HUMAN_DECISION_REQUESTED between their TASK_STARTED and
  TASK_COMPLETED (matched by ``task_id`` payload).
- ``interventions_per_hour`` — human decision requests over the wall-clock
  span of the log (hours; 0 span yields the raw count).
- ``tokens_per_verified_task`` — budget tokens used per completed task
  backed by a TEST_PASSED (None when no verified task exists).
- ``regression_rate`` — share of completed tasks followed by a TEST_FAILED
  for the same task id.
- ``autonomous_run_length`` — longest streak of consecutive completed
  tasks with no intervention between their start and completion.
- ``failure_recovery_rate`` — share of TASK_REPAIRED tasks that reached
  TASK_COMPLETED afterwards.

All functions are pure and work on synthetic event logs, so the tests
never need a running forge.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .events import EventLog, EventType, ForgeEvent

__all__ = ["Metrics", "render"]

# Task identity lives here first; fall back gracefully to less specific
# keys so older hand-rolled logs still produce numbers instead of errors.
_TASK_ID_KEYS = ("task_id", "id", "slice")


def _task_key(event: ForgeEvent) -> Optional[str]:
    """Best-effort task identity for an event, or None when unknowable.

    Events that carry no usable id are keyed by their own seq number so
    they never silently merge with an unrelated task.
    """
    payload = event.payload or {}
    for key in _TASK_ID_KEYS:
        value = payload.get(key)
        if value:
            return str(value)
    return f"seq:{event.seq}"


def _parse_ts(ts: str) -> Optional[datetime]:
    """Parse an event timestamp; None when the string is unusable.

    The tally must never die on one malformed timestamp.
    """
    if not isinstance(ts, str) or not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _span_hours(events: List[ForgeEvent]) -> float:
    """Wall-clock hours between first and last event (0 when unknown)."""
    stamps = [_parse_ts(event.ts) for event in events]
    stamps = [stamp for stamp in stamps if stamp is not None]
    if len(stamps) < 2:
        return 0.0
    span = (max(stamps) - min(stamps)).total_seconds() / 3600.0
    return max(0.0, span)


class Metrics:
    """Computes spec §33 success metrics from an EventLog.

    Args:
        event_log: The project's ledger (any EventLog instance; synthetic
            logs work fine, which is what makes the dashboard testable).
        budget: Optional ``Budget``; needed only for the
            ``tokens_per_verified_task`` metric. Metrics without a budget
            report None for that metric instead of failing.
    """

    def __init__(self, event_log: EventLog, budget: Optional[Any] = None) -> None:
        self._log = event_log
        self._budget = budget

    # -- helpers ----------------------------------------------------------
    def _events(self) -> List[ForgeEvent]:
        """All events in chronological order, read fresh each time."""
        try:
            return self._log.query()
        except Exception:  # the tally never sinks the ship
            return []

    @staticmethod
    def _task_lifecycles(events: List[ForgeEvent]) -> Dict[str, Dict[str, Any]]:
        """Fold the event stream into per-task lifecycle records.

        Each record holds the TASK_STARTED event, the TASK_COMPLETED
        event, and every HUMAN_DECISION_REQUESTED / TASK_REPAIRED /
        TEST_FAILED / TEST_PASSED in between or after. Events are already
        chronological, so a task's ``start`` is its first TASK_STARTED.
        """
        tasks: Dict[str, Dict[str, Any]] = {}
        for event in events:
            key = _task_key(event)
            record = tasks.setdefault(
                key,
                {
                    "start": None,
                    "completed": None,
                    "interventions": [],
                    "repaired": [],
                    "test_failed": [],
                    "test_passed": [],
                },
            )
            etype = event.type
            if etype is EventType.TASK_STARTED and record["start"] is None:
                record["start"] = event
            elif etype is EventType.TASK_COMPLETED:
                record["completed"] = event
            elif etype is EventType.HUMAN_DECISION_REQUESTED:
                record["interventions"].append(event)
            elif etype is EventType.TASK_REPAIRED:
                record["repaired"].append(event)
            elif etype is EventType.TEST_FAILED:
                record["test_failed"].append(event)
            elif etype is EventType.TEST_PASSED:
                record["test_passed"].append(event)
        return tasks

    @staticmethod
    def _intervention_between(record: Dict[str, Any]) -> bool:
        """True when any intervention fell between start and completion."""
        start = record["start"]
        completed = record["completed"]
        if start is None or completed is None:
            return bool(record["interventions"])
        return any(
            start.seq < ev.seq <= completed.seq for ev in record["interventions"]
        )

    # -- the dashboard ------------------------------------------------------
    def compute(self) -> Dict[str, Any]:
        """Compute every metric; returns a plain dict of values.

        Missing evidence yields honest zeros / Nones, never exceptions.
        """
        events = self._events()
        tasks = self._task_lifecycles(events)
        completed = [r for r in tasks.values() if r["completed"] is not None]
        n_completed = len(completed)

        # Interventions: only those tied to a task's lifespan count as
        # "without intervention"; the raw hourly rate counts everything.
        without_intervention = sum(
            1 for r in completed if not self._intervention_between(r)
        )
        total_interventions = len(
            self._log.query(type=EventType.HUMAN_DECISION_REQUESTED)
            if self._log is not None
            else []
        )
        hours = _span_hours(events)
        # Annualizing a wall-clock span shorter than one second is
        # nonsense (it turns microsecond gaps into millions per hour);
        # below that floor, report the raw count honestly.
        if hours < 1 / 3600:
            interventions_per_hour = float(total_interventions)
        else:
            interventions_per_hour = total_interventions / hours

        # Verified task: completed AND a TEST_PASSED for it afterwards.
        verified = sum(
            1
            for r in completed
            if any(
                ev.seq >= r["completed"].seq for ev in r["test_passed"]
            )
        )
        tokens_per_verified: Optional[float] = None
        tokens_used = self._budget.tokens_used if self._budget is not None else None
        if tokens_used is not None and verified > 0:
            tokens_per_verified = tokens_used / verified

        # Regression: a completed task later failing a test.
        regressed = sum(
            1
            for r in completed
            if any(ev.seq > r["completed"].seq for ev in r["test_failed"])
        )
        regression_rate = regressed / n_completed if n_completed else 0.0

        # Autonomous run length: longest streak of consecutive completions
        # (ordered by completion seq) with no intervention between
        # their own start and completion.
        ordered = sorted(completed, key=lambda r: r["completed"].seq)
        best = streak = 0
        for record in ordered:
            if self._intervention_between(record):
                streak = 0
            else:
                streak += 1
                best = max(best, streak)
        autonomous_run_length = best

        # Failure recovery: repaired tasks that completed afterwards.
        repaired_keys = [key for key, r in tasks.items() if r["repaired"]]
        recovered = sum(
            1
            for key in repaired_keys
            if tasks[key]["completed"] is not None
            and tasks[key]["completed"].seq
            > max(ev.seq for ev in tasks[key]["repaired"])
        )
        failure_recovery_rate = (
            recovered / len(repaired_keys) if repaired_keys else 0.0
        )

        return {
            "tasks_completed": n_completed,
            "tasks_without_intervention": without_intervention,
            "interventions_per_hour": interventions_per_hour,
            "tokens_per_verified_task": tokens_per_verified,
            "regression_rate": regression_rate,
            "autonomous_run_length": autonomous_run_length,
            "failure_recovery_rate": failure_recovery_rate,
            "total_interventions": total_interventions,
            "verified_tasks": verified,
            "tasks_with_regression": regressed,
        }


def _format_value(key: str, value: Any) -> str:
    """Human-friendly rendering of one metric value."""
    if value is None:
        return "n/a (no verified tasks yet)"
    if key in ("interventions_per_hour", "tokens_per_verified_task"):
        return f"{value:,.2f}"
    if key in ("regression_rate", "failure_recovery_rate"):
        return f"{value * 100:,.1f}%"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


_NOTES = {
    "tasks_completed": "TASK_COMPLETED events in the ledger",
    "tasks_without_intervention": "no HUMAN_DECISION_REQUESTED between start and completion",
    "interventions_per_hour": "human decision requests over the log's wall-clock span",
    "tokens_per_verified_task": "budget tokens per completed task with a passing test",
    "regression_rate": "completed tasks followed by a TEST_FAILED for the same task",
    "autonomous_run_length": "longest streak of consecutive intervention-free completions",
    "failure_recovery_rate": "TASK_REPAIRED tasks that later completed",
}


def render(metrics: Dict[str, Any]) -> str:
    """Render the metrics dict as an ASCII table (pure function).

    Rows follow a fixed order so dashboards stay comparable run to run.
    Unknown extra keys in the dict are shown after the known ones.
    """
    rows: List[Tuple[str, str, str]] = []
    ordered = [
        "tasks_completed",
        "tasks_without_intervention",
        "interventions_per_hour",
        "tokens_per_verified_task",
        "regression_rate",
        "autonomous_run_length",
        "failure_recovery_rate",
    ]
    known = set(ordered)
    for key in ordered:
        if key in metrics:
            rows.append((key, _format_value(key, metrics[key]), _NOTES[key]))
    for key in metrics:
        if key not in known:
            rows.append((key, _format_value(key, metrics[key]), ""))
    if not rows:
        return "no metrics available"
    name_width = max(len(name) for name, _, _ in rows)
    value_width = max(len(value) for _, value, _ in rows)
    header = f"{'METRIC':<{name_width}}  {'VALUE':<{value_width}}  NOTE"
    divider = "-" * (len(header) + 10)
    lines = ["Success metrics (spec section 33)", divider, header, divider]
    for name, value, note in rows:
        lines.append(f"{name:<{name_width}}  {value:<{value_width}}  {note}")
    lines.append(divider)
    return "\n".join(lines)
