"""worker.py — ForgeWorker role (slice 14).

The ForgeWorker is the Forge's hands: it takes one bounded task, applies a
unified-diff patch to the project tree, runs a short list of shell commands,
and files the diff away as evidence under ``.mythis/evidence/diffs/``.

Guardrails (the worker's oath, enforced in :meth:`ForgeWorker.run`):

* the patch may not touch paths outside ``project_dir`` (no ``..`` escapes,
  no absolute paths, no drive letters);
* the patch may not touch ``.mythis/`` — the Scribe owns the canonical docs;
* shell commands stop at the first non-zero exit.

The pure :func:`apply_patch` helper parses and applies unified diffs
(``---``/``+++``/``@@`` hunks): new files (``--- /dev/null``), deletions
(``+++ /dev/null``), multiple hunks and context lines are all handled.
Malformed patches or context mismatches raise :class:`PatchError`.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .base import Role, RoleContext, RoleResult, register_role


class PatchError(Exception):
    """A unified diff could not be parsed or applied cleanly."""


# ---------------------------------------------------------------------------
# Patch model
# ---------------------------------------------------------------------------

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_NO_NEWLINE = "\\ No newline at end of file"
_DEV_NULL = "/dev/null"
_DIFF_GIT_RE = re.compile(r"^diff --git ")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


@dataclass
class _Hunk:
    """One @@ hunk of a unified diff."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: List[str] = field(default_factory=list)


@dataclass
class _FilePatch:
    """All hunks touching a single file."""

    old_path: str  # "/dev/null" for a new file
    new_path: str  # "/dev/null" for a deletion
    hunks: List[_Hunk] = field(default_factory=list)

    @property
    def target_path(self) -> str:
        """The path that survives the patch ('/dev/null' if deleted)."""
        return self.new_path


def _strip_diff_prefix(path: str) -> str:
    """Remove git's a/ b/ prefixes and surrounding quotes from a diff path."""
    path = path.strip()
    if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
        path = path[1:-1]
    if path == _DEV_NULL:
        return path
    for prefix in ("a/", "b/"):
        if path.startswith(prefix):
            return path[len(prefix):]
    return path


def _parse_patch(patch_text: str) -> List[_FilePatch]:
    """Parse a unified diff into per-file patches.

    Raises :class:`PatchError` on malformed input.
    """
    if not patch_text or not patch_text.strip():
        raise PatchError("empty patch")
    files: List[_FilePatch] = []
    current: Optional[_FilePatch] = None
    hunk: Optional[_Hunk] = None

    def close_hunk() -> None:
        nonlocal hunk
        if hunk is not None and current is not None:
            _check_hunk_counts(hunk, current)
            current.hunks.append(hunk)
        hunk = None

    for lineno, raw in enumerate(patch_text.splitlines(), start=1):
        line = raw.rstrip("\r\n")
        if _DIFF_GIT_RE.match(line) or line.startswith("Index: "):
            close_hunk()
            current = None
            continue
        if line.startswith("--- "):
            close_hunk()
            old = _strip_diff_prefix(line[4:])
            current = _FilePatch(old_path=old, new_path="")
            files.append(current)
            continue
        if line.startswith("+++ "):
            if current is None:
                raise PatchError(f"line {lineno}: '+++' without '---'")
            current.new_path = _strip_diff_prefix(line[4:])
            continue
        match = _HUNK_RE.match(line)
        if match:
            if current is None:
                raise PatchError(f"line {lineno}: hunk without file header")
            close_hunk()
            old_start = int(match.group(1))
            old_count = int(match.group(2)) if match.group(2) else 1
            new_start = int(match.group(3))
            new_count = int(match.group(4)) if match.group(4) else 1
            hunk = _Hunk(old_start, new_start=new_start,
                         old_count=old_count, new_count=new_count)
            continue
        if hunk is not None:
            if line == _NO_NEWLINE:
                hunk.lines.append(line)
            elif not line:
                # A truly empty line in a diff body is an empty context line.
                hunk.lines.append(" ")
            elif line[0] in (" ", "-", "+"):
                hunk.lines.append(line)
            else:
                raise PatchError(
                    f"line {lineno}: unexpected diff body line {line[:20]!r}")
        elif line.strip() and not line.startswith(("---", "+++")):
            # Stray text outside any hunk (e.g. extended headers) is ignored
            # only if it looks like a diff header; anything else is suspect.
            if not _DIFF_GIT_RE.match(line):
                pass
    close_hunk()
    if not files:
        raise PatchError("no file headers ('---'/'+++') found in patch")
    for fp in files:
        if not fp.new_path:
            raise PatchError(f"missing '+++' header for {fp.old_path!r}")
        if not fp.hunks and fp.old_path != _DEV_NULL \
                and fp.new_path != _DEV_NULL:
            raise PatchError(f"no hunks for {fp.target_path!r}")
    return files


def _check_hunk_counts(hunk: _Hunk, fp: _FilePatch) -> None:
    """Verify a hunk's body matches its @@ counts. Raises PatchError."""
    old_n = sum(1 for ln in hunk.lines if ln[:1] in (" ", "-"))
    new_n = sum(1 for ln in hunk.lines if ln[:1] in (" ", "+"))
    if old_n != hunk.old_count or new_n != hunk.new_count:
        raise PatchError(
            f"hunk @@ -{hunk.old_start},{hunk.old_count} "
            f"+{hunk.new_start},{hunk.new_count} @@ in "
            f"{fp.target_path!r}: body has {old_n} old / {new_n} new lines")


def _apply_hunks(old_lines: List[str], hunks: List[_Hunk],
                 target: str) -> Tuple[List[str], bool]:
    """Apply hunks to old file lines.

    Returns (new_lines, trailing_newline). Raises PatchError on mismatch.
    """
    new_lines: List[str] = []
    pos = 0  # 0-based cursor into old_lines
    trailing_newline = True
    for hunk in hunks:
        # old_start is 1-based; 0 marks "before the first line", used by
        # new-file hunks (@@ -0,0 ... @@).
        expected = max(hunk.old_start - 1, 0)
        if expected < pos:
            raise PatchError(
                f"{target!r}: overlapping/out-of-order hunks at "
                f"@@ -{hunk.old_start}")
        # Copy any untouched gap between the cursor and this hunk verbatim.
        while pos < expected:
            if pos >= len(old_lines):
                raise PatchError(
                    f"{target!r}: hunk @@ -{hunk.old_start} starts past "
                    f"end of file ({len(old_lines)} lines)")
            new_lines.append(old_lines[pos])
            pos += 1
        for bl in hunk.lines:
            if bl == _NO_NEWLINE:
                # The file's tail carries no trailing newline.
                trailing_newline = False
                continue
            kind, text = bl[0], bl[1:]
            if kind == " ":
                if pos >= len(old_lines) or old_lines[pos] != text:
                    raise PatchError(
                        f"{target!r}: context mismatch near line {pos + 1}: "
                        f"expected {text!r}")
                new_lines.append(text)
                pos += 1
            elif kind == "-":
                if pos >= len(old_lines) or old_lines[pos] != text:
                    raise PatchError(
                        f"{target!r}: removal mismatch near line {pos + 1}: "
                        f"expected {text!r}")
                pos += 1
            elif kind == "+":
                new_lines.append(text)
            else:  # pragma: no cover - guarded in _parse_patch
                raise PatchError(f"{target!r}: bad hunk line {bl[:20]!r}")
    # Copy the untouched tail of the file.
    new_lines.extend(old_lines[pos:])
    return new_lines, trailing_newline


def _safe_target(project_dir: str, rel_path: str) -> str:
    """Resolve a patch path inside project_dir.

    Raises PatchError on absolute paths, drive letters, or ``..`` escapes.
    """
    if not rel_path or rel_path == _DEV_NULL:
        raise PatchError(f"invalid patch path {rel_path!r}")
    if os.path.isabs(rel_path) or _DRIVE_RE.match(rel_path):
        raise PatchError(f"absolute path refused: {rel_path!r}")
    parts = rel_path.replace("\\", "/").split("/")
    if ".." in parts:
        raise PatchError(f"path traversal refused: {rel_path!r}")
    base = os.path.abspath(project_dir)
    full = os.path.abspath(os.path.join(base, *parts))
    if os.path.commonpath([base, full]) != base:
        raise PatchError(f"path escapes project dir: {rel_path!r}")
    return full


def apply_patch(project_dir: str, patch_text: str, *, dry_run: bool = False) -> List[str]:
    """Apply a unified diff to the files under project_dir.

    Returns the list of changed relative paths (deletions included).
    Raises :class:`PatchError` on malformed patches, context mismatches,
    or paths that escape project_dir.

    When ``dry_run`` is True, the patch is fully validated — malformed
    patches, context mismatches, new-file collisions, and path escapes
    all raise :class:`PatchError` exactly as a real application would —
    but nothing is written, created, or deleted. The returned list is the
    set of relative paths that *would* change.
    """
    file_patches = _parse_patch(patch_text)
    changed: List[str] = []
    for fp in file_patches:
        rel = fp.target_path
        if rel == _DEV_NULL:
            # Pure deletion with no surviving path: use the old path.
            rel = fp.old_path
        target = _safe_target(project_dir, rel)
        display = os.path.relpath(target, os.path.abspath(project_dir))
        display = display.replace(os.sep, "/")

        if fp.old_path == _DEV_NULL:
            # New file.
            if os.path.exists(target):
                raise PatchError(f"new file already exists: {display!r}")
            old_lines: List[str] = []
            had_newline = True
        else:
            if not os.path.isfile(target):
                raise PatchError(f"patched file not found: {display!r}")
            with open(target, "r", encoding="utf-8") as fh:
                old_text = fh.read()
            old_lines = old_text.splitlines()
            had_newline = old_text.endswith("\n") or not old_text

        # Full validation: hunks must apply cleanly against current content.
        _apply_hunks(old_lines, fp.hunks, display)
        if dry_run:
            changed.append(display)
            continue

        new_lines, want_newline = _apply_hunks(old_lines, fp.hunks, display)
        if fp.old_path == _DEV_NULL:
            want_newline = want_newline and had_newline

        if fp.new_path == _DEV_NULL:
            os.remove(target)
        else:
            parent = os.path.dirname(target)
            if parent:
                os.makedirs(parent, exist_ok=True)
            text = "\n".join(new_lines)
            if new_lines and want_newline:
                text += "\n"
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(text)
        changed.append(display)
    return changed


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------

def _refusal_reason(project_dir: str, rel_path: str) -> Optional[str]:
    """Return a refusal reason if a patch path violates guardrails."""
    if not rel_path or rel_path == _DEV_NULL:
        return "invalid path"
    if os.path.isabs(rel_path) or _DRIVE_RE.match(rel_path):
        return "absolute paths are forbidden"
    parts = rel_path.replace("\\", "/").split("/")
    if ".." in parts:
        return "path traversal ('..') is forbidden"
    base = os.path.abspath(project_dir)
    full = os.path.abspath(os.path.join(base, *parts))
    if os.path.commonpath([base, full]) != base:
        return "path escapes the project directory"
    if parts[0] == ".mythis":
        return ".mythis/ is owned by the Scribe; the worker may not edit it"
    return None


def _sanitize_task_id(task_id: str) -> str:
    """Make a task id safe for use as a file name."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", task_id) or "unknown"


# ---------------------------------------------------------------------------
# The role
# ---------------------------------------------------------------------------

@register_role
class ForgeWorker(Role):
    """Implementation agent: applies patches and runs bounded commands."""

    name = "forge_worker"
    purpose = ("Applies one unified-diff patch and runs a bounded list of "
               "shell commands, filing evidence under .mythis/evidence/diffs/.")

    command_timeout: int = 120  # seconds per shell command

    # -- internals ------------------------------------------------------

    def _run_commands(self, project_dir: str,
                      commands: List[str]) -> Tuple[bool, List[Dict], str]:
        """Run shell commands in order; stop on first non-zero exit.

        Returns (all_ok, outputs, summary_note).
        """
        outputs: List[Dict] = []
        for cmd in commands:
            try:
                proc = subprocess.run(
                    cmd, shell=True, cwd=project_dir,
                    capture_output=True, text=True,
                    timeout=self.command_timeout)
                outputs.append({
                    "command": cmd,
                    "returncode": proc.returncode,
                    "stdout": proc.stdout,
                    "stderr": proc.stderr,
                })
                if proc.returncode != 0:
                    note = (f"command failed (exit {proc.returncode}): {cmd}\n"
                            f"--- stdout ---\n{proc.stdout}"
                            f"--- stderr ---\n{proc.stderr}")
                    return False, outputs, note
            except subprocess.TimeoutExpired:
                outputs.append({"command": cmd, "returncode": None,
                                "stdout": "", "stderr": "timed out"})
                return False, outputs, (
                    f"command timed out after {self.command_timeout}s: {cmd}")
            except OSError as exc:
                outputs.append({"command": cmd, "returncode": None,
                                "stdout": "", "stderr": str(exc)})
                return False, outputs, f"command failed to start: {cmd}: {exc}"
        return True, outputs, ""

    # -- Role contract --------------------------------------------------

    def run(self, ctx: RoleContext) -> RoleResult:
        """Apply the task's patch and commands. Never raises."""
        try:
            return self._execute(ctx)
        except Exception as exc:  # the Forge must not fall mid-swing
            return RoleResult(ok=False,
                              summary=f"forge_worker crashed: {exc}")

    def _execute(self, ctx: RoleContext) -> RoleResult:
        task = ctx.task
        goal = getattr(task, "goal", None) if task is not None else None
        if not goal:
            return RoleResult(ok=False,
                              summary="refused: task is missing a goal")
        patch_text = ctx.artifacts.get("patch")
        if not patch_text or not str(patch_text).strip():
            return RoleResult(ok=False,
                              summary="refused: no patch text in artifacts")
        patch_text = str(patch_text)
        project_dir = os.path.abspath(ctx.project_dir)

        # Parse first so guardrails see every touched path before anything
        # is written; a refusal must leave the tree untouched.
        try:
            file_patches = _parse_patch(patch_text)
        except PatchError as exc:
            return RoleResult(ok=False,
                              summary=f"refused: malformed patch: {exc}")
        for fp in file_patches:
            for rel in (fp.old_path, fp.new_path):
                if rel == _DEV_NULL:
                    continue
                reason = _refusal_reason(project_dir, _strip_diff_prefix(rel))
                if reason:
                    return RoleResult(
                        ok=False,
                        summary=f"refused: {reason}: {rel}")

        try:
            changed = apply_patch(project_dir, patch_text)
        except PatchError as exc:
            return RoleResult(ok=False,
                              summary=f"patch failed to apply: {exc}")
        except OSError as exc:
            return RoleResult(ok=False,
                              summary=f"patch I/O error: {exc}")

        # File the diff as evidence (the worker's own write, not the patch's).
        task_id = _sanitize_task_id(
            str(getattr(task, "task_id",
                        getattr(task, "id", "unknown")) or "unknown"))
        evidence_dir = os.path.join(project_dir, ".mythis", "evidence", "diffs")
        evidence_path = os.path.join(evidence_dir, f"{task_id}.diff")
        try:
            os.makedirs(evidence_dir, exist_ok=True)
            with open(evidence_path, "w", encoding="utf-8") as fh:
                fh.write(patch_text if patch_text.endswith("\n")
                         else patch_text + "\n")
        except OSError as exc:
            return RoleResult(
                ok=False,
                summary=(f"patch applied ({len(changed)} file(s)) but "
                         f"evidence write failed: {exc}"),
                artifacts={"changed_files": changed})

        artifacts: Dict = {
            "changed_files": changed,
            "diff_path": os.path.relpath(evidence_path, project_dir),
        }

        commands = ctx.artifacts.get("commands") or []
        if commands:
            if not isinstance(commands, list) or not all(
                    isinstance(c, str) for c in commands):
                return RoleResult(
                    ok=False,
                    summary="refused: 'commands' must be a list of strings",
                    artifacts=artifacts)
            cmds_ok, outputs, note = self._run_commands(project_dir, commands)
            artifacts["command_outputs"] = outputs
            if not cmds_ok:
                return RoleResult(ok=False, summary=note, artifacts=artifacts)

        self.emit(ctx, "worker.patch_applied",
                  {"task_id": task_id, "changed_files": changed})
        return RoleResult(
            ok=True,
            summary=(f"applied patch to {len(changed)} file(s); "
                     f"ran {len(commands)} command(s)"),
            artifacts=artifacts)
