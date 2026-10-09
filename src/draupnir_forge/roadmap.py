"""roadmap.py — Slice 22: the machine task graph, the plan's beating heart.

The Planner (slice 13) dreams the roadmap; :class:`TaskGraph` keeps it
honest at runtime. It loads ``.mythis/roadmap.json``, answers which
tasks stand ready (status ``ready`` with every dependency done), marks
progress, splits oversized tasks (the TASK_TOO_LARGE rite), and
validates the graph's integrity — unknown dependencies and dependency
cycles are rejected loudly.

Like a well-kept loom, every mutation is persisted atomically
(write-temp-then-rename), so a crash mid-weave never leaves the
roadmap torn. A missing or unreadable roadmap file heals to an empty
graph rather than crashing; :meth:`validate` is where strictness lives.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from draupnir_forge.tasks import ForgeTask

log = logging.getLogger("draupnir_forge.roadmap")

ROADMAP_FILE = "roadmap.json"
_MYTHIS = ".mythis"

_TASK_ID_RE = re.compile(r"^T-(\d{3,})$")


class TaskGraph:
    """Runtime engine over the persisted task graph.

    Args:
        project_dir: Root of the forge project holding ``.mythis/``.
    """

    def __init__(self, project_dir: Union[str, Path]) -> None:
        self.project_dir = Path(project_dir)
        self._path = self.project_dir / _MYTHIS / ROADMAP_FILE
        # _tasks maps id -> ForgeTask; _order keeps insertion order stable.
        self._tasks: Dict[str, ForgeTask] = {}
        self._order: List[str] = []
        self._load()

    # -- loading / persistence --------------------------------------

    def _load(self) -> None:
        """Read the roadmap from disk; heal to an empty graph on trouble."""
        try:
            raw_text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return  # No roadmap yet — an empty loom is a fine start.
        except OSError as exc:
            log.warning("Could not read roadmap %s: %s", self._path, exc)
            return
        try:
            raw = json.loads(raw_text)
        except ValueError as exc:
            log.warning("Roadmap %s is not valid JSON (%s); starting empty",
                        self._path, exc)
            return
        items = raw if isinstance(raw, list) else raw.get("tasks", [])
        if not isinstance(items, list):
            log.warning("Roadmap %s has no task list; starting empty", self._path)
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                task = ForgeTask.from_dict(item)
            except (TypeError, ValueError) as exc:
                log.warning("Skipping malformed task entry: %s", exc)
                continue
            if task.task_id in self._tasks:
                log.warning("Duplicate task id %r; keeping the first",
                            task.task_id)
                continue
            self._tasks[task.task_id] = task
            self._order.append(task.task_id)

    def _save(self) -> None:
        """Persist the graph atomically (tmp file + rename). Never raises."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(self._path.name + ".tmp")
            payload = [self._tasks[tid].to_dict() for tid in self._order]
            tmp.write_text(json.dumps(payload, indent=2) + "\n",
                           encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError as exc:
            log.warning("Could not persist roadmap %s: %s", self._path, exc)

    # -- queries -----------------------------------------------------

    def task_ids(self) -> List[str]:
        """Task ids in insertion order."""
        return list(self._order)

    def get(self, task_id: str) -> ForgeTask:
        """Fetch one task by id.

        Raises:
            KeyError: If no task bears that id.
        """
        try:
            return self._tasks[task_id]
        except KeyError:
            raise KeyError(f"No such task in the roadmap: {task_id!r}") from None

    def ready_tasks(self) -> List[ForgeTask]:
        """Tasks that are ``ready`` and whose dependencies are all ``done``."""
        ready: List[ForgeTask] = []
        for task_id in self._order:
            task = self._tasks[task_id]
            if task.status != "ready":
                continue
            if all(self._tasks.get(dep) is not None
                   and self._tasks[dep].status == "done"
                   for dep in task.depends_on):
                ready.append(task)
        return ready

    def dependents(self, task_id: str) -> List[ForgeTask]:
        """Tasks that directly depend on ``task_id``."""
        self.get(task_id)  # loud if unknown
        return [self._tasks[tid] for tid in self._order
                if task_id in self._tasks[tid].depends_on]

    # -- mutations ---------------------------------------------------

    def _set_status(self, task_id: str, status: str) -> ForgeTask:
        task = self.get(task_id)
        task.status = status  # ForgeTask validates the status value.
        self._save()
        return task

    def mark_complete(self, task_id: str) -> ForgeTask:
        """Mark a task ``done`` and persist."""
        return self._set_status(task_id, "done")

    def mark_failed(self, task_id: str) -> ForgeTask:
        """Mark a task ``failed`` and persist."""
        return self._set_status(task_id, "failed")

    def mark_in_progress(self, task_id: str) -> ForgeTask:
        """Mark a task ``in_progress`` and persist."""
        return self._set_status(task_id, "in_progress")

    def add_task(self, spec: Dict[str, Any]) -> ForgeTask:
        """Add a task from a spec dict; assigns the next ``T-NNN`` id.

        If ``spec`` already carries a ``task_id`` it is honored (and must
        be unique). Persists.

        Raises:
            ValueError: On a duplicate task id.
        """
        data = dict(spec)
        task_id = str(data.get("task_id") or "").strip()
        if not task_id:
            task_id = self._next_task_id()
            data["task_id"] = task_id
        if task_id in self._tasks:
            raise ValueError(f"Task id already in the roadmap: {task_id!r}")
        task = ForgeTask.from_dict(data)
        self._tasks[task.task_id] = task
        self._order.append(task.task_id)
        self._save()
        return task

    def _next_task_id(self) -> str:
        """Next free ``T-NNN`` id (one past the highest numeric id seen)."""
        highest = 0
        for task_id in self._tasks:
            match = _TASK_ID_RE.match(task_id)
            if match:
                highest = max(highest, int(match.group(1)))
        return f"T-{highest + 1:03d}"

    def split_task(self, task_id: str, parts: List[Dict[str, Any]]) -> List[str]:
        """Split one task into sequential parts (the TASK_TOO_LARGE rite).

        The original task is *replaced* by its parts: part ``m1`` inherits
        the original's dependencies, each later part depends on the one
        before it, and every task that depended on the original now
        depends on the *last* part. New ids look like ``T-001-m1``.

        Args:
            task_id: The oversized task to split.
            parts: One spec dict per part (title, domain, goal, ...).

        Returns:
            The new part ids in order.

        Raises:
            KeyError: If the task does not exist.
            ValueError: If ``parts`` is empty or a part id collides.
        """
        original = self.get(task_id)
        if not parts:
            raise ValueError("Cannot split a task into zero parts")
        new_ids = [f"{task_id}-m{i + 1}" for i in range(len(parts))]
        for new_id in new_ids:
            if new_id in self._tasks:
                raise ValueError(
                    f"Split part id already in the roadmap: {new_id!r}")
        previous: Optional[str] = None
        new_tasks: List[ForgeTask] = []
        for new_id, spec in zip(new_ids, parts):
            data = dict(spec)
            data["task_id"] = new_id
            data["status"] = "ready"
            # m1 inherits the original's deps; the rest chain on each other.
            data["depends_on"] = ([previous] if previous is not None
                                  else list(original.depends_on))
            new_tasks.append(ForgeTask.from_dict(data))
            previous = new_id
        last_part = new_ids[-1]
        # Rewire: original dependents now wait on the last part instead.
        for dependent in self.dependents(task_id):
            dependent.depends_on = [
                last_part if dep == task_id else dep
                for dep in dependent.depends_on
            ]
        # Replace the original with its parts, in place.
        position = self._order.index(task_id)
        del self._tasks[task_id]
        for offset, task in enumerate(new_tasks):
            self._tasks[task.task_id] = task
        self._order[position:position + 1] = new_ids
        self._save()
        return new_ids

    # -- validation --------------------------------------------------

    def validate(self) -> None:
        """Check the graph's integrity; loud on any flaw.

        Raises:
            ValueError: On unknown dependencies or dependency cycles.
        """
        for task_id in self._order:
            task = self._tasks[task_id]
            for dep in task.depends_on:
                if dep not in self._tasks:
                    raise ValueError(
                        f"Task {task_id!r} depends on unknown task {dep!r}")
        cycle = self._find_cycle()
        if cycle:
            raise ValueError(
                "Dependency cycle detected: " + " -> ".join(cycle))

    def _find_cycle(self) -> List[str]:
        """Return one dependency cycle as an id path, or [] if acyclic."""
        visiting: List[str] = []
        visited: set = set()

        def visit(node: str) -> Optional[List[str]]:
            if node in visiting:
                return visiting[visiting.index(node):] + [node]
            if node in visited:
                return None
            visiting.append(node)
            for dep in self._tasks[node].depends_on:
                found = visit(dep)
                if found:
                    return found
            visiting.pop()
            visited.add(node)
            return None

        for task_id in self._order:
            found = visit(task_id)
            if found:
                return found
        return []
