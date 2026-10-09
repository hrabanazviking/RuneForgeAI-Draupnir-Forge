"""ui.py — Black-Box Progress Interface (slice 31, spec §19).

Huginn's plain report: what the forge has done, what it does now, and
what waits its turn — readable without ever opening the ``.mythis/``
ledger. :class:`ProgressView` is a pure function of plain dicts, so its
output can be pinned by golden tests with no TTY, no project on disk,
and no running forge loop.

Task dicts are read defensively: ``title`` (falling back to ``name``
or the task id), ``status`` (falling back to ``state``), and the usual
planner fields ``task_id`` / ``id`` / ``slice`` / ``depends_on`` are
all tolerated, because the panel must survive roadmaps written by any
slice or by hand.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

__all__ = ["ProgressView"]


# Status vocabularies mapped onto the three progress buckets.
_DONE_STATUSES = frozenset({"done", "completed", "complete"})
_CURRENT_STATUSES = frozenset(
    {"in_progress", "in-progress", "doing", "active", "working"}
)

# Rendering budgets — the panel summarizes, it never dumps the ledger.
_GOAL_MAX = 60
_COMPLETED_MAX = 6
_QUEUED_MAX = 4
_LINE_MAX = 72


def _as_str(value: Any, default: str = "") -> str:
    """Coerce a possibly-missing value to text."""
    if value is None:
        return default
    return str(value)


def _truncate(text: str, limit: int) -> str:
    """Shorten text to at most ``limit`` characters, with an ellipsis.

    The ellipsis counts toward the limit, so the result is never longer
    than ``limit`` — a promise the caller's column math can rely on.
    """
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _task_title(task: Dict[str, Any]) -> str:
    """Best-effort human title for a task dict of any shape."""
    for key in ("title", "name"):
        value = task.get(key)
        if value:
            return _truncate(_as_str(value), _LINE_MAX)
    for key in ("task_id", "id", "slice"):
        value = task.get(key)
        if value:
            return _truncate(f"task {_as_str(value)}", _LINE_MAX)
    return "(untitled)"


def _task_status(task: Dict[str, Any]) -> str:
    """Normalized lowercase status string for a task dict."""
    raw = task.get("status")
    if raw is None:
        raw = task.get("state", "")
    return _as_str(raw).strip().lower()


def _bucket(status: str) -> str:
    """Map a normalized status to ``done`` / ``current`` / ``queued``."""
    if status in _DONE_STATUSES:
        return "done"
    if status in _CURRENT_STATUSES:
        return "current"
    return "queued"


def _current_task(tasks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The task currently being worked, if any task is in progress."""
    for task in tasks:
        if _bucket(_task_status(task)) == "current":
            return task
    return None


def _decisions_needed(state: Dict[str, Any]) -> List[str]:
    """Human decisions the forge is waiting on, from the state dict.

    Several key spellings are tolerated (``decisions_needed``,
    ``human_decisions``, ``awaiting_human``); a lone string is treated
    as a single decision.
    """
    for key in ("decisions_needed", "human_decisions", "awaiting_human"):
        value = state.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            return [value] if value.strip() else []
        try:
            items = [_as_str(item) for item in value if _as_str(item)]
        except TypeError:
            items = []
        return items
    return []


class ProgressView:
    """Renders the §19 progress panel as plain text.

    All methods are static and pure: given the same state, tasks and
    health, they always return the same string. No filesystem, no TTY,
    no forge machinery.
    """

    @staticmethod
    def render(
        state: Dict[str, Any],
        tasks: List[Dict[str, Any]],
        health: str = "Stable",
    ) -> str:
        """Render the full §19-style progress panel.

        Args:
            state: Project state dict (``phase``, ``goal``, plus any
                decision-waiting list).
            tasks: Task dicts (planner tasks, roadmap entries, or stubs).
            health: One-line system-health summary.

        Returns:
            The multi-line panel text.
        """
        tasks = [t for t in tasks if isinstance(t, dict)]
        done = [t for t in tasks if _bucket(_task_status(t)) == "done"]
        queued = [t for t in tasks if _bucket(_task_status(t)) == "queued"]
        total = len(tasks)
        percent = (len(done) * 100 // total) if total else 0

        goal = _truncate(_as_str(state.get("goal")), _GOAL_MAX)
        phase = _as_str(state.get("phase"), "UNKNOWN")
        current = _current_task(tasks)
        objective = _task_title(current) if current is not None else "—"
        decisions = _decisions_needed(state)

        lines = [
            "DRAUPNIR FORGE",
            f"Project: {goal}",
            f"Overall: {percent}%",
            f"Current objective: {objective}",
            f"Current phase: {phase}",
            "",
            "Completed:",
        ]
        shown_done = done[:_COMPLETED_MAX]
        if shown_done:
            lines.extend(f"✓ {_task_title(t)}" for t in shown_done)
            overflow = len(done) - len(shown_done)
            if overflow:
                lines.append(f"  +{overflow} more")
        else:
            lines.append("  (none)")
        lines.append("")
        lines.append("Now:")
        if current is not None:
            lines.append(f"◉ {_task_title(current)}")
        else:
            lines.append("—")
        lines.append("")
        lines.append("Queued:")
        shown_queued = queued[:_QUEUED_MAX]
        if shown_queued:
            lines.extend(f"○ {_task_title(t)}" for t in shown_queued)
        else:
            lines.append("  (none)")
        lines.append("")
        lines.append("Human decisions needed:")
        if decisions:
            lines.extend(f"- {d}" for d in decisions)
        else:
            lines.append("None")
        lines.append("")
        lines.append("System health:")
        lines.append(_as_str(health, "Stable"))
        return "\n".join(lines)

    @staticmethod
    def render_compact(
        state: Dict[str, Any], tasks: List[Dict[str, Any]]
    ) -> str:
        """Render a one-line status summary suitable for logs.

        Example: ``Draupnir Forge: 53% · phase VERIFYING · now: Verify
        citations · 7 done, 1 active, 5 queued``. Never emits newlines.
        """
        tasks = [t for t in tasks if isinstance(t, dict)]
        buckets = [_bucket(_task_status(t)) for t in tasks]
        total = len(tasks)
        percent = (buckets.count("done") * 100 // total) if total else 0
        phase = _as_str(state.get("phase"), "UNKNOWN")
        current = _current_task(tasks)
        objective = _task_title(current) if current is not None else "—"
        objective = objective.replace("\n", " ")
        summary = (
            f"Draupnir Forge: {percent}% · phase {phase} · "
            f"now: {objective} · "
            f"{buckets.count('done')} done, "
            f"{buckets.count('current')} active, "
            f"{buckets.count('queued')} queued"
        )
        return summary.replace("\n", " ")
