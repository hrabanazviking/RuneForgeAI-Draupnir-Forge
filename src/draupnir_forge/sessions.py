"""sessions.py — the völva's session memory and replay (slice 42).

A forge run keeps its own diary. Each run gets a directory under
``<project>/.mythis/sessions/<session-id>/`` holding:

- ``goal.md`` — the goal the run set out to achieve (written by
  :meth:`Session.begin`);
- ``actions.jsonl`` — one JSON object per recorded action, in order;
- ``findings.md`` — appended human-readable findings;
- ``result.md`` — the outcome, written by :meth:`Session.finish`.

The session id is the UTC timestamp the run began, in a
filename-safe form like ``20261009T052200Z`` (two runs begun in the
same second get a numeric suffix, so ids stay unique).

:meth:`Session.replay` reads ``actions.jsonl`` and returns a numbered,
human-readable action sequence. The replay is labeled for **debugging
only** — it is never re-executed.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

__all__ = ["Session"]

_REPLAY_HEADER = (
    "SESSION REPLAY — DEBUGGING VIEW ONLY. This sequence is a record "
    "of what the Forge did; it is NOT re-executed by replay()."
)


def _session_id_now() -> str:
    """UTC-timestamp session id, safe for directory names."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _write_text_atomic(path: Path, text: str) -> None:
    """Write *text* to *path* atomically.

    The content goes to a temp file in the same directory (same
    filesystem, so the rename is atomic), is flushed and ``os.fsync``-ed,
    then swapped over the target with ``os.replace``. A crash mid-write
    can never leave a torn target behind, and no ``*.tmp`` files remain
    after a normal write.
    """
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _append_text_atomic(path: Path, text: str) -> None:
    """Append *text* to *path* atomically.

    Reads the current content and rewrites the whole file via
    :func:`_write_text_atomic`, so an interruption can never leave a
    torn line at the tail of the file.
    """
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    _write_text_atomic(path, existing + text)


def _render_action(index: int, action: Dict[str, Any]) -> str:
    """One numbered, human-readable line for a recorded action dict."""
    kind = str(action.get("action", action.get("type", "action")))
    detail = action.get("detail") or action.get("summary") or action.get("task")
    parts = [f"{index:>3}. {kind}"]
    if detail:
        parts.append(str(detail))
    ts = action.get("ts") or action.get("timestamp")
    if ts:
        parts.append(f"[{ts}]")
    extras = {
        key: value
        for key, value in action.items()
        if key not in {"action", "type", "detail", "summary", "task", "ts", "timestamp"}
    }
    for key, value in extras.items():
        parts.append(f"{key}={value}")
    return " — ".join(parts)


class Session:
    """Per-run session memory under ``.mythis/sessions/``.

    Args:
        project_dir: Project root; sessions live beneath its
            ``.mythis/sessions/`` directory (created on demand).
    """

    def __init__(self, project_dir: Union[str, Path]) -> None:
        self._project_dir = Path(project_dir)
        self._sessions_dir = self._project_dir / ".mythis" / "sessions"
        self._current_id: Optional[str] = None

    # -- lifecycle ----------------------------------------------------------
    def _unique_id(self, base: str) -> str:
        """Ensure the session id is unique inside the sessions directory."""
        candidate = base
        suffix = 1
        while (self._sessions_dir / candidate).exists():
            suffix += 1
            candidate = f"{base}-{suffix}"
        return candidate

    def _dir(self, session_id: str) -> Path:
        """Directory for one session (relative to the project only)."""
        return self._sessions_dir / session_id

    def begin(self, goal: str) -> str:
        """Open a new session: write ``goal.md`` and return its id.

        The id is the UTC timestamp the session began (filename-safe).
        Two begins in the same second still get distinct ids.
        """
        session_id = self._unique_id(_session_id_now())
        session_dir = self._dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        _write_text_atomic(
            session_dir / "goal.md",
            f"# Session goal\n\n{goal}\n\n_started: {_utc_now_iso()}_\n",
        )
        self._current_id = session_id
        return session_id

    @property
    def current_id(self) -> Optional[str]:
        """Id of the most recently begun session (None before begin())."""
        return self._current_id

    # -- recording ------------------------------------------------------------
    def record_action(self, action: Dict[str, Any]) -> None:
        """Append one action dict to the session's ``actions.jsonl``.

        The record gains a ``ts`` field when it has none. Requires a begun
        session (raises RuntimeError otherwise).
        """
        session_dir = self._require_current()
        if not isinstance(action, dict):
            raise TypeError(
                f"action must be a dict, got {type(action).__name__}"
            )
        record = dict(action)
        record.setdefault("ts", _utc_now_iso())
        _append_text_atomic(
            session_dir / "actions.jsonl",
            json.dumps(record, ensure_ascii=False) + "\n",
        )

    def record_finding(self, text: str) -> None:
        """Append a timestamped finding line to ``findings.md``."""
        session_dir = self._require_current()
        if not isinstance(text, str):
            raise TypeError(
                f"finding must be a str, got {type(text).__name__}"
            )
        _append_text_atomic(
            session_dir / "findings.md", f"- [{_utc_now_iso()}] {text}\n"
        )

    def finish(self, result: Dict[str, Any]) -> None:
        """Write ``result.md`` with the session's outcome; closes the run."""
        session_dir = self._require_current()
        if not isinstance(result, dict):
            raise TypeError(
                f"result must be a dict, got {type(result).__name__}"
            )
        lines = ["# Session result", ""]
        for key, value in result.items():
            lines.append(f"## {key}")
            lines.append("")
            lines.append(str(value))
            lines.append("")
        lines.append(f"_finished: {_utc_now_iso()}_")
        _write_text_atomic(session_dir / "result.md", "\n".join(lines) + "\n")
        self._current_id = None

    def _require_current(self) -> Path:
        """Session directory of the open run; raises when none is open."""
        if self._current_id is None:
            raise RuntimeError(
                "no session is open: call begin(goal) before recording"
            )
        return self._dir(self._current_id)

    # -- inspection ------------------------------------------------------------
    def list_sessions(self) -> List[str]:
        """Session ids present on disk, oldest first."""
        if not self._sessions_dir.is_dir():
            return []
        return sorted(
            entry.name
            for entry in self._sessions_dir.iterdir()
            if entry.is_dir()
        )

    def _actions(self, session_id: str) -> List[Dict[str, Any]]:
        """Raw action dicts for a session (skips corrupt lines)."""
        path = self._dir(session_id) / "actions.jsonl"
        actions: List[Dict[str, Any]] = []
        if not path.is_file():
            return actions
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue  # self-healing: a torn line never kills replay
                if isinstance(data, dict):
                    actions.append(data)
        return actions

    def replay(self, session_id: str) -> str:
        """Return the session's action sequence as a numbered listing.

        This is for **debugging only** — replaying is reading, never
        re-executing. The returned text carries that warning at the top.
        Raises FileNotFoundError when the session does not exist.
        """
        if not self._dir(session_id).is_dir():
            raise FileNotFoundError(f"no such session: {session_id!r}")
        actions = self._actions(session_id)
        lines = [_REPLAY_HEADER, ""]
        if not actions:
            lines.append("(no actions were recorded in this session)")
        else:
            for index, action in enumerate(actions, start=1):
                lines.append(_render_action(index, action))
        return "\n".join(lines) + "\n"
