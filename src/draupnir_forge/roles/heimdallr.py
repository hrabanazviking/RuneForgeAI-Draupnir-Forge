"""Slice 18b — Heimdallr role: the watchman of the Forge's loop.

Heimdallr stands on the bridge and watches the task history flow past.
He never builds anything himself; he *notices*:

  - a task that failed 3 times (the same wound reopened),
  - no progress in N consecutive cycles (a runaway loop),
  - a budget-exceeded marker (spending outrunning the hoard).

``check()`` returns a list of escalation strings; an empty list means
the loop is healthy.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from draupnir_forge.roles.base import Role, RoleContext, RoleResult, register_role

log = logging.getLogger("draupnir_forge.roles.heimdallr")

# How many failures of one task before the watchman sounds the horn.
REPEATED_FAILURE_THRESHOLD = 3

# Default window for the no-progress watch.
DEFAULT_NO_PROGRESS_CYCLES = 5


@register_role
class Heimdallr(Role):
    """Watches loop health and raises escalations before things burn."""

    name = "heimdallr"
    purpose = (
        "Monitor the task history for repeated failures, runaway loops "
        "with no progress, and budget breaches; return escalation strings."
    )

    def check(
        self,
        history: List[Dict[str, Any]],
        no_progress_cycles: int = DEFAULT_NO_PROGRESS_CYCLES,
    ) -> List[str]:
        """Inspect a task history and return escalation strings.

        Args:
            history: Chronological list of entries, each like
                ``{"task_id": "T-001", "outcome": "failed"|"ok", ...}``.
                A ``{"budget_exceeded": True}`` entry marks a breach.
            no_progress_cycles: Consecutive non-complete entries that
                count as "no progress" (a runaway loop).

        Returns:
            Escalation strings; empty list means healthy.
        """
        escalations: List[str] = []
        history = [e for e in (history or []) if isinstance(e, dict)]
        if not history:
            return escalations

        # 1. The same wound reopened: one task failing again and again.
        failure_counts: Dict[str, int] = {}
        for entry in history:
            if str(entry.get("outcome", "")).strip().lower() == "failed":
                task_id = str(entry.get("task_id", "?"))
                failure_counts[task_id] = failure_counts.get(task_id, 0) + 1
        for task_id in sorted(failure_counts):
            count = failure_counts[task_id]
            if count >= REPEATED_FAILURE_THRESHOLD:
                escalations.append(
                    f"task {task_id} failed {count} times — "
                    "repeated failure, escalate to repair or the human"
                )

        # 2. Treading water: no completed task in the recent window.
        try:
            window = max(1, int(no_progress_cycles))
        except (TypeError, ValueError):
            window = DEFAULT_NO_PROGRESS_CYCLES
        streak = 0
        for entry in reversed(history):
            if str(entry.get("outcome", "")).strip().lower() == "ok":
                break
            streak += 1
        if streak >= window:
            escalations.append(
                f"no progress in {streak} consecutive cycles "
                f"(threshold {window}) — runaway loop suspected"
            )

        # 3. The hoard is empty: any budget-exceeded marker trips this.
        if any(entry.get("budget_exceeded") for entry in history):
            escalations.append(
                "budget exceeded — spending has outrun the hoard, halt and ask"
            )

        return escalations

    def run(self, ctx: RoleContext) -> RoleResult:
        try:
            history = ctx.artifacts.get("history") or []
            escalations = self.check(history)
        except Exception as exc:
            # The contract says: never raise on bad input.
            return RoleResult(ok=False, summary=f"Heimdallr stumbled: {exc}")

        ok = not escalations
        summary = (
            "the Bifrost is quiet — loop healthy"
            if ok
            else f"Heimdallr sounds the horn: {'; '.join(escalations)}"
        )
        return RoleResult(
            ok=ok,
            summary=summary,
            artifacts={"escalations": escalations},
            escalation=None if ok else "; ".join(escalations),
        )
