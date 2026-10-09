"""reground.py — Slice 38: the re-grounding cycle, the Forge finding its feet again.

Drift has been sighted, the map no longer matches the land — so the Forge
re-grounds itself: it re-runs the Cartographer over the living
repository, diffs the fresh chart against the stored
``.mythis/domain_map.json`` (added and removed files), validates every
roadmap task (do its prerequisites still exist? is its status still
sane?), marks stale tasks with a note in ``roadmap.json``, and runs the
drift detector. When drift is high-severity, the cycle recommends the
Architect re-run the design.

A ``PROJECT_REGROUNDED`` event is emitted best-effort — a silent ledger
must never sink a re-grounding.

Returns ``{"regrounded": True, "drift": [...], "stale_tasks": [...],
"needs_architect": bool}``.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

from draupnir_forge.drift import (
    DriftDetector,
    DriftFinding,
    SEVERITY_HIGH,
)
from draupnir_forge.events import EventLog, EventType
from draupnir_forge.roadmap import TaskGraph
from draupnir_forge.roles import cartographer
from draupnir_forge.tasks import ForgeTask

log = logging.getLogger("draupnir_forge.reground")

#: File references inside task text look like ``src/foo/bar.py``.
_FILE_REF_RE = re.compile(
    r"(?<![\w.])((?:[\w.\-]+/)+[\w.\-]+\.(?:py|md|json|yaml|yml|toml|txt|js|ts|"
    r"java|go|rs|c|h|cpp|sh|sql|css|html))(?!/)"
)

#: Statuses that describe finished work — their files should still exist.
_DONE_STATUSES = frozenset({"done"})


def _old_domain_files(project_dir: Path) -> Optional[Set[str]]:
    """File paths from the stored ``.mythis/domain_map.json``.

    Returns None when the stored map is absent or unreadable, so the
    caller can distinguish "no baseline" from "empty repo".
    """
    path = project_dir / ".mythis" / "domain_map.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError) as exc:
        log.warning("Stored domain map unavailable (%s)", exc)
        return None
    files = raw.get("files", []) if isinstance(raw, dict) else []
    return {str(f.get("path", "")) for f in files
            if isinstance(f, dict) and f.get("path")}


def _referenced_files(task: ForgeTask) -> List[str]:
    """File paths a task's text claims it works on."""
    texts = [task.goal or "", task.title or "",
             task.notes or ""] + list(task.constraints or []) \
        + list(task.acceptance or []) + list(task.verification or [])
    found: List[str] = []
    for text in texts:
        for match in _FILE_REF_RE.finditer(str(text)):
            candidate = match.group(1)
            if candidate not in found:
                found.append(candidate)
    return found


def _validate_task(project_dir: Path, task: ForgeTask) -> Optional[str]:
    """Check one task against the live repo.

    Returns a stale-reason string when the task no longer makes sense,
    or None when it stands on solid ground.
    """
    referenced = _referenced_files(task)
    missing = [ref for ref in referenced
               if not (project_dir / ref).exists()]
    if missing and task.status in _DONE_STATUSES:
        return (f"re-grounding {datetime.now(timezone.utc).date().isoformat()}: "
                f"task is '{task.status}' but these referenced files are "
                f"gone: {', '.join(missing)}")
    if missing and len(missing) == len(referenced) and referenced:
        return (f"re-grounding {datetime.now(timezone.utc).date().isoformat()}: "
                f"every referenced file is gone: {', '.join(missing)}")
    return None


def _mark_stale(graph: TaskGraph, task_id: str, reason: str) -> None:
    """Append a re-grounding note to a task and persist the roadmap."""
    task = graph.get(task_id)
    note = reason.strip()
    existing = (task.notes or "").strip()
    if note and note not in existing:
        task.notes = (existing + "\n" + note).strip() if existing else note
        graph._save()  # same package: persist the annotated roadmap


def _emit_regrounded(project_dir: Path, payload: Dict[str, Any]) -> None:
    """Emit PROJECT_REGROUNDED best-effort; never raises."""
    try:
        EventLog(project_dir).emit(
            EventType.PROJECT_REGROUNDED,
            "reground",
            payload,
        )
    except Exception as exc:
        log.warning("Could not emit PROJECT_REGROUNDED: %s", exc)


def reground(project_dir: Union[str, Path]) -> Dict[str, Any]:
    """Re-ground the Forge's picture of the project. Never raises.

    Re-runs the Cartographer, diffs the fresh chart against the stored
    domain map, validates every roadmap task (stale ones get a note),
    runs drift detection, and emits PROJECT_REGROUNDED best-effort.

    Args:
        project_dir: Root of the forge project holding ``.mythis/``.

    Returns:
        ``{"regrounded": True, "added_files": [...], "removed_files":
        [...], "drift": [finding dicts], "stale_tasks": [task ids],
        "needs_architect": bool}``.
    """
    root = Path(project_dir)
    added_files: List[str] = []
    removed_files: List[str] = []
    stale_tasks: List[str] = []
    drift: List[DriftFinding] = []

    # 1. Fresh chart vs the stored map.
    try:
        chart = cartographer.map_repo(str(root))
        fresh = {info.path for info in chart.files}
    except Exception as exc:
        log.warning("Cartographer re-map failed: %s", exc)
        fresh = set()
    old = _old_domain_files(root)
    if old is not None:
        added_files = sorted(fresh - old)
        removed_files = sorted(old - fresh)

    # 2. Validate every roadmap task; annotate the stale ones.
    try:
        graph = TaskGraph(root)
        for task_id in graph.task_ids():
            try:
                task = graph.get(task_id)
            except KeyError:
                continue
            reason = _validate_task(root, task)
            if reason:
                _mark_stale(graph, task_id, reason)
                stale_tasks.append(task_id)
    except Exception as exc:
        log.warning("Roadmap validation failed: %s", exc)

    # 3. Drift against the architecture baseline.
    try:
        drift = DriftDetector(root).compare()
    except Exception as exc:
        log.warning("Drift detection failed: %s", exc)
        drift = []

    needs_architect = any(f.severity == SEVERITY_HIGH for f in drift)

    result: Dict[str, Any] = {
        "regrounded": True,
        "added_files": added_files,
        "removed_files": removed_files,
        "drift": [f.to_dict() for f in drift],
        "stale_tasks": stale_tasks,
        "needs_architect": needs_architect,
    }

    _emit_regrounded(root, {
        "added_files": len(added_files),
        "removed_files": len(removed_files),
        "stale_tasks": stale_tasks,
        "drift_kinds": [f.kind.value for f in drift],
        "needs_architect": needs_architect,
    })
    return result
