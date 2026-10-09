"""orchestrator.py — the Forge's core loop (slice 20).

The Orchestrator walks a project through the state machine
(:mod:`draupnir_forge.machine`), dispatching one role per state and
carrying each task through the pipeline

    IMPLEMENTING → REVIEWING → TESTING → VERIFYING → COMPLETE_TASK

with VERIFYING as the funnel where every failure is classified and
routed: REPAIR (bounded — at most three repair runs per task) →
REPLAN (the planner revises once) → HUMAN_DECISION (persist a question
file, pause, and return a ``paused`` status).

A failure in IMPLEMENTING, REVIEWING, or TESTING is recorded and the
pipeline walks forward (later stages skip their roles) until VERIFYING,
the only state the transition map lets choose the remedy — the map
governs, the Orchestrator obeys.

Heimdallr watches every cycle for repeated failures and runaway loops.
Every state change is recorded to the event log. The loop is
deterministic: roles may deliberate, but the machine governs.

Result of :meth:`Orchestrator.run`::

    {"status": "complete" | "paused" | "blocked",
     "tasks_done": <int>, "cycles": <int>, "state": <final state>}
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .events import EventLog, EventType, utc_now_iso
from .failures import FailureClass, FailureRecord, classify_failure, escalation_for
from .machine import ForgeMachine, IllegalTransition, STATE_ROLE_MAP
from .roles.base import (
    RoleContext,
    RoleResult,
    RoleRegistry,
    registry as _global_registry,
)
from .tasks import ForgeTask

__all__ = ["Orchestrator", "MAX_REPAIR_ATTEMPTS", "MAX_REPLANS_PER_TASK"]

log = logging.getLogger("draupnir_forge.orchestrator")

# Bounded repair: a task gets at most this many repair runs (REPAIR
# visits) before the Orchestrator stops hammering and replans instead.
MAX_REPAIR_ATTEMPTS = 3

# Bounded replanning: the planner revises a failing task's roadmap at
# most once; a second failure after revision goes to the human.
MAX_REPLANS_PER_TASK = 1

# Heimdallr's no-progress window, in cycles. One full task pipeline is
# ~9 state visits, so the watchman only barks when a task's worth of
# work passes with no completion.
HEIMDALLR_NO_PROGRESS_CYCLES = 15

# Pipeline states where a role does the stage's work.
_PIPELINE_STATES = ("IMPLEMENTING", "REVIEWING", "TESTING", "VERIFYING")

# Planning states: a role failure here means the forge cannot think
# straight, so the human is asked rather than looping blindly.
_PLANNING_STATES = ("DISCOVERY", "DEFINITION", "ARCHITECTURE", "ROADMAP",
                    "DOCUMENTING")

# Failure classes that always need the human, no matter the budget left.
_HUMAN_ONLY_CLASSES = frozenset({
    FailureClass.USER_DECISION_REQUIRED,
    FailureClass.AMBIGUOUS_REQUIREMENT,
})

_AWAITING_HUMAN_FILENAME = "awaiting_human.md"


class Orchestrator:
    """Drives the Forge state machine from goal to PROJECT_COMPLETE.

    Args:
        project_dir: The project root (``.mythis/`` lives beneath it).
        config: Optional :class:`~draupnir_forge.config.ForgeConfig`.
            When omitted, the layered config is loaded for the project.
        role_registry: Optional :class:`RoleRegistry`. Defaults to the
            global registry (populated by importing the roles package),
            which holds all ten production roles. Tests pass a fresh
            registry with stub doubles.
        event_log: Optional :class:`EventLog`; created when omitted.
        budget: Optional :class:`~draupnir_forge.budget.Budget`.
    """

    def __init__(
        self,
        project_dir: Union[str, Path],
        config: Any = None,
        role_registry: Optional[RoleRegistry] = None,
        event_log: Optional[EventLog] = None,
        budget: Any = None,
    ) -> None:
        self._project_dir = Path(project_dir)
        self._config = config if config is not None else self._load_config()
        # Importing the roles package fires @register_role for all ten.
        from . import roles as _roles_pkg  # noqa: F401
        self._registry = (role_registry if role_registry is not None
                          else _global_registry)
        self._event_log = (event_log if event_log is not None
                           else EventLog(self._project_dir))
        self._budget = budget if budget is not None else self._build_budget()
        self._machine = ForgeMachine(self._project_dir, self._event_log)

        # Run-scoped working memory, reset at the start of every run().
        self._tasks: List[ForgeTask] = []
        self._artifacts: Dict[str, Any] = {}
        self._current_task: Optional[ForgeTask] = None
        self._failures: List[FailureRecord] = []
        self._history: List[Dict[str, Any]] = []  # heimdallr's watch-list
        self._repair_attempts: Dict[str, int] = {}
        self._replans: Dict[str, int] = {}
        self._audited: Dict[str, bool] = {}
        self._pipeline_failed: bool = False  # an earlier stage already broke
        self._skip_planner_once: bool = False  # REPLAN already ran planner
        self._tasks_done: int = 0
        self._cycles: int = 0

    # -- construction helpers -------------------------------------------

    def _load_config(self) -> Any:
        """Load the layered config; fall back to bare defaults."""
        try:
            from .config import ForgeConfig
            return ForgeConfig.load(self._project_dir)
        except Exception as exc:
            log.warning("config load failed, using empty config: %s", exc)
            return None

    def _build_budget(self) -> Any:
        """Build a Budget from config, or None when that fails."""
        try:
            from .budget import Budget
            max_tokens = 127000
            max_cost = 50.0
            if self._config is not None:
                try:
                    max_tokens = int(self._config.get("budget.max_tokens", max_tokens))
                    max_cost = float(self._config.get("budget.max_cost_usd", max_cost))
                except Exception:
                    pass
            return Budget(self._project_dir, max_tokens, max_cost)
        except Exception as exc:
            log.warning("budget unavailable: %s", exc)
            return None

    # -- the core loop ----------------------------------------------------

    def run(self, goal: str, max_cycles: int = 50) -> Dict[str, Any]:
        """Drive the forge from INTAKE to PROJECT_COMPLETE (or a pause).

        Args:
            goal: The project's goal, recorded at INTAKE.
            max_cycles: Safety bound on state visits; exceeding it
                returns ``status="blocked"`` rather than looping forever.

        Returns:
            ``{"status": "complete"|"paused"|"blocked", "tasks_done": n,
            "cycles": n, "state": <final state>}``.
        """
        self._reset_run_state()
        machine = self._machine
        if machine.is_terminal():
            return self._result("complete")

        while self._cycles < max_cycles and not machine.is_terminal():
            self._cycles += 1
            state = machine.current()
            log.info("cycle %d: state %s", self._cycles, state)

            paused = self._heimdallr_watch()
            if paused is not None:
                return paused

            handler = getattr(self, f"_handle_{state.lower()}", None)
            if handler is None:
                return self._pause_for_human(
                    f"no handler for state {state!r} — the forge does not "
                    "know this road"
                )
            try:
                outcome = handler(goal)
            except IllegalTransition as exc:
                return self._pause_for_human(
                    f"illegal transition attempted: {exc}"
                )
            except Exception as exc:  # the loop itself never crashes
                log.warning("handler for %s raised: %s", state, exc)
                return self._pause_for_human(
                    f"orchestrator handler for {state} raised "
                    f"{type(exc).__name__}: {exc}"
                )
            if outcome is not None:  # a pause/block/complete short-circuit
                return outcome

        if machine.is_terminal():
            self._emit(EventType.PROJECT_COMPLETED, "orchestrator",
                       {"tasks_done": self._tasks_done, "cycles": self._cycles})
            return self._result("complete")
        log.warning("max_cycles=%d exhausted in state %s", max_cycles,
                    machine.current())
        return self._result("blocked")

    def _reset_run_state(self) -> None:
        """Clear per-run memory (a fresh run re-derives everything)."""
        self._tasks = []
        self._artifacts = {}
        self._current_task = None
        self._failures = []
        self._history = []
        self._repair_attempts = {}
        self._replans = {}
        self._audited = {}
        self._pipeline_failed = False
        self._skip_planner_once = False
        self._tasks_done = 0
        self._cycles = 0

    def _result(self, status: str) -> Dict[str, Any]:
        return {
            "status": status,
            "tasks_done": self._tasks_done,
            "cycles": self._cycles,
            "state": self._machine.current(),
        }

    # -- Heimdallr ----------------------------------------------------------

    def _heimdallr_watch(self) -> Optional[Dict[str, Any]]:
        """Ask Heimdallr about loop health once per cycle.

        Returns a paused result when the watchman demands escalation,
        otherwise None (the loop continues).
        """
        try:
            watchman = self._registry.create("heimdallr")
        except KeyError:
            return None  # no watchman registered (e.g. stub registries)
        try:
            escalations = watchman.check(
                self._history,
                no_progress_cycles=HEIMDALLR_NO_PROGRESS_CYCLES,
            )
        except Exception as exc:
            log.warning("heimdallr check failed: %s", exc)
            return None
        if not escalations:
            return None
        self._emit(EventType.INVARIANT_VIOLATED, "heimdallr",
                   {"escalations": list(escalations)})
        task_id = self._current_task.task_id if self._current_task else ""
        failures_here = sum(
            1 for f in self._failures if f.task_id == task_id
        )
        if any("repeated failure" in e for e in escalations):
            if (task_id and failures_here >= MAX_REPAIR_ATTEMPTS
                    and self._machine.current() == "VERIFYING"):
                # The repair budget is spent and the funnel has arrived
                # at the one state that may legally replan: hasten it.
                log.warning("heimdallr: repeated failure on %s — replanning",
                            task_id)
                return self._go_replan(
                    f"heimdallr: task {task_id} failed {failures_here} times"
                )
            log.info("heimdallr noticed repeated failure; the repair "
                     "ladder is still handling it")
        if any("no progress" in e or "budget exceeded" in e
               for e in escalations):
            return self._pause_for_human(
                "heimdallr: " + "; ".join(escalations)
            )
        return None

    # -- role dispatch --------------------------------------------------------

    def _run_role(self, role_name: str,
                  task: Optional[ForgeTask] = None) -> RoleResult:
        """Build a RoleContext and run one role; never raises."""
        try:
            role = self._registry.create(role_name)
        except KeyError:
            return RoleResult(
                ok=False,
                summary=f"orchestrator: role {role_name!r} is not registered",
            )
        ctx = RoleContext(
            project_dir=str(self._project_dir),
            config=self._config,
            event_log=self._event_log,
            state=self._machine.state,
            budget=self._budget,
            task=task,
            artifacts=dict(self._artifacts),
        )
        try:
            result = role.run(ctx)
        except Exception as exc:  # the contract says roles don't raise,
            log.warning("role %s raised: %s", role_name, exc)  # but be sure
            return RoleResult(
                ok=False,
                summary=f"role {role_name} raised {type(exc).__name__}: {exc}",
            )
        if not isinstance(result, RoleResult):
            return RoleResult(
                ok=False,
                summary=f"role {role_name} returned {type(result).__name__} "
                        "instead of RoleResult",
            )
        if isinstance(result.artifacts, dict):
            self._artifacts.update(result.artifacts)
        return result

    def _emit(self, event_type: EventType, actor: str,
              payload: Dict[str, Any]) -> None:
        try:
            self._event_log.emit(event_type, actor, payload)
        except Exception as exc:
            log.warning("event emission failed: %s", exc)

    # -- state handlers ---------------------------------------------------------

    def _handle_intake(self, goal: str) -> Optional[Dict[str, Any]]:
        if goal:
            self._machine.state.update(goal=goal)
        self._emit(EventType.PROJECT_CREATED, "orchestrator",
                   {"goal": self._machine.state.goal})
        self._machine.go("DISCOVERY")
        return None

    def _handle_discovery(self, goal: str) -> Optional[Dict[str, Any]]:
        return self._planning_step("DISCOVERY", "DEFINITION")

    def _handle_definition(self, goal: str) -> Optional[Dict[str, Any]]:
        return self._planning_step("DEFINITION", "ARCHITECTURE")

    def _handle_architecture(self, goal: str) -> Optional[Dict[str, Any]]:
        return self._planning_step("ARCHITECTURE", "ROADMAP")

    def _planning_step(self, state: str,
                       nxt: str) -> Optional[Dict[str, Any]]:
        """Run the state's role; a thinking failure asks the human."""
        role_name = STATE_ROLE_MAP[state]
        assert role_name is not None
        result = self._run_role(role_name)
        if not result.ok:
            failure_class = classify_failure(context=result.summary)
            return self._pause_for_human(
                f"{role_name} failed in {state}: {result.summary} "
                f"(classified {failure_class.value})"
            )
        self._machine.go(nxt)
        return None

    def _handle_roadmap(self, goal: str) -> Optional[Dict[str, Any]]:
        if self._skip_planner_once:
            # REPLAN already ran the planner and reloaded the tasks.
            self._skip_planner_once = False
            self._machine.go("TASK_READY")
            return None
        result = self._run_role("planner")
        if not result.ok:
            failure_class = classify_failure(context=result.summary)
            return self._pause_for_human(
                f"planner failed in ROADMAP: {result.summary} "
                f"(classified {failure_class.value})"
            )
        self._load_tasks(result)
        if not self._tasks:
            return self._pause_for_human(
                "planner produced no tasks — the roadmap is empty"
            )
        self._persist_roadmap()
        self._machine.go("TASK_READY")
        return None

    def _handle_task_ready(self, goal: str) -> Optional[Dict[str, Any]]:
        nxt = self._next_ready_task()
        if nxt is None:
            if all(t.status == "done" for t in self._tasks):
                self._machine.go("PROJECT_COMPLETE")
                return None
            return self._pause_for_human(
                "roadmap stalled: no ready task and work remains "
                "(blocked dependencies?)"
            )
        self._current_task = nxt
        self._pipeline_failed = False
        self._artifacts.pop("repair_hint", None)  # fresh task, fresh counsel
        self._machine.state.update(task_id=nxt.task_id)
        self._machine.go("IMPLEMENTING")
        return None

    def _handle_implementing(self, goal: str) -> Optional[Dict[str, Any]]:
        task = self._current_task
        result = self._run_role("forge_worker", task)
        if not result.ok:
            return self._handle_failure(result, "IMPLEMENTING")
        self._machine.go("REVIEWING")
        return None

    def _handle_reviewing(self, goal: str) -> Optional[Dict[str, Any]]:
        if self._pipeline_failed:
            # An earlier stage already broke: skip the audit and walk
            # forward — VERIFYING alone may route to REPAIR/REPLAN.
            self._machine.go("TESTING")
            return None
        task = self._current_task
        result = self._run_role("auditor", task)
        if not result.ok:
            return self._handle_failure(result, "REVIEWING")
        self._machine.go("TESTING")
        return None

    def _handle_testing(self, goal: str) -> Optional[Dict[str, Any]]:
        if self._pipeline_failed:
            self._machine.go("VERIFYING")
            return None
        task = self._current_task
        result = self._run_role("tester", task)
        if not result.ok:
            return self._handle_failure(result, "TESTING")
        self._emit(EventType.TEST_PASSED, "tester",
                   {"task_id": task.task_id if task else None})
        self._machine.go("VERIFYING")
        return None

    def _handle_verifying(self, goal: str) -> Optional[Dict[str, Any]]:
        task = self._current_task
        task_id = task.task_id if task else ""
        if self._pipeline_failed:
            # Funnel: an earlier stage broke — the decision below routes
            # to REPAIR / REPLAN / HUMAN_DECISION, carrying the real
            # failure class forward.
            failure_class = next(
                (f.failure_class for f in reversed(self._failures)
                 if f.task_id == task_id),
                FailureClass.UNKNOWN,
            )
            return self._route_failure(task_id, failure_class,
                                       "pipeline failed upstream")
        result = self._run_role("verifier", task)
        if not result.ok:
            return self._handle_failure(result, "VERIFYING")
        self._machine.go("COMPLETE_TASK")
        return None

    def _handle_complete_task(self, goal: str) -> Optional[Dict[str, Any]]:
        task = self._current_task
        if task is not None:
            task.status = "done"
            self._persist_roadmap()
            self._tasks_done += 1
            try:
                self._machine.state.increment("tasks_completed")
            except Exception:
                pass
            self._history.append({"task_id": task.task_id, "outcome": "ok"})
            for ledger in (self._repair_attempts, self._replans,
                           self._audited):
                ledger.pop(task.task_id, None)
        self._current_task = None
        self._pipeline_failed = False
        try:
            self._machine.state.update(task_id=None)
        except Exception:
            pass
        self._machine.go("DOCUMENTING")
        return None

    def _handle_documenting(self, goal: str) -> Optional[Dict[str, Any]]:
        return self._planning_step("DOCUMENTING", "REGROUNDING")

    def _handle_regrounding(self, goal: str) -> Optional[Dict[str, Any]]:
        # v1: the ground is re-surveyed by future slices; for now the
        # state is recorded and the loop continues to the next task.
        self._machine.go("TASK_READY")
        return None

    def _handle_repair(self, goal: str) -> Optional[Dict[str, Any]]:
        # REPAIR is the repair *decision* state: the re-run itself happens
        # in IMPLEMENTING with the repair hint in the artifacts. Forward
        # legally; IMPLEMENTING's handler consumes the hint.
        task_id = self._current_task.task_id if self._current_task else ""
        log.info("repairing task %s (attempt %d/%d)", task_id,
                 self._repair_attempts.get(task_id, 0), MAX_REPAIR_ATTEMPTS)
        self._machine.go("IMPLEMENTING")
        return None

    def _handle_replan(self, goal: str) -> Optional[Dict[str, Any]]:
        task = self._current_task
        result = self._run_role("planner", task)
        if not result.ok:
            return self._pause_for_human(
                f"planner failed to revise the roadmap: {result.summary}"
            )
        self._load_tasks(result)
        if task is not None:
            # A revised plan grants the task a fresh repair budget.
            self._repair_attempts.pop(task.task_id, None)
            self._audited.pop(task.task_id, None)
            for t in self._tasks:
                if t.task_id == task.task_id and t.status != "done":
                    t.status = "ready"
        self._persist_roadmap()
        self._skip_planner_once = True
        self._machine.go("ROADMAP")
        return None

    def _handle_human_decision(self, goal: str) -> Optional[Dict[str, Any]]:
        # Reaching here means a pause was requested without a result
        # (e.g. a resumed run finding the question still open).
        return self._pause_for_human(
            "awaiting human decision — see .mythis/awaiting_human.md"
        )

    # -- failure pipeline ----------------------------------------------------------

    def _handle_failure(self, result: RoleResult,
                        stage: str) -> Optional[Dict[str, Any]]:
        """Classify a stage failure and funnel it toward VERIFYING.

        The transition map lets only VERIFYING decide between REPAIR,
        REPLAN, and HUMAN_DECISION, so a failure in an earlier stage is
        recorded here (classified, hinted, counted) and the pipeline
        walks forward — downstream stages skip their roles while
        ``_pipeline_failed`` is set — until VERIFYING routes it.
        """
        task = self._current_task
        task_id = task.task_id if task else ""
        test_output = ""
        if isinstance(result.artifacts, dict):
            test_output = str(result.artifacts.get("test_output", "") or "")
        failure_class = classify_failure(
            test_output=test_output,
            context=f"{stage}: {result.summary}",
        )
        detail = f"{stage}: {result.summary}"
        if result.escalation:
            detail += f" | escalation: {result.escalation}"
        self._failures.append(FailureRecord(
            failure_class=failure_class, task_id=task_id, detail=detail,
        ))
        self._history.append({"task_id": task_id, "outcome": "failed"})
        self._pipeline_failed = True
        self._emit(EventType.TASK_FAILED, stage,
                   {"task_id": task_id, "class": failure_class.value,
                    "detail": detail[:500]})

        # Hard human gates first: some failures are questions, not bugs.
        if failure_class in _HUMAN_ONLY_CLASSES:
            return self._pause_for_human(
                f"task {task_id} needs a human: {detail[:300]} "
                f"(classified {failure_class.value})"
            )

        ladder = escalation_for(self._failures)
        if ladder == "auditor" and not self._audited.get(task_id):
            # Second identical wound: let the Auditor look before
            # hammering again, and fold its findings into the hint.
            audit = self._run_role("auditor", task)
            findings = ""
            if isinstance(audit.artifacts, dict):
                findings = str(audit.artifacts.get("findings", "") or "")
            self._artifacts["repair_hint"] = (
                f"{stage} failed ({failure_class.value}): {result.summary}. "
                f"Auditor's counsel: {findings or audit.summary}"
            )
            self._audited[task_id] = True
        else:
            self._artifacts["repair_hint"] = (
                f"{stage} failed ({failure_class.value}): {result.summary}"
            )

        if stage == "VERIFYING":
            return self._route_failure(task_id, failure_class, detail)
        # Funnel forward: only VERIFYING may choose the remedy.
        _next_stage = {
            "IMPLEMENTING": "REVIEWING",
            "REVIEWING": "TESTING",
            "TESTING": "VERIFYING",
        }
        self._machine.go(_next_stage[stage])
        return None

    def _route_failure(self, task_id: str, failure_class: FailureClass,
                       detail: str) -> Optional[Dict[str, Any]]:
        """VERIFYING's funnel: route a decided failure onward.

        From VERIFYING the machine may legally reach REPAIR, REPLAN, or
        HUMAN_DECISION. Repairs are bounded; an exhausted repair budget
        replans (once per task); a spent replan pauses for the human.
        """
        attempts = self._repair_attempts.get(task_id, 0)
        if attempts < MAX_REPAIR_ATTEMPTS:
            self._repair_attempts[task_id] = attempts + 1
            self._machine.go("REPAIR")
            return None
        return self._go_replan(
            f"task {task_id}: repair budget exhausted "
            f"({failure_class.value}): {detail[:300]}"
        )

    def _go_replan(self, reason: str) -> Optional[Dict[str, Any]]:
        """Enter REPLAN, or pause when the planner already revised once."""
        task_id = self._current_task.task_id if self._current_task else ""
        replans = self._replans.get(task_id, 0)
        if replans >= MAX_REPLANS_PER_TASK:
            return self._pause_for_human(
                f"task {task_id}: planner already revised the roadmap once "
                f"and it still fails — {reason[:300]}"
            )
        self._replans[task_id] = replans + 1
        self._machine.go("REPLAN")
        return None

    # -- pausing --------------------------------------------------------------------

    def _pause_for_human(self, reason: str) -> Dict[str, Any]:
        """Persist the question and pause with status='paused'."""
        try:
            self._machine.go("HUMAN_DECISION")
        except IllegalTransition:
            pass  # already there, or state store disagrees — pause anyway
        question_path = self._project_dir / ".mythis" / _AWAITING_HUMAN_FILENAME
        task_id = self._current_task.task_id if self._current_task else ""
        lines = [
            "# The Forge awaits a human decision",
            "",
            f"Written: {utc_now_iso()}",
            f"State: {self._machine.current()}",
            f"Task: {task_id or '(none)'}",
            f"Goal: {self._machine.state.goal}",
            "",
            "## Question",
            "",
            reason,
            "",
            "## Recent failures",
            "",
        ]
        for record in self._failures[-5:]:
            lines.append(
                f"- [{record.task_id}] {record.failure_class.value}: "
                f"{record.detail[:200]}"
            )
        if not self._failures:
            lines.append("- (none recorded)")
        lines += [
            "",
            "## How to resume",
            "",
            "Answer above (or edit the roadmap / code directly), delete this",
            "file, and run the forge again.",
            "",
        ]
        try:
            question_path.parent.mkdir(parents=True, exist_ok=True)
            question_path.write_text("\n".join(lines), encoding="utf-8")
        except OSError as exc:
            log.warning("could not write %s: %s", question_path, exc)
        self._emit(EventType.HUMAN_DECISION_REQUESTED, "orchestrator",
                   {"reason": reason[:500], "task_id": task_id})
        log.warning("paused for human decision: %s", reason)
        return self._result("paused")

    # -- task bookkeeping --------------------------------------------------

    def _load_tasks(self, planner_result: RoleResult) -> None:
        """Take tasks from the planner's artifacts, else roadmap.json."""
        raw: Any = None
        if isinstance(planner_result.artifacts, dict):
            raw = planner_result.artifacts.get("tasks")
        if isinstance(raw, list) and raw:
            tasks: List[ForgeTask] = []
            for entry in raw:
                try:
                    if isinstance(entry, ForgeTask):
                        tasks.append(entry)
                    elif isinstance(entry, dict):
                        tasks.append(ForgeTask.from_dict(entry))
                except (ValueError, TypeError) as exc:
                    log.warning("dropping malformed task: %s", exc)
            if tasks:
                self._tasks = tasks
                return
        # Fall back to the roadmap file the planner role writes.
        roadmap_path = self._project_dir / ".mythis" / "roadmap.json"
        try:
            data = roadmap_path.read_text(encoding="utf-8")
        except OSError:
            return
        try:
            import json
            entries = json.loads(data)
        except ValueError:
            log.warning("roadmap.json is not valid JSON")
            return
        if isinstance(entries, dict):
            entries = entries.get("tasks", [])
        if isinstance(entries, list):
            tasks = []
            for entry in entries:
                try:
                    tasks.append(ForgeTask.from_dict(entry))
                except (ValueError, TypeError, AttributeError) as exc:
                    log.warning("dropping malformed roadmap task: %s", exc)
            if tasks:
                self._tasks = tasks

    def _persist_roadmap(self) -> None:
        """Write the current task list back to .mythis/roadmap.json."""
        try:
            import json
            path = self._project_dir / ".mythis" / "roadmap.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps([t.to_dict() for t in self._tasks], indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            log.warning("could not persist roadmap: %s", exc)

    def _next_ready_task(self) -> Optional[ForgeTask]:
        """First task whose dependencies are all done and is not done."""
        done = {t.task_id for t in self._tasks if t.status == "done"}
        for task in self._tasks:
            if task.status == "done":
                continue
            if all(dep in done for dep in task.depends_on):
                return task
        return None
