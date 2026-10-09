"""planner.py — Planner role (slice 13).

The Planner is the strategist of the Forge: it takes an architecture and
a vision, plus a list of task specs, and compiles them into an ordered
task graph of :class:`ForgeTask` — each with a stable ``T-001`` style id,
a domain, dependencies, goal, constraints, acceptance criteria, and
verification steps. It validates the graph (unknown deps and cycles are
rejected loudly), topologically sorts it, and persists the roadmap as
``.mythis/roadmap.json`` plus a human-readable ``ROADMAP.md``.

``ForgeTask`` is defined here and re-exported from
``draupnir_forge.tasks`` for the Orchestrator (slice 20) to use.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from draupnir_forge.roles.base import (
    Role,
    RoleContext,
    RoleResult,
    register_role,
)

log = logging.getLogger("draupnir_forge.roles.planner")

ROADMAP_JSON = "roadmap.json"
ROADMAP_MD = "ROADMAP.md"

TASK_ID_RE = re.compile(r"^T-\d{3,}$")
VALID_STATUSES = ("ready", "blocked", "in_progress", "done", "failed")


# ---------------------------------------------------------------------------
# ForgeTask
# ---------------------------------------------------------------------------


@dataclass
class ForgeTask:
    """One unit of forge work: what, where, and how it is proven done."""

    task_id: str
    title: str
    domain: str
    status: str = "ready"
    depends_on: List[str] = field(default_factory=list)
    goal: str = ""
    constraints: List[str] = field(default_factory=list)
    acceptance: List[str] = field(default_factory=list)
    verification: List[str] = field(default_factory=list)
    notes: str = ""  # operator/re-grounding annotations; free text

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(
                f"Invalid task status {self.status!r}; "
                f"must be one of {VALID_STATUSES}"
            )
        self.depends_on = [str(d) for d in self.depends_on]
        self.constraints = [str(c) for c in self.constraints]
        self.acceptance = [str(a) for a in self.acceptance]
        self.verification = [str(v) for v in self.verification]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "domain": self.domain,
            "status": self.status,
            "depends_on": list(self.depends_on),
            "goal": self.goal,
            "constraints": list(self.constraints),
            "acceptance": list(self.acceptance),
            "verification": list(self.verification),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ForgeTask":
        return cls(
            task_id=str(data.get("task_id", "")),
            title=str(data.get("title", "")),
            domain=str(data.get("domain", "")),
            status=str(data.get("status", "ready")),
            depends_on=[str(d) for d in data.get("depends_on", []) or []],
            goal=str(data.get("goal", "")),
            constraints=[str(c) for c in data.get("constraints", []) or []],
            acceptance=[str(a) for a in data.get("acceptance", []) or []],
            verification=[str(v) for v in data.get("verification", []) or []],
            notes=str(data.get("notes", "") or ""),
        )


# ---------------------------------------------------------------------------
# Roadmap compilation
# ---------------------------------------------------------------------------


def _task_id_for_index(index: int) -> str:
    """Stable id for the spec at position ``index``: T-001, T-002, ..."""
    return f"T-{index + 1:03d}"


def _resolve_dep(raw: Any, index_of: Dict[str, int]) -> str:
    """Resolve one depends_on entry to a task id.

    Accepts a ``T-00N`` id string or a 0-based integer index into the
    spec list. Raises ``ValueError`` for anything unresolvable.
    """
    if isinstance(raw, bool):
        raise ValueError(f"Invalid dependency reference: {raw!r}")
    if isinstance(raw, int):
        if raw < 0 or raw >= len(index_of):
            raise ValueError(f"Dependency index out of range: {raw}")
        return _task_id_for_index(raw)
    text = str(raw).strip()
    if not TASK_ID_RE.match(text):
        raise ValueError(
            f"Invalid dependency reference {raw!r}: "
            "use a T-00N task id or a 0-based spec index"
        )
    if text not in index_of:
        raise ValueError(f"Dependency {text!r} does not match any task")
    return text


def _topological_sort(tasks: List[ForgeTask]) -> List[ForgeTask]:
    """Order tasks so every dependency comes before its dependent.

    Kahn's algorithm over the dependency edges; ties keep spec order
    so the result is deterministic. Raises ``ValueError`` on cycles.
    """
    by_id = {t.task_id: t for t in tasks}
    indegree = {t.task_id: 0 for t in tasks}
    dependents: Dict[str, List[str]] = {t.task_id: [] for t in tasks}
    for task in tasks:
        for dep in task.depends_on:
            if dep == task.task_id:
                raise ValueError(f"Task {task.task_id} depends on itself")
            indegree[task.task_id] += 1
            dependents[dep].append(task.task_id)

    queue = [t.task_id for t in tasks if indegree[t.task_id] == 0]
    ordered: List[ForgeTask] = []
    while queue:
        tid = queue.pop(0)
        ordered.append(by_id[tid])
        for dependent in dependents[tid]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                queue.append(dependent)

    if len(ordered) != len(tasks):
        stuck = sorted(t for t, d in indegree.items() if d > 0)
        raise ValueError(
            "Dependency cycle detected involving: " + ", ".join(stuck)
        )
    return ordered


def compile_roadmap(
    architecture: Dict[str, Any],
    vision: Dict[str, Any],
    task_specs: List[Dict[str, Any]],
) -> List[ForgeTask]:
    """Compile task specs into an ordered, validated list of ForgeTasks.

    Assigns stable ``T-001`` ids in spec order, validates that every
    dependency exists and every domain is known, then topologically
    sorts. Tasks with no dependencies start ``ready``; tasks with
    dependencies start ``blocked`` (the Orchestrator promotes them to
    ``ready`` as their dependencies complete).

    Raises:
        ValueError: On unknown domains, unknown dependencies, or
            dependency cycles.
    """
    if not isinstance(task_specs, list) or not task_specs:
        raise ValueError("compile_roadmap needs a non-empty task_specs list")
    architecture = architecture or {}
    vision = vision or {}

    known_domains = {
        str(d.get("name", ""))
        for d in (architecture.get("domains", []) or [])
        if isinstance(d, dict)
    }
    known_domains.discard("")

    # First pass: assign ids so specs can reference each other.
    index_of = {_task_id_for_index(i): i for i in range(len(task_specs))}

    tasks: List[ForgeTask] = []
    for i, spec in enumerate(task_specs):
        if not isinstance(spec, dict):
            raise ValueError(f"Task spec {i} must be a mapping")
        title = str(spec.get("title", "") or "").strip()
        if not title:
            raise ValueError(f"Task spec {i} needs a title")
        domain = str(spec.get("domain", "") or "").strip()
        if known_domains and domain not in known_domains:
            raise ValueError(
                f"Task {title!r} names unknown domain {domain!r}; "
                f"known: {sorted(known_domains)}"
            )
        raw_deps = spec.get("depends_on", []) or []
        if isinstance(raw_deps, str):
            raw_deps = [raw_deps]
        depends_on = [_resolve_dep(d, index_of) for d in raw_deps]

        status = str(spec.get("status", "") or "").strip() or (
            "ready" if not depends_on else "blocked"
        )
        tasks.append(
            ForgeTask(
                task_id=_task_id_for_index(i),
                title=title,
                domain=domain,
                status=status,
                depends_on=depends_on,
                goal=str(spec.get("goal", "") or ""),
                constraints=[str(c) for c in spec.get("constraints", []) or []],
                acceptance=[str(a) for a in spec.get("acceptance", []) or []],
                verification=[str(v) for v in spec.get("verification", []) or []],
            )
        )

    ordered = _topological_sort(tasks)
    log.info("Compiled roadmap: %d tasks", len(ordered))
    return ordered


def next_ready(tasks: List[ForgeTask]) -> Optional[ForgeTask]:
    """Return the first ready task whose dependencies are all done.

    A task counts as ready only when its status is ``ready`` *and*
    every task it depends on has status ``done`` — belt and
    suspenders, in case a status went stale. Returns ``None`` when no
    task can run yet.
    """
    by_id = {t.task_id: t for t in tasks}
    for task in tasks:
        if task.status != "ready":
            continue
        if all(
            by_id.get(dep) is not None and by_id[dep].status == "done"
            for dep in task.depends_on
        ):
            return task
    return None


# ---------------------------------------------------------------------------
# Role
# ---------------------------------------------------------------------------


@register_role
class Planner(Role):
    """Compiles architecture + vision + task specs into a task graph."""

    name = "planner"
    purpose = (
        "Turn an architecture and a vision into an ordered, validated "
        "roadmap of ForgeTasks with dependencies, acceptance criteria, "
        "and verification steps."
    )

    def starter_tasks(self, architecture: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Generate three real starter tasks from the architecture domains.

        Used when the caller provides no task specs: a scaffold task,
        then tests, then docs — each genuinely derived from the
        domains the Architect designed.
        """
        domains = [
            str(d.get("name", ""))
            for d in (architecture.get("domains", []) or [])
            if isinstance(d, dict) and d.get("name")
        ]
        first = domains[0] if domains else "core"
        named = ", ".join(domains) if domains else "the project domains"
        return [
            {
                "title": f"Scaffold the {named} domain package(s)",
                "domain": first,
                "depends_on": [],
                "goal": (
                    "Create the package skeleton for every designed domain "
                    "so each has an importable home for its future code."
                ),
                "constraints": [
                    "Do not change any existing working code.",
                    "Keep the layout the Architect designed; no new top-level domains.",
                ],
                "acceptance": [
                    "Every domain has a package directory with __init__.py.",
                    "Each package imports cleanly with no errors.",
                ],
                "verification": [
                    "python -c 'import <each domain package>' succeeds.",
                    "unittest discovery finds the new packages.",
                ],
            },
            {
                "title": f"Write unit tests for the {first} domain scaffold",
                "domain": first,
                "depends_on": [0],
                "goal": (
                    "Prove the scaffold is real: unit tests that import "
                    "every domain package and assert the designed public "
                    "interface surface exists."
                ),
                "constraints": ["Tests must use the stdlib unittest framework."],
                "acceptance": [
                    "One test module per domain package.",
                    "The full suite passes.",
                ],
                "verification": ["python -m unittest discover -s tests -q passes."],
            },
            {
                "title": "Document the interfaces in INTERFACES.md",
                "domain": first,
                "depends_on": [1],
                "goal": (
                    "Turn the Architect's mined interfaces into maintained "
                    "human documentation: what each public function and "
                    "class is for, per domain."
                ),
                "constraints": [
                    "Document only what exists; do not invent APIs.",
                ],
                "acceptance": [
                    "INTERFACES.md covers every module the Architect listed.",
                    "Each public name has a one-line description.",
                ],
                "verification": [
                    "Every module in architecture.json appears in INTERFACES.md.",
                ],
            },
        ]

    def run(self, ctx: RoleContext) -> RoleResult:
        """Compile the roadmap and write .mythis/roadmap.json + ROADMAP.md."""
        try:
            architecture = ctx.artifacts.get("architecture")
            if not isinstance(architecture, dict) or not architecture:
                return RoleResult(
                    ok=False,
                    summary="planner: ctx.artifacts['architecture'] is required",
                )
            vision = ctx.artifacts.get("vision") or {}
            if not isinstance(vision, dict):
                vision = {}
            task_specs = ctx.artifacts.get("task_specs")
            if task_specs is None:
                task_specs = self.starter_tasks(architecture)
                log.info("No task_specs given; generated %d starter tasks",
                         len(task_specs))

            tasks = compile_roadmap(architecture, vision, task_specs)

            mythis = Path(ctx.project_dir) / ".mythis"
            mythis.mkdir(parents=True, exist_ok=True)
            json_path = mythis / ROADMAP_JSON
            json_path.write_text(
                json.dumps([t.to_dict() for t in tasks], indent=2) + "\n",
                encoding="utf-8",
            )
            (mythis / ROADMAP_MD).write_text(
                self._render_roadmap_md(vision, tasks), encoding="utf-8"
            )

            try:
                from draupnir_forge.events import EventType

                self.emit(
                    ctx,
                    EventType.ROADMAP_REVISED,
                    {"tasks": len(tasks),
                     "task_ids": [t.task_id for t in tasks]},
                )
            except Exception as exc:  # event emission is best-effort
                log.debug("Event emission skipped: %s", exc)

            return RoleResult(
                ok=True,
                summary=f"planner: compiled roadmap of {len(tasks)} tasks",
                artifacts={
                    "tasks": [t.to_dict() for t in tasks],
                    "roadmap_path": str(json_path),
                },
            )
        except ValueError as exc:
            # Validation failures are honest input errors, not crashes.
            log.warning("planner rejected input: %s", exc)
            return RoleResult(ok=False, summary=f"planner: invalid input: {exc}")
        except Exception as exc:  # the contract: never raise on bad input
            log.warning("planner failed: %s", exc)
            return RoleResult(ok=False, summary=f"planner failed: {exc}")

    @staticmethod
    def _render_roadmap_md(
        vision: Dict[str, Any], tasks: List[ForgeTask]
    ) -> str:
        title = str(vision.get("name") or vision.get("title") or "Project")
        lines = [
            f"# Roadmap — {title}",
            "",
            "Compiled by the Planner role. Dependencies form a DAG: "
            "a task runs only after everything it depends on is done.",
            "",
            "| ID | Title | Domain | Status | Depends on |",
            "|----|-------|--------|--------|------------|",
        ]
        for task in tasks:
            deps = ", ".join(task.depends_on) if task.depends_on else "—"
            lines.append(
                f"| {task.task_id} | {task.title} | {task.domain} "
                f"| {task.status} | {deps} |"
            )
        lines.append("")
        for task in tasks:
            lines.append(f"## {task.task_id} — {task.title}")
            lines.append("")
            if task.goal:
                lines.append(f"**Goal:** {task.goal}")
                lines.append("")
            if task.constraints:
                lines.append("**Constraints:**")
                lines.extend(f"- {c}" for c in task.constraints)
                lines.append("")
            if task.acceptance:
                lines.append("**Acceptance:**")
                lines.extend(f"- [ ] {a}" for a in task.acceptance)
                lines.append("")
            if task.verification:
                lines.append("**Verification:**")
                lines.extend(f"- {v}" for v in task.verification)
                lines.append("")
        return "\n".join(lines)
