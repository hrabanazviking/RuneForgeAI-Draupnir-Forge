"""tools.py — Tool executor (slice 24).

The Forge's hands on the world: shell commands run under an authority
model, and unified-diff patches land on the project tree. Every command
runs with its working directory pinned to the project root — it never
inherits the caller's cwd. Commands are always passed as an argument
list; ``shell=True`` is never used, anywhere.

Authority kinds (spec §31): read | write | exec | install | network |
destructive. The ``tools.allow_*`` config flags gate each kind;
destructive and network are denied by default, and a denied action
raises :class:`AuthorityDenied` before anything spawns.

The :func:`apply_patch` unified-diff applier (new files, deletions,
multi-hunk edits) delegates to the canonical implementation that the
Forge Worker role carries; it is exposed here so every role and engine
shares the one patch engine.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

log = logging.getLogger("draupnir_forge.tools")

# Cap on captured stdout/stderr per command (100 KiB).
_OUTPUT_CAP = 100 * 1024

# Tool kind -> config flag that authorizes it (None = always allowed).
_KIND_TO_FLAG = {
    "read": None,
    "write": None,
    "exec": "tools.allow_exec",
    "install": "tools.allow_install",
    "network": "tools.allow_network",
    "destructive": "tools.allow_destructive",
}


class AuthorityDenied(Exception):
    """A tool action was refused: the config does not authorize it."""


@dataclass
class ToolResult:
    """The outcome of one executed command.

    Attributes:
        exit_code: Process exit status (127 when the binary was missing).
        stdout: Captured standard output, capped at 100 KiB.
        stderr: Captured standard error, capped at 100 KiB.
        duration_s: Wall-clock seconds the command ran.
        timed_out: True when the timeout killed the process tree.
    """

    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool


class ToolExecutor:
    """Runs commands under the Forge's authority model.

    Args:
        project_dir: Project root; every command's cwd is pinned here.
        config: A :class:`ForgeConfig` carrying the ``tools.allow_*``
            flags and ``tools.default_timeout_s``.
    """

    def __init__(self, project_dir: str | Path, config: object) -> None:
        self.project_dir = Path(project_dir).resolve()
        self.config = config

    # -- authority -------------------------------------------------------
    def _check_authority(self, kind: str) -> None:
        """Raise :class:`AuthorityDenied` unless *kind* is authorized.

        Unknown kinds raise ``ValueError``; the authority map is closed.
        """
        if kind not in _KIND_TO_FLAG:
            raise ValueError(
                f"Unknown tool kind {kind!r}; expected one of "
                f"{sorted(_KIND_TO_FLAG)}."
            )
        flag = _KIND_TO_FLAG[kind]
        if flag is None:
            return  # read/write are the forge's bread and water
        allowed = False
        try:
            allowed = bool(self.config.get(flag, False))
        except Exception as exc:  # unreadable config degrades to deny.
            log.warning(
                "Could not read config %r; denying %s: %s", flag, kind, exc
            )
        if not allowed:
            raise AuthorityDenied(
                f"Tool kind {kind!r} is not authorized: config {flag!r} "
                "is false (destructive and network actions are denied "
                "by default)."
            )

    # -- execution -------------------------------------------------------
    def run(
        self,
        cmd: List[str],
        timeout: Optional[float] = None,
        kind: str = "exec",
    ) -> ToolResult:
        """Run *cmd* pinned to the project dir and capture the outcome.

        Args:
            cmd: Argument list (never a shell string; ``shell=True`` is
                never used). Must be a non-empty list of strings.
            timeout: Seconds before the process tree is killed; ``None``
                uses ``tools.default_timeout_s`` from the config.
            kind: Authority kind (read|write|exec|install|network|
                destructive); denied kinds raise :class:`AuthorityDenied`
                before anything spawns.

        Returns:
            A :class:`ToolResult` with exit code, capped output,
            duration, and the timeout flag.

        Raises:
            ValueError: On an unknown *kind* or a malformed *cmd*.
            AuthorityDenied: When the config forbids *kind*.
        """
        if not isinstance(cmd, list):
            raise ValueError(
                "cmd must be a list of strings (shell strings are forbidden; "
                "shell=True is never used)."
            )
        if not cmd:
            raise ValueError("cmd must not be empty.")
        if not all(isinstance(part, str) for part in cmd):
            raise ValueError("cmd must be a list of strings.")
        self._check_authority(kind)

        if timeout is None:
            try:
                timeout = float(
                    self.config.get("tools.default_timeout_s", 300)
                )
            except Exception:
                timeout = 300.0

        start = time.monotonic()
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(self.project_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                # Own process group so the whole tree dies together.
                start_new_session=True,
            )
        except OSError as exc:
            # Binary missing or unlaunchable: a result, not a crash.
            return ToolResult(
                127, "", str(exc), time.monotonic() - start, False
            )

        timed_out = False
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._kill_tree(proc)
            stdout, stderr = proc.communicate()
        exit_code = proc.returncode if proc.returncode is not None else -1
        duration = time.monotonic() - start
        return ToolResult(
            exit_code=exit_code,
            stdout=stdout[:_OUTPUT_CAP],
            stderr=stderr[:_OUTPUT_CAP],
            duration_s=duration,
            timed_out=timed_out,
        )

    @staticmethod
    def _kill_tree(proc: subprocess.Popen) -> None:
        """Kill the whole process tree.

        The process group dies on POSIX; the child alone on Windows.
        """
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:  # Windows: no process groups via killpg; kill the child.
                proc.kill()
        except Exception as exc:  # already dead is a fine outcome.
            log.debug("Process-tree kill: %s", exc)
            try:
                proc.kill()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Unified-diff patch application
# ---------------------------------------------------------------------------


def apply_patch(project_dir: str, patch_text: str) -> List[str]:
    """Apply a unified diff to the files under *project_dir*.

    Handles new files (``--- /dev/null``), deletions (``+++ /dev/null``),
    multiple hunks, and context lines. Returns the list of changed
    relative paths (deletions included).

    The canonical implementation lives in the Forge Worker role
    (``draupnir_forge.roles.worker``); this entry point delegates to it
    so every consumer shares one patch engine. The import is lazy to
    avoid any import-order coupling between tools and roles.

    Raises:
        PatchError: On malformed patches, context mismatches, or paths
            that escape *project_dir* (see ``draupnir_forge.tools``).
    """
    from draupnir_forge.roles.worker import apply_patch as _impl

    return _impl(project_dir, patch_text)


def __getattr__(name: str):
    """Lazily re-export ``PatchError`` from the Forge Worker role.

    The exception class is defined once (in ``roles.worker``); tools.py
    exposes it under its own name without importing the whole roles
    package at module load time.
    """
    if name == "PatchError":
        from draupnir_forge.roles.worker import PatchError

        return PatchError
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AuthorityDenied",
    "ToolResult",
    "ToolExecutor",
    "apply_patch",
    "PatchError",
]
