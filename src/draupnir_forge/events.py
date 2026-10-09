"""events.py — the Forge's memory of its own deeds (slice 5).

Spec §29: every important action emits a structured event so the system
stays observable — for analytics, debugging, replay, multi-agent
coordination, and failure-pattern discovery.

The EventLog is an append-only JSONL ledger living at
<project>/.mythis/events.jsonl. Each line is one ForgeEvent. Sequence
numbers are monotonic across process restarts: the log is scanned on
open and the next sequence number continues from the highest one found
(Huginn remembers every thread, even the ones cut short).

Crash safety: every append is flushed and fsync'ed before emit()
returns, so a crash can lose at most the in-flight write. Corrupt
lines are never fatal — they are skipped and counted.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

__all__ = [
    "EventType",
    "ForgeEvent",
    "EventLog",
    "utc_now_iso",
]


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string (e.g. 2026-10-09T08:31:00+00:00)."""
    return datetime.now(timezone.utc).isoformat()


class EventType(str, Enum):
    """All event types from spec §29. The set is closed — exactly these."""

    PROJECT_CREATED = "PROJECT_CREATED"
    VISION_UPDATED = "VISION_UPDATED"
    DOMAIN_DISCOVERED = "DOMAIN_DISCOVERED"
    ARCHITECTURE_UPDATED = "ARCHITECTURE_UPDATED"
    ROADMAP_REVISED = "ROADMAP_REVISED"
    TASK_STARTED = "TASK_STARTED"
    TASK_FAILED = "TASK_FAILED"
    TASK_REPAIRED = "TASK_REPAIRED"
    TEST_FAILED = "TEST_FAILED"
    TEST_PASSED = "TEST_PASSED"
    INVARIANT_VIOLATED = "INVARIANT_VIOLATED"
    HUMAN_DECISION_REQUESTED = "HUMAN_DECISION_REQUESTED"
    HUMAN_DECISION_RECEIVED = "HUMAN_DECISION_RECEIVED"
    MODEL_SWITCHED = "MODEL_SWITCHED"
    CHECKPOINT_CREATED = "CHECKPOINT_CREATED"
    TASK_COMPLETED = "TASK_COMPLETED"
    PROJECT_REGROUNDED = "PROJECT_REGROUNDED"
    PROJECT_COMPLETED = "PROJECT_COMPLETED"


@dataclass
class ForgeEvent:
    """One immutable fact about what the Forge did.

    Attributes:
        seq: Monotonic sequence number within the project's event log.
        ts: UTC ISO-8601 timestamp of emission.
        type: What happened (one of EventType).
        actor_role: Which role emitted the event (e.g. "Auditor").
        project: Project name (derived from the project directory name).
        payload: Arbitrary JSON-serializable detail about the event.
        caused_by: seq of the event that caused this one, if any.
    """

    seq: int
    ts: str
    type: EventType
    actor_role: str
    project: str
    payload: Dict[str, Any]
    caused_by: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain JSON-compatible dict."""
        return {
            "seq": self.seq,
            "ts": self.ts,
            "type": self.type.value,
            "actor_role": self.actor_role,
            "project": self.project,
            "payload": self.payload,
            "caused_by": self.caused_by,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ForgeEvent":
        """Rebuild a ForgeEvent from a dict produced by to_dict().

        Raises:
            KeyError: If a required field is missing.
            ValueError: If a field (e.g. the event type) is invalid.
            TypeError: If a field has the wrong type.
        """
        if not isinstance(data, dict):
            raise TypeError(
                f"ForgeEvent.from_dict() needs a dict, got {type(data).__name__}"
            )
        seq = data["seq"]
        ts = data["ts"]
        event_type = EventType(data["type"])  # ValueError on unknown type
        actor_role = data["actor_role"]
        project = data["project"]
        payload = data["payload"]
        caused_by = data.get("caused_by")
        # Muninn checks every feather: wrong shapes never become events.
        if not isinstance(seq, int) or isinstance(seq, bool):
            raise TypeError(f"ForgeEvent 'seq' must be an int, got {seq!r}")
        if not isinstance(ts, str):
            raise TypeError(f"ForgeEvent 'ts' must be a str, got {ts!r}")
        if not isinstance(actor_role, str):
            raise TypeError(
                f"ForgeEvent 'actor_role' must be a str, got {actor_role!r}"
            )
        if not isinstance(project, str):
            raise TypeError(f"ForgeEvent 'project' must be a str, got {project!r}")
        if not isinstance(payload, dict):
            raise TypeError(
                f"ForgeEvent 'payload' must be a dict, got {payload!r}"
            )
        if caused_by is not None and (
            not isinstance(caused_by, int) or isinstance(caused_by, bool)
        ):
            raise TypeError(
                f"ForgeEvent 'caused_by' must be an int or None, got {caused_by!r}"
            )
        return cls(
            seq=seq,
            ts=ts,
            type=event_type,
            actor_role=actor_role,
            project=project,
            payload=payload,
            caused_by=caused_by,
        )


class EventLog:
    """Append-only JSONL event log for one project.

    The ledger lives at <project>/.mythis/events.jsonl. Parent
    directories are created on open. On open the file is scanned once
    to find the highest existing sequence number, so numbering stays
    monotonic across restarts. Corrupt lines are skipped (never raise)
    and counted in :attr:`skipped_corrupt`.
    """

    _FILENAME = "events.jsonl"

    def __init__(self, project_dir: Union[str, os.PathLike]) -> None:
        # No absolute paths are stored: everything hangs off project_dir.
        self._project_dir = Path(project_dir)
        self._mythis_dir = self._project_dir / ".mythis"
        self._mythis_dir.mkdir(parents=True, exist_ok=True)
        self._path = self._mythis_dir / self._FILENAME
        # Project name from the directory's own name; fall back to the
        # current working directory's name for bare "." inputs.
        self._project = self._project_dir.name or Path.cwd().name
        self._lock = threading.Lock()
        self.skipped_corrupt = 0
        self._next_seq = self._scan()

    @property
    def project(self) -> str:
        """Project name recorded on every emitted event."""
        return self._project

    @property
    def path(self) -> Path:
        """Filesystem path of the JSONL ledger."""
        return self._path

    def _scan(self) -> int:
        """Return max(existing seq) + 1 (1 when the log is empty/missing).

        Corrupt lines are skipped and counted; they never raise.
        """
        highest = 0
        if not self._path.exists():
            return 1
        with open(self._path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = ForgeEvent.from_dict(json.loads(line))
                except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                    self.skipped_corrupt += 1
                    continue
                if event.seq > highest:
                    highest = event.seq
        return highest + 1

    @staticmethod
    def _check_payload_serializable(payload: Dict[str, Any]) -> None:
        """Raise TypeError early if the payload cannot become JSON."""
        if not isinstance(payload, dict):
            raise TypeError(
                "Event payload must be a dict, got "
                f"{type(payload).__name__}"
            )
        try:
            json.dumps(payload)
        except (TypeError, ValueError) as exc:
            raise TypeError(
                "Event payload must be JSON-serializable; "
                f"json.dumps() failed: {exc}"
            ) from exc

    def emit(
        self,
        type: Union[EventType, str],
        actor_role: str,
        payload: Dict[str, Any],
        caused_by: Optional[int] = None,
    ) -> ForgeEvent:
        """Append one event to the ledger and return it.

        The payload is validated *before* anything is written, so a bad
        payload raises TypeError without touching the log or the
        sequence counter.

        Args:
            type: EventType (or its exact string value).
            actor_role: Non-empty role name, e.g. "Auditor".
            payload: JSON-serializable dict of event details.
            caused_by: seq of the causal parent event, if any.

        Returns:
            The emitted ForgeEvent with its assigned seq and timestamp.
        """
        event_type = type if isinstance(type, EventType) else EventType(type)
        if not isinstance(actor_role, str) or not actor_role.strip():
            raise ValueError("actor_role must be a non-empty string")
        if caused_by is not None and (
            not isinstance(caused_by, int) or isinstance(caused_by, bool)
        ):
            raise TypeError(
                f"caused_by must be an int or None, got {caused_by!r}"
            )
        # Validate first: a poisoned payload must not claim a seq number.
        self._check_payload_serializable(payload)

        with self._lock:
            event = ForgeEvent(
                seq=self._next_seq,
                ts=utc_now_iso(),
                type=event_type,
                actor_role=actor_role,
                project=self._project,
                payload=payload,
                caused_by=caused_by,
            )
            line = json.dumps(event.to_dict(), ensure_ascii=False) + "\n"
            # Crash-safe append: the bytes reach durable storage
            # before emit() reports success.
            with open(self._path, "a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
            self._next_seq += 1
        return event

    def query(
        self,
        type: Optional[Union[EventType, str]] = None,
        since_seq: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[ForgeEvent]:
        """Read events back in chronological order (newest last).

        Args:
            type: Keep only events of this type (EventType or its value).
            since_seq: Keep only events with seq greater than this.
            limit: Keep at most this many of the most recent matches.

        Corrupt lines are skipped and counted in :attr:`skipped_corrupt`;
        they never raise.
        """
        wanted: Optional[EventType] = None
        if type is not None:
            wanted = type if isinstance(type, EventType) else EventType(type)
        if limit is not None and limit < 0:
            raise ValueError(f"limit must be >= 0, got {limit}")

        matches: List[ForgeEvent] = []
        if self._path.exists():
            with open(self._path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = ForgeEvent.from_dict(json.loads(line))
                    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                        self.skipped_corrupt += 1
                        continue
                    if wanted is not None and event.type is not wanted:
                        continue
                    if since_seq is not None and event.seq <= since_seq:
                        continue
                    matches.append(event)
        if limit is not None:
            matches = matches[-limit:] if limit else []
        return matches

    def __len__(self) -> int:
        """Number of valid (non-corrupt) events currently in the ledger."""
        return len(self.query())
