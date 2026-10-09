"""machine.py — the Forge's governing state machine (slice 19).

The Orchestrator (slice 20) may *recommend* where to go next, but this
module *governs* which moves are legal. The transition law is DATA, not
branching logic: ``TRANSITIONS`` is built directly from slice 6's
``ALLOWED_TRANSITIONS`` (imported, never duplicated) so the Norns' thread
map has exactly one canonical home.

A ``ForgeMachine`` wraps a :class:`~draupnir_forge.state.ProjectState`
(which persists the current phase under ``.mythis/``) and guards every
move with the transition table. Each successful move is recorded to the
:class:`~draupnir_forge.events.EventLog` when one is available, using the
state's natural event type (some quieter states emit none — a silent
footstep still moves the walker forward).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .events import EventLog, EventType
from .state import ALLOWED_TRANSITIONS, IllegalTransition, ProjectState

__all__ = [
    "IllegalTransition",
    "TRANSITIONS",
    "STATES",
    "STATE_ROLE_MAP",
    "STATE_EVENT_MAP",
    "TERMINAL_STATES",
    "INITIAL_STATE",
    "ForgeMachine",
]

log = logging.getLogger("draupnir_forge.machine")

# The transition law as DATA. Converted to sorted lists for readable
# introspection; the canonical source is state.ALLOWED_TRANSITIONS.
TRANSITIONS: Dict[str, List[str]] = {
    state: sorted(targets) for state, targets in ALLOWED_TRANSITIONS.items()
}

# All 17 states of the Forge, in lifecycle order.
STATES: List[str] = [
    "INTAKE",
    "DISCOVERY",
    "DEFINITION",
    "ARCHITECTURE",
    "ROADMAP",
    "TASK_READY",
    "IMPLEMENTING",
    "REVIEWING",
    "TESTING",
    "VERIFYING",
    "COMPLETE_TASK",
    "REPAIR",
    "REPLAN",
    "HUMAN_DECISION",
    "DOCUMENTING",
    "REGROUNDING",
    "PROJECT_COMPLETE",
]

INITIAL_STATE = "INTAKE"

# States the machine cannot leave by any transition — the end of the road.
TERMINAL_STATES = frozenset({"PROJECT_COMPLETE"})

# Which role owns each phase of the forge loop. States handled by
# orchestrator logic itself (INTAKE, TASK_READY, COMPLETE_TASK, REPAIR,
# REPLAN, HUMAN_DECISION, REGROUNDING, PROJECT_COMPLETE) map to None.
STATE_ROLE_MAP: Dict[str, Optional[str]] = {
    "INTAKE": None,
    "DISCOVERY": "cartographer",
    "DEFINITION": "skald",
    "ARCHITECTURE": "architect",
    "ROADMAP": "planner",
    "TASK_READY": None,
    "IMPLEMENTING": "forge_worker",
    "REVIEWING": "auditor",
    "TESTING": "tester",
    "VERIFYING": "verifier",
    "COMPLETE_TASK": None,
    "REPAIR": None,
    "REPLAN": None,
    "HUMAN_DECISION": None,
    "DOCUMENTING": "scribe",
    "REGROUNDING": None,
    "PROJECT_COMPLETE": None,
}

# The natural event for entering each state, where one exists. Quiet
# states (TASK_READY, REVIEWING, TESTING, VERIFYING, DOCUMENTING) record
# their movement through the state store alone; their outcomes are
# announced by the roles that work in them.
STATE_EVENT_MAP: Dict[str, EventType] = {
    "DISCOVERY": EventType.DOMAIN_DISCOVERED,
    "DEFINITION": EventType.VISION_UPDATED,
    "ARCHITECTURE": EventType.ARCHITECTURE_UPDATED,
    "ROADMAP": EventType.ROADMAP_REVISED,
    "IMPLEMENTING": EventType.TASK_STARTED,
    "COMPLETE_TASK": EventType.TASK_COMPLETED,
    "REPAIR": EventType.TASK_FAILED,
    "REPLAN": EventType.ROADMAP_REVISED,
    "HUMAN_DECISION": EventType.HUMAN_DECISION_REQUESTED,
    "REGROUNDING": EventType.PROJECT_REGROUNDED,
    "PROJECT_COMPLETE": EventType.PROJECT_COMPLETED,
}

# Actor name stamped on state-change events.
_MACHINE_ACTOR = "orchestrator"


class ForgeMachine:
    """Governs legal movement through the Forge's 17 states.

    Wraps :class:`~draupnir_forge.state.ProjectState` (the persisted
    phase) and validates every move against ``TRANSITIONS`` before it
    is made. ``go()`` raises :class:`IllegalTransition` for unknown
    states or illegal moves and never mutates state on failure.

    Args:
        project_dir: The project root (``.mythis/`` lives beneath it).
        event_log: Optional :class:`~draupnir_forge.events.EventLog`
            to announce state changes through.
    """

    def __init__(
        self,
        project_dir: Union[str, Path],
        event_log: Optional[EventLog] = None,
    ) -> None:
        self._project_dir = Path(project_dir)
        self._state = ProjectState(self._project_dir)
        self._event_log = event_log

    # -- read access ----------------------------------------------------

    def current(self) -> str:
        """The phase the Forge currently stands in."""
        return self._state.phase

    @property
    def state(self) -> ProjectState:
        """The wrapped ProjectState (goal, task_id, counters, ...)."""
        return self._state

    def allowed_from(self, state: str) -> List[str]:
        """Legal destinations from ``state`` (empty for unknown states)."""
        return list(TRANSITIONS.get(state, []))

    def is_terminal(self) -> bool:
        """True only when the project has reached PROJECT_COMPLETE."""
        return self._state.phase in TERMINAL_STATES

    # -- movement ---------------------------------------------------------

    def go(self, target: str) -> None:
        """Move to ``target`` if the transition table allows it.

        Moving to PROJECT_COMPLETE or HUMAN_DECISION is always legal:
        the former is the explicit completion (per slice 6), the latter
        an out-of-band human-escalation interrupt. All other moves are
        validated against ``TRANSITIONS``. On success the new phase is
        persisted and a state-change event is emitted when one exists
        for the target.

        Args:
            target: The state to move to.

        Raises:
            IllegalTransition: If ``target`` is unknown or the move
                from the current state is not in the transition table.
        """
        if target not in STATES:
            raise IllegalTransition(
                f"Unknown state {target!r}. Known states: "
                f"{', '.join(STATES)}"
            )
        previous = self._state.phase
        if previous == target:
            # A move onto one's own stone is a no-op, not an error —
            # the walker simply stands still.
            return
        if target == "PROJECT_COMPLETE":
            # The explicit completion bypasses the map, per slice 6.
            self._state.complete()
        elif target == "HUMAN_DECISION":
            # Human escalation is an out-of-band interrupt, not forward
            # progress: like complete(), it bypasses the map so the
            # Orchestrator may pause from any phase.
            self._state.interrupt(target)
        else:
            self._state.transition(target)
        self._announce(previous, target)

    # -- internal --------------------------------------------------------

    def _announce(self, previous: str, target: str) -> None:
        """Best-effort event emission for a successful transition."""
        if self._event_log is None:
            return
        event_type = STATE_EVENT_MAP.get(target)
        if event_type is None:
            return
        try:
            payload: Dict[str, Any] = {
                "from": previous,
                "to": target,
                "goal": self._state.goal,
            }
            if self._state.task_id:
                payload["task_id"] = self._state.task_id
            self._event_log.emit(event_type, _MACHINE_ACTOR, payload)
        except Exception as exc:  # Never let the ledger break the walk.
            log.warning("state-change event failed to emit: %s", exc)
