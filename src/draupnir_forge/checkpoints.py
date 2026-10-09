"""checkpoints.py — Slice 28: git checkpoints and the pause/resume rite.

:class:`Checkpointer` is the forge's way of remembering where it has
been. It commits the project's working tree with a ``Forge-Task:``
trailer (graceful when there is nothing to commit, or no repository at
all), can roll the tree back — only with an explicit, destructive
``confirm=True`` — and can pause the forge into ``.mythis/PAUSED``
with the reason, the hour, and a full memory snapshot, then wake it
again with :meth:`resume`.

Git runs through :meth:`_run`, which honors an optional
:class:`~draupnir_forge.tools.ToolExecutor` (slice 24) when one is
provided and falls back to :mod:`subprocess` otherwise — the
ToolExecutor seam is typed but optional, so this slice stands alone.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from draupnir_forge.events import utc_now_iso
from draupnir_forge.memory import ProjectMemory

if TYPE_CHECKING:  # slice 24 is not built yet; keep the seam type-only
    from draupnir_forge.tools import ToolExecutor

log = logging.getLogger("draupnir_forge.checkpoints")

PAUSED_FILE = "PAUSED"
_CHECKPOINT_TIMEOUT = 120


class _CmdResult:
    """The one shape git results wear, whatever ran the command."""

    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class Checkpointer:
    """Git checkpoints plus the pause/resume rite for a forge project.

    Args:
        project_dir: Root of the forge project (and git working tree).
        tools: Optional ToolExecutor (slice 24) to run git through;
            plain :mod:`subprocess` is used when it is ``None``.
    """

    def __init__(self, project_dir: Union[str, Path],
                 tools: Optional["ToolExecutor"] = None) -> None:
        self.project_dir = Path(project_dir)
        self._tools = tools
        self._memory = ProjectMemory(project_dir)

    # -- command running ----------------------------------------------------

    def _run(self, argv: List[str], check: bool = True) -> _CmdResult:
        """Run a command in the project dir; normalize the result shape."""
        if self._tools is not None:
            raw = self._tools.run(argv, cwd=str(self.project_dir),
                                  timeout=_CHECKPOINT_TIMEOUT)
            result = self._normalize(raw)
        else:
            completed = subprocess.run(
                argv, cwd=str(self.project_dir), capture_output=True,
                text=True, timeout=_CHECKPOINT_TIMEOUT)
            result = _CmdResult(completed.returncode,
                                completed.stdout or "",
                                completed.stderr or "")
        if check and result.returncode != 0:
            raise RuntimeError(
                f"Command {' '.join(argv)} failed "
                f"(exit {result.returncode}): {result.stderr.strip()}")
        return result

    @staticmethod
    def _normalize(raw: Any) -> _CmdResult:
        """Coerce a ToolExecutor result into :class:`_CmdResult`."""
        if isinstance(raw, _CmdResult):
            return raw
        if isinstance(raw, dict):
            code = raw.get("exit_code", raw.get("returncode", 0))
            return _CmdResult(int(code or 0),
                              str(raw.get("stdout", "") or ""),
                              str(raw.get("stderr", "") or ""))
        return _CmdResult(int(getattr(raw, "returncode", 0) or 0),
                          str(getattr(raw, "stdout", "") or ""),
                          str(getattr(raw, "stderr", "") or ""))

    def _git(self, *args: str, check: bool = True) -> _CmdResult:
        """Run ``git`` inside the project directory."""
        return self._run(["git", *args], check=check)

    def _is_git_repo(self) -> bool:
        """True when the project dir lives inside a git working tree."""
        try:
            result = self._git("rev-parse", "--is-inside-work-tree",
                               check=False)
        except (OSError, RuntimeError) as exc:
            log.warning("git rev-parse failed: %s", exc)
            return False
        return (result.returncode == 0
                and result.stdout.strip() == "true")

    # -- checkpoints ----------------------------------------------------------

    def checkpoint(self, task_id: str, message: str) -> Optional[str]:
        """Commit the working tree with a ``Forge-Task:`` trailer.

        Stages everything (``git add -A``), then commits
        ``"<message>\\n\\nForge-Task: <task_id>"``.

        Returns:
            The new commit hash, or ``None`` when there is nothing to
            commit — or when the project is not a git repository at all
            (both are quiet, honest answers, not errors).
        """
        if not self._is_git_repo():
            log.info("No git repository at %s; checkpoint skipped",
                     self.project_dir)
            return None
        self._git("add", "-A")
        status = self._git("status", "--porcelain", check=False)
        if not status.stdout.strip():
            return None  # a clean tree needs no new stone
        full_message = f"{message}\n\nForge-Task: {task_id}"
        self._git("commit", "-m", full_message)
        head = self._git("rev-parse", "HEAD", check=False)
        return head.stdout.strip() or None

    def rollback(self, n: int = 1, confirm: bool = False) -> str:
        """Roll the tree back ``n`` commits (``git reset --hard HEAD~n``).

        This is destructive — uncommitted work is lost — so it refuses
        to act without the explicit ``confirm=True`` oath.

        Returns:
            The new HEAD commit hash.

        Raises:
            PermissionError: If ``confirm`` is not ``True``.
            ValueError: If ``n`` is less than 1.
            RuntimeError: If the project is not a git repository.
        """
        if not confirm:
            raise PermissionError(
                "rollback() is destructive: pass confirm=True to proceed")
        if n < 1:
            raise ValueError(f"rollback n must be >= 1, got {n}")
        if not self._is_git_repo():
            raise RuntimeError(
                f"Not a git repository: {self.project_dir}")
        self._git("reset", "--hard", f"HEAD~{n}")
        head = self._git("rev-parse", "HEAD", check=False)
        return head.stdout.strip()

    # -- pause / resume ---------------------------------------------------------

    def _paused_path(self) -> Path:
        return self.project_dir / ".mythis" / PAUSED_FILE

    def is_paused(self) -> bool:
        """True while a ``.mythis/PAUSED`` marker exists."""
        return self._paused_path().exists()

    def pause(self, reason: str) -> Path:
        """Lay the forge down: write ``.mythis/PAUSED``.

        The marker holds the reason, the hour, and a full memory
        snapshot so :meth:`resume` can wake the forge with context.
        Writes atomically.
        """
        payload: Dict[str, Any] = {
            "reason": reason,
            "paused_at": utc_now_iso(),
            "snapshot": self._memory.snapshot(),
        }
        path = self._paused_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        log.info("Forge paused: %s", reason)
        return path

    def resume(self) -> Dict[str, Any]:
        """Wake the forge: remove ``.mythis/PAUSED`` and return its record.

        Returns:
            The pause record (reason, paused_at, snapshot).

        Raises:
            RuntimeError: If the forge is not paused.
        """
        path = self._paused_path()
        if not path.exists():
            raise RuntimeError(
                "The forge is not paused: no .mythis/PAUSED marker found")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Pause marker unreadable: {exc}") from exc
        path.unlink()
        log.info("Forge resumed (was paused: %s)",
                 payload.get("reason", "?"))
        return payload
