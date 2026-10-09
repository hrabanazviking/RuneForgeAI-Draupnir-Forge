"""state.py — the Forge's phase state machine (slice 6).

Spec §8: the Orchestrator walks a project through phases
(INTAKE → DISCOVERY → … → PROJECT_COMPLETE). LLMs may *recommend*
transitions, but this module *governs* which transitions are legal.

The law is data, not branching logic: ALLOWED_TRANSITIONS is a
module-level dict mapping each phase to the set of phases it may
move to. The map can be read, audited, and extended without touching
any code path — the Norns' threads, listed plainly for all to see.

ProjectState persists to <project>/.mythis/PROJECT_STATE.json with
atomic writes (temp file + os.replace), so a crash can never leave a
half-written state file. A corrupt state file is renamed aside as
PROJECT_STATE.json.corrupt.<ts> and replaced with a fresh default —
the Forge never crashes on its own memory.
"""

from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional, Union

from .events import utc_now_iso

__all__ = [
    "IllegalTransition",
    "ALLOWED_TRANSITIONS",
    "PHASES",
    "TERMINAL_PHASE",
    "INITIAL_PHASE",
    "ProjectState",
]


class IllegalTransition(Exception):
    """Raised when a phase transition violates the allowed-transition map."""


# The state machine as DATA (spec §8). Key: current phase.
# Value: phases it may legally move to via transition().
# PROJECT_COMPLETE is terminal in the map; complete() bypasses the map
# so any phase may end the project explicitly.
ALLOWED_TRANSITIONS: Dict[str, FrozenSet[str]] = {
    "INTAKE": frozenset({"DISCOVERY"}),
    "DISCOVERY": frozenset({"DEFINITION"}),
    "DEFINITION": frozenset({"ARCHITECTURE"}),
    "ARCHITECTURE": frozenset({"ROADMAP"}),
    "ROADMAP": frozenset({"TASK_READY"}),
    "TASK_READY": frozenset({"IMPLEMENTING", "PROJECT_COMPLETE"}),
    "IMPLEMENTING": frozenset({"REVIEWING"}),
    "REVIEWING": frozenset({"TESTING"}),
    "TESTING": frozenset({"VERIFYING"}),
    "VERIFYING": frozenset(
        {"COMPLETE_TASK", "REPAIR", "REPLAN", "HUMAN_DECISION"}
    ),
    "COMPLETE_TASK": frozenset({"DOCUMENTING", "TASK_READY"}),
    "REPAIR": frozenset({"IMPLEMENTING"}),
    "REPLAN": frozenset({"ROADMAP", "ARCHITECTURE"}),
    "HUMAN_DECISION": frozenset({"TASK_READY", "ROADMAP", "DEFINITION"}),
    "DOCUMENTING": frozenset({"REGROUNDING"}),
    "REGROUNDING": frozenset(
        {"TASK_READY", "ROADMAP", "ARCHITECTURE", "DEFINITION"}
    ),
    "PROJECT_COMPLETE": frozenset(),
}

PHASES: FrozenSet[str] = frozenset(ALLOWED_TRANSITIONS)
INITIAL_PHASE = "INTAKE"
TERMINAL_PHASE = "PROJECT_COMPLETE"

_STATE_FILENAME = "PROJECT_STATE.json"


class ProjectState:
    """Owns <project>/.mythis/PROJECT_STATE.json for one project.

    Fields: phase, goal, task_id, updated_ts (UTC ISO-8601), counters
    (a dict of named integer tallies, e.g. tasks completed).

    Loading never crashes: a missing file yields the default state
    (phase INTAKE); a corrupt file is renamed aside with a timestamp
    and replaced with a fresh default. All writes are atomic.
    """

    def __init__(self, project_dir: Union[str, os.PathLike]) -> None:
        self._project_dir = Path(project_dir)
        self._mythis_dir = self._project_dir / ".mythis"
        self._mythis_dir.mkdir(parents=True, exist_ok=True)
        self._path = self._mythis_dir / _STATE_FILENAME
        self._data = self._load()

    # -- lifecycle ----------------------------------------------------

    def _default_state(self) -> Dict[str, Any]:
        """A newborn project's state: at the well's edge, in INTAKE."""
        return {
            "phase": INITIAL_PHASE,
            "goal": "",
            "task_id": None,
            "updated_ts": utc_now_iso(),
            "counters": {},
        }

    def _load(self) -> Dict[str, Any]:
        """Load state from disk; heal anything broken along the way."""
        try:
            with open(self._path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except FileNotFoundError:
            state = self._default_state()
            self._save(state)
            return state
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return self._recover_corrupt()

        if not isinstance(raw, dict):
            return self._recover_corrupt()
        phase = raw.get("phase")
        if phase not in PHASES:
            return self._recover_corrupt()

        # Self-healing: absent or misshapen fields fall back to defaults
        # rather than crashing the reader.
        state = self._default_state()
        state["phase"] = phase
        goal = raw.get("goal", "")
        state["goal"] = goal if isinstance(goal, str) else ""
        task_id = raw.get("task_id")
        state["task_id"] = (
            task_id if task_id is None or isinstance(task_id, str) else None
        )
        # Read-compat: slice 4's CLI writes "updated"; this module's
        # canonical key is "updated_ts". Either is accepted on load.
        updated_ts = raw.get("updated_ts", raw.get("updated", ""))
        state["updated_ts"] = (
            updated_ts if isinstance(updated_ts, str) else state["updated_ts"]
        )
        counters = raw.get("counters", {})
        if isinstance(counters, dict):
            state["counters"] = {
                str(k): v for k, v in counters.items()
                if isinstance(v, int) and not isinstance(v, bool)
            }
        return state

    def _recover_corrupt(self) -> Dict[str, Any]:
        """Rename the corrupt file aside and start from a fresh default."""
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = self._path.with_name(f"{_STATE_FILENAME}.corrupt.{stamp}")
        counter = 1
        while backup.exists():
            counter += 1
            backup = self._path.with_name(
                f"{_STATE_FILENAME}.corrupt.{stamp}-{counter}"
            )
        try:
            os.replace(self._path, backup)
        except OSError:
            # If even the rename fails, the fresh state still wins:
            # never crash on our own memory.
            pass
        state = self._default_state()
        self._save(state)
        return state

    def _save(self, state: Dict[str, Any]) -> None:
        """Atomic write: temp file, fsync, then os.replace over the real one.

        The on-disk file mirrors "updated_ts" as "updated" so slice 4's
        `draupnir status` (which reads "updated") keeps working; the
        in-memory state stays canonical.
        """
        on_disk = dict(state, updated=state["updated_ts"])
        tmp_path = self._path.with_name(f"{_STATE_FILENAME}.tmp")
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(on_disk, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, self._path)

    def _commit(self) -> None:
        """Refresh updated_ts and persist the current in-memory state."""
        self._data["updated_ts"] = utc_now_iso()
        self._save(self._data)

    # -- read access ---------------------------------------------------

    @property
    def phase(self) -> str:
        """Current phase of the state machine."""
        return self._data["phase"]

    @property
    def goal(self) -> str:
        """The project's declared goal."""
        return self._data["goal"]

    @property
    def task_id(self) -> Optional[str]:
        """ID of the task currently being worked, if any."""
        return self._data["task_id"]

    @property
    def updated_ts(self) -> str:
        """UTC ISO-8601 timestamp of the last mutation."""
        return self._data["updated_ts"]

    @property
    def counters(self) -> Dict[str, int]:
        """A copy of the named integer counters."""
        return dict(self._data["counters"])

    @property
    def path(self) -> Path:
        """Filesystem path of the state file."""
        return self._path

    def snapshot(self) -> Dict[str, Any]:
        """A deep copy of the full state dict (safe to hand to strangers)."""
        return copy.deepcopy(self._data)

    # -- mutations ------------------------------------------------------

    def transition(self, new_phase: str) -> str:
        """Move to a new phase if the transition map allows it.

        Args:
            new_phase: The phase to move to.

        Returns:
            The new phase.

        Raises:
            IllegalTransition: If new_phase is unknown or the move from
                the current phase is not in ALLOWED_TRANSITIONS.
        """
        if new_phase not in PHASES:
            raise IllegalTransition(
                f"Unknown phase {new_phase!r}. Known phases: "
                f"{', '.join(sorted(PHASES))}"
            )
        allowed = ALLOWED_TRANSITIONS[self._data["phase"]]
        if new_phase not in allowed:
            raise IllegalTransition(
                f"Illegal transition {self._data['phase']} -> {new_phase}. "
                f"Allowed from {self._data['phase']}: "
                f"{', '.join(sorted(allowed)) or '(none — terminal phase)'}"
            )
        self._data["phase"] = new_phase
        self._commit()
        return new_phase

    def complete(self) -> str:
        """End the project from ANY phase (the explicit any→PROJECT_COMPLETE).

        This is the only sanctioned way to reach PROJECT_COMPLETE from
        phases other than TASK_READY.
        """
        self._data["phase"] = TERMINAL_PHASE
        self._commit()
        return TERMINAL_PHASE

    def interrupt(self, new_phase: str) -> str:
        """Out-of-band phase change, bypassing the transition map.

        Reserved for interrupts that are not forward progress —
        currently only HUMAN_DECISION (the Orchestrator pausing for the
        human, which may strike from any phase). Mirrors complete():
        the map governs the walk, but an explicit interrupt may stop it
        from anywhere. Ordinary flow must still use transition().

        Raises:
            IllegalTransition: If new_phase is not a sanctioned
                interrupt target.
        """
        if new_phase != "HUMAN_DECISION":
            raise IllegalTransition(
                "interrupt() only supports HUMAN_DECISION, "
                f"got {new_phase!r}"
            )
        self._data["phase"] = new_phase
        self._commit()
        return new_phase

    def update(self, **fields: Any) -> Dict[str, Any]:
        """Update goal, task_id, and/or counters (not phase — use transition()).

        Raises:
            ValueError: If 'phase', 'updated_ts', or 'updated' is passed
                (all managed internally).
            KeyError: If an unknown field name is passed.
            TypeError: If a value has the wrong type.
        """
        if "phase" in fields:
            raise ValueError(
                "Cannot set 'phase' via update(); use transition() or complete()"
            )
        if "updated_ts" in fields or "updated" in fields:
            raise ValueError(
                "'updated_ts'/'updated' are managed by ProjectState itself"
            )
        allowed_fields = {"goal", "task_id", "counters"}
        for name, value in fields.items():
            if name not in allowed_fields:
                raise KeyError(
                    f"Unknown state field {name!r}. "
                    f"Known fields: {', '.join(sorted(allowed_fields))}"
                )
            if name == "goal":
                if not isinstance(value, str):
                    raise TypeError(
                        f"'goal' must be a str, got {type(value).__name__}"
                    )
            elif name == "task_id":
                if value is not None and not isinstance(value, str):
                    raise TypeError(
                        "'task_id' must be a str or None, "
                        f"got {type(value).__name__}"
                    )
            elif name == "counters":
                if not isinstance(value, dict):
                    raise TypeError(
                        f"'counters' must be a dict, got {type(value).__name__}"
                    )
                for key, val in value.items():
                    if not isinstance(val, int) or isinstance(val, bool):
                        raise TypeError(
                            f"Counter {key!r} must be an int, got {val!r}"
                        )
            self._data[name] = copy.deepcopy(value)
        self._commit()
        return self.snapshot()

    def increment(self, counter_name: str) -> int:
        """Bump a named counter by one (creating it at 1) and return it."""
        if not isinstance(counter_name, str) or not counter_name:
            raise ValueError("counter_name must be a non-empty string")
        counters = self._data["counters"]
        counters[counter_name] = counters.get(counter_name, 0) + 1
        self._commit()
        return counters[counter_name]
