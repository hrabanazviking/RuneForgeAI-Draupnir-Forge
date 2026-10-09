"""tests/test_orchestrator.py — slice 20: Forge Orchestrator core loop.

Drives the loop with scripted stub roles registered in a fresh
RoleRegistry (never the production roles), proving the machine walk,
the failure ladder, and the pause paths.
"""

import json
import shutil
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from draupnir_forge.events import EventLog, EventType
from draupnir_forge.orchestrator import Orchestrator
from draupnir_forge.roles.base import Role, RoleContext, RoleRegistry, RoleResult


@contextmanager
def temp_project():
    tmp = tempfile.mkdtemp(prefix="forge-orch-")
    try:
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def make_stub(name: str,
              behavior: Optional[Callable[[RoleContext, int], RoleResult]] = None,
              results: Optional[List[RoleResult]] = None):
    """Build a scripted Role subclass with a per-class call log.

    ``run`` is defined in the class namespace (via ``type()``) so the
    ABC machinery clears the abstractmethod flag — assigning it after
    class creation would leave the class uninstantiable.
    """
    calls: List[Dict[str, Any]] = []
    script = list(results or [])

    def _run(self, ctx: RoleContext) -> RoleResult:
        calls.append({
            "task_id": ctx.task.task_id if ctx.task is not None else None,
            "repair_hint": (ctx.artifacts or {}).get("repair_hint"),
        })
        if behavior is not None:
            return behavior(ctx, len(calls))
        if script:
            return script.pop(0)
        return RoleResult(ok=True, summary=f"{name} stub ok")

    stub = type("_Stub", (Role,), {"name": name, "run": _run})
    stub.calls = calls  # type: ignore[attr-defined]
    return stub


def ok(summary: str = "stub ok", **artifacts: Any) -> RoleResult:
    return RoleResult(ok=True, summary=summary, artifacts=dict(artifacts))


def fail(summary: str, **artifacts: Any) -> RoleResult:
    return RoleResult(ok=False, summary=summary, artifacts=dict(artifacts))


def two_task_planner(ctx: RoleContext, n: int) -> RoleResult:
    tasks = [
        {"task_id": "T-001", "title": "raise the first post", "domain": "core"},
        {"task_id": "T-002", "title": "raise the second post", "domain": "core",
         "depends_on": ["T-001"]},
    ]
    return ok("planner: roadmap of 2 tasks", tasks=tasks)


def one_task_planner(ctx: RoleContext, n: int) -> RoleResult:
    tasks = [
        {"task_id": "T-001", "title": "the only task", "domain": "core"},
    ]
    return ok("planner: roadmap of 1 task", tasks=tasks)


def happy_registry(planner_behavior=two_task_planner) -> RoleRegistry:
    """Fresh registry where every role succeeds."""
    reg = RoleRegistry()
    for name in ("cartographer", "skald", "architect", "auditor",
                 "tester", "verifier", "scribe"):
        reg.register(make_stub(name))
    reg.register(make_stub("planner", behavior=planner_behavior))
    reg.register(make_stub("forge_worker"))
    return reg


class TestOrchestratorHappyPath(unittest.TestCase):
    def test_two_tasks_to_project_complete(self):
        reg = happy_registry()
        with temp_project() as project:
            orch = Orchestrator(project, role_registry=reg)
            result = orch.run("forge a widget", max_cycles=80)

            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["tasks_done"], 2)
            self.assertEqual(result["state"], "PROJECT_COMPLETE")
            self.assertLessEqual(result["cycles"], 80)

            # Dependency order honored: T-001 implemented before T-002.
            worker = reg._roles["forge_worker"]
            self.assertEqual(
                [c["task_id"] for c in worker.calls], ["T-001", "T-002"])

            # Roadmap persisted with both tasks done.
            roadmap = json.loads(
                Path(project, ".mythis", "roadmap.json").read_text())
            self.assertEqual(
                {t["task_id"]: t["status"] for t in roadmap},
                {"T-001": "done", "T-002": "done"})

            # Key events were emitted.
            log = EventLog(project)
            types = [e.type for e in log.query()]
            self.assertIn(EventType.PROJECT_CREATED, types)
            self.assertIn(EventType.PROJECT_COMPLETED, types)
            self.assertEqual(
                types.count(EventType.TASK_COMPLETED), 2)

            # No human was bothered.
            self.assertFalse(
                Path(project, ".mythis", "awaiting_human.md").exists())

    def test_already_complete_returns_immediately(self):
        reg = happy_registry()
        with temp_project() as project:
            first = Orchestrator(project, role_registry=reg)
            self.assertEqual(first.run("x", max_cycles=80)["status"],
                             "complete")
            second = Orchestrator(project, role_registry=happy_registry())
            result = second.run("x", max_cycles=80)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["cycles"], 0)

    def test_max_cycles_exhausted_blocks(self):
        reg = happy_registry()
        with temp_project() as project:
            orch = Orchestrator(project, role_registry=reg)
            result = orch.run("forge a widget", max_cycles=3)
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["cycles"], 3)


class TestOrchestratorPause(unittest.TestCase):
    def test_verifier_demands_human_pauses(self):
        reg = happy_registry(planner_behavior=one_task_planner)
        reg._roles.pop("verifier")
        reg.register(make_stub(
            "verifier",
            results=[fail("verifier: blocked — human decision required "
                          "to proceed",
                          escalation="the human must decide")],
        ))
        with temp_project() as project:
            orch = Orchestrator(project, role_registry=reg)
            result = orch.run("forge a widget", max_cycles=80)

            self.assertEqual(result["status"], "paused")
            self.assertEqual(result["state"], "HUMAN_DECISION")
            self.assertEqual(result["tasks_done"], 0)
            question = Path(project, ".mythis", "awaiting_human.md")
            self.assertTrue(question.exists())
            text = question.read_text(encoding="utf-8")
            self.assertIn("human decision required", text)

    def test_missing_role_pauses(self):
        reg = happy_registry()  # no heimdallr; drop scribe too
        reg._roles.pop("scribe")
        with temp_project() as project:
            orch = Orchestrator(project, role_registry=reg)
            result = orch.run("forge a widget", max_cycles=120)
            self.assertEqual(result["status"], "paused")
            self.assertEqual(result["state"], "HUMAN_DECISION")
            # The first task completed before DOCUMENTING stalled.
            self.assertEqual(result["tasks_done"], 1)

    def test_heimdallr_no_progress_pauses(self):
        reg = happy_registry()
        watchman = make_stub(
            "heimdallr",
            behavior=lambda ctx, n: ok("horn sounded"),
        )
        # Give the double a check() shaped like Heimdallr.check().
        def check(self, history, no_progress_cycles=5):
            return ["no progress in 99 consecutive cycles "
                    "(threshold 15) — runaway loop suspected"]
        watchman.check = check.__get__(watchman, watchman)
        reg.register(watchman)
        with temp_project() as project:
            orch = Orchestrator(project, role_registry=reg)
            result = orch.run("forge a widget", max_cycles=80)
            self.assertEqual(result["status"], "paused")
            text = Path(project, ".mythis",
                         "awaiting_human.md").read_text(encoding="utf-8")
            self.assertIn("heimdallr", text)


class TestOrchestratorRepairLadder(unittest.TestCase):
    def test_repair_gives_up_after_three_then_replans_then_pauses(self):
        reg = RoleRegistry()
        for name in ("cartographer", "skald", "architect", "tester",
                     "verifier", "scribe"):
            reg.register(make_stub(name))
        reg.register(make_stub("planner", behavior=one_task_planner))
        reg.register(make_stub(
            "auditor",
            results=[ok("auditor: findings noted",
                        findings="the diff smells of haste")] * 10,
        ))
        reg.register(make_stub(
            "forge_worker",
            behavior=lambda ctx, n: fail(
                "worker: Traceback (most recent call last): build exploded"),
        ))
        with temp_project() as project:
            orch = Orchestrator(project, role_registry=reg)
            result = orch.run("forge a widget", max_cycles=300)

            worker_calls = reg._roles["forge_worker"].calls
            planner_calls = reg._roles["planner"].calls
            # 1 initial run + 3 repair runs, then a replan, then the
            # same again before the human is asked.
            self.assertEqual(len(worker_calls), 8)
            self.assertEqual(len(planner_calls), 2)
            self.assertEqual(result["status"], "paused")
            self.assertEqual(result["state"], "HUMAN_DECISION")
            self.assertEqual(result["tasks_done"], 0)
            text = Path(project, ".mythis",
                         "awaiting_human.md").read_text(encoding="utf-8")
            self.assertIn("already revised the roadmap once", text)

    def test_repair_hint_reaches_worker(self):
        seen_hints = []

        def worker_behavior(ctx: RoleContext, n: int) -> RoleResult:
            hint = (ctx.artifacts or {}).get("repair_hint")
            if hint:
                seen_hints.append(hint)
            if n < 2:
                return fail("worker: Traceback (most recent call last): boom")
            return ok("worker: mended")

        reg = happy_registry(planner_behavior=one_task_planner)
        reg._roles.pop("forge_worker")
        reg.register(make_stub("forge_worker", behavior=worker_behavior))
        with temp_project() as project:
            orch = Orchestrator(project, role_registry=reg)
            result = orch.run("forge a widget", max_cycles=80)
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["tasks_done"], 1)
            self.assertTrue(seen_hints, "worker never saw a repair hint")
            self.assertIn("implementation_error", seen_hints[0])


if __name__ == "__main__":
    unittest.main()
