"""repair.py — Slice 37: the self-repair engine, the Forge mending its own work.

When a task fails, the Forge does not blindly swing the hammer again —
Forge Law says failure should produce knowledge, not blind repetition.
The :class:`RepairEngine` takes the failure, classifies it, picks a
data-driven repair hint from ``data/repair_hints.yaml``, and attempts a
*bounded* fix:

- only IMPLEMENTATION_ERROR and TEST_FAILURE are repairable; anything
  else is handed back for replanning;
- the patch may touch at most 3 files and 50 changed lines;
- test files are NEVER edited to make tests pass — a patch touching a
  test is refused outright;
- after applying, the Tester role re-runs the suite; green means
  repaired, red means a fresh :class:`FailureRecord` for the caller;
- at most 3 attempts per task, tracked in
  ``.mythis/repair_history.jsonl``; then the engine gives up and the
  caller replans (escalation ladder, §16).

Every attempt is preserved as evidence in
``.mythis/evidence/repair/<utc-ts>.log``: the hint, the patch, and the
test outcome.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from draupnir_forge import _paths
from draupnir_forge.failures import (
    FailureClass,
    FailureRecord,
    classify_failure,
)
from draupnir_forge.roles import cartographer
from draupnir_forge.roles.tester import run_tests
from draupnir_forge.roles.worker import PatchError, apply_patch
from draupnir_forge.tasks import ForgeTask

log = logging.getLogger("draupnir_forge.repair")

#: Repair hint templates (DATA, never hardcoded).
HINTS_FILE = "repair_hints.yaml"

#: Hard bounds: the forge mends with a needle, not a broadsword.
MAX_ATTEMPTS = 3
MAX_PATCH_FILES = 3
MAX_PATCH_LINES = 50

_HISTORY_FILE = ".mythis/repair_history.jsonl"
_EVIDENCE_SUBDIR = Path(".mythis") / "evidence" / "repair"

#: Failure classes the engine will even attempt to repair.
#: DEPENDENCY_FAILURE is included for the bounded missing-requirement
#: fix (ImportError -> "check dependency file" hint); everything else
#: outside this set goes straight back for replanning.
REPAIRABLE = frozenset({
    FailureClass.IMPLEMENTATION_ERROR,
    FailureClass.TEST_FAILURE,
    FailureClass.DEPENDENCY_FAILURE,
})

_hints_cache: Optional[List[Dict[str, Any]]] = None


@dataclass
class RepairResult:
    """What one repair attempt accomplished."""

    repaired: bool
    patch: Optional[str]            # the unified diff that was applied
    new_failure: Optional[FailureRecord]  # set when still failing
    attempts: int                  # attempts used for this task so far


# ---------------------------------------------------------------------------
# Hint templates (data-driven)
# ---------------------------------------------------------------------------


def load_hints() -> List[Dict[str, Any]]:
    """Load the repair-hint rows from the YAML data file (cached)."""
    global _hints_cache
    if _hints_cache is None:
        try:
            data = _paths.load_data_yaml(HINTS_FILE)
        except Exception as exc:
            log.warning("Repair hints unreadable: %s", exc)
            data = {}
        rows = data.get("hints", []) or []
        _hints_cache = [r for r in rows if isinstance(r, dict)]
    return _hints_cache


def select_hint(failure_class: FailureClass,
                detail: str) -> Tuple[str, str]:
    """Pick the first matching hint row; returns (hint, strategy).

    The regex scans "class value + detail". ``{g1}``-style placeholders
    are filled from capture groups. Falls back to a generic hint with
    strategy "none" when nothing matches.
    """
    haystack = f"{failure_class.value} {detail or ''}"
    for row in load_hints():
        classes = row.get("failure_classes", []) or []
        if classes and failure_class.value not in [str(c) for c in classes]:
            continue
        pattern = str(row.get("pattern", "") or "")
        try:
            match = re.search(pattern, haystack, re.IGNORECASE | re.DOTALL)
        except re.error:
            continue
        if not match:
            continue
        template = str(row.get("hint", "") or "")
        groups = {"g%d" % i: (g or "") for i, g in
                  enumerate(match.groups(), start=1)}
        try:
            hint = template.format(**groups)
        except (KeyError, IndexError, ValueError):
            hint = template
        return hint, str(row.get("strategy", "none") or "none")
    return ("No repair hint matched this failure; escalate for a fresh "
            "pair of eyes."), "none"


# ---------------------------------------------------------------------------
# Small diff helpers
# ---------------------------------------------------------------------------


def _unified_diff(rel_path: str, old_lines: List[str],
                  new_lines: List[str]) -> str:
    """Build a minimal unified diff turning old_lines into new_lines.

    Emits one hunk per contiguous changed region with three lines of
    context, in the shape the worker's patch parser accepts.
    """
    import difflib

    fromfile = "/dev/null" if not old_lines else f"a/{rel_path}"
    diff = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=fromfile, tofile=f"b/{rel_path}",
        n=3, lineterm="",
    )
    text = "\n".join(diff)
    return text + "\n" if text else ""


def _patch_stats(patch_text: str) -> Tuple[int, int, List[str]]:
    """Return (file_count, changed_line_count, touched_paths) for a diff."""
    files: List[str] = []
    changed = 0
    for line in patch_text.splitlines():
        if line.startswith("--- "):
            path = line[4:].strip()
            for prefix in ("a/", "b/"):
                if path.startswith(prefix):
                    path = path[len(prefix):]
            files.append(path.strip('"'))
        elif line.startswith(("+++ ", "@@ ")):
            continue
        elif line.startswith(("+", "-")):
            changed += 1
    return len(files), changed, files


def _is_test_path(rel_path: str) -> bool:
    """True when a repo-relative path is a test file or lives under tests."""
    parts = rel_path.replace("\\", "/").split("/")
    name = parts[-1]
    if any(part in ("test", "tests") for part in parts[:-1]):
        return True
    return (name.startswith("test_") or name.endswith("_test.py")
            or name == "conftest.py")


def _within_bounds(patch_text: str) -> Tuple[bool, str]:
    """Check the patch obeys the Forge's repair bounds.

    Returns (ok, reason): at most MAX_PATCH_FILES files, MAX_PATCH_LINES
    changed lines, and never a test file.
    """
    n_files, n_lines, touched = _patch_stats(patch_text)
    if n_files == 0:
        return False, "patch touches no files"
    if n_files > MAX_PATCH_FILES:
        return False, (f"patch touches {n_files} files "
                       f"(limit {MAX_PATCH_FILES})")
    if n_lines > MAX_PATCH_LINES:
        return False, (f"patch changes {n_lines} lines "
                       f"(limit {MAX_PATCH_LINES})")
    for path in touched:
        if _is_test_path(path):
            return False, f"patch touches a test file: {path} — refused"
    return True, ""


# ---------------------------------------------------------------------------
# Bounded auto-fix strategies
# ---------------------------------------------------------------------------


def _iter_py_files(project_dir: Path):
    """Yield (relative posix path, absolute Path) for repo Python files."""
    for dirpath, dirnames, filenames in os.walk(project_dir):
        dirnames[:] = [d for d in dirnames
                       if d not in cartographer.SKIP_DIRS
                       and not d.startswith(".")]
        for filename in filenames:
            if filename.endswith(".py"):
                full = Path(dirpath) / filename
                yield full.relative_to(project_dir).as_posix(), full


def _top_level_names(source: str) -> List[str]:
    """Top-level defined names in a Python source string. Never raises."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    names: List[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.append(target.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                names.append(node.target.id)
    return names


def _find_definition(project_dir: Path, name: str) -> Optional[str]:
    """Find the repo module (dotted) that defines ``name`` at top level.

    Skips test files — the repair must come from real code. Returns the
    module's dotted name, or None when nothing defines it.
    """
    for rel, full in _iter_py_files(project_dir):
        if _is_test_path(rel):
            continue
        try:
            source = full.read_text(encoding="utf-8")
        except OSError:
            continue
        if name in _top_level_names(source):
            return cartographer._module_name(rel)
    return None


def _local_module_exists(project_dir: Path, top_name: str) -> bool:
    """True when the repo itself provides a top-level module ``top_name``."""
    for rel, _full in _iter_py_files(project_dir):
        first = cartographer._module_name(rel).split(".")[0]
        if first == top_name:
            return True
    return False


def _strategy_add_missing_import(project_dir: Path, detail: str,
                                 impl: Dict[str, Any]) -> Optional[str]:
    """Fix ``NameError: name 'X' is not defined`` with a real import.

    Locates the module defining X in the repo and inserts
    ``from <module> import X`` after the last top-level import of the
    failing file. Returns a unified diff, or None when no definition
    or target file can be found.
    """
    match = re.search(r"name '(\w+)' is not defined", detail or "")
    if not match:
        return None
    name = match.group(1)
    rel_file = _failure_file(project_dir, detail, impl)
    if not rel_file:
        return None
    defining = _find_definition(project_dir, name)
    if not defining:
        log.info("No repo definition found for name %r", name)
        return None
    if defining == cartographer._module_name(rel_file):
        return None  # defined in the same file; an import cannot help
    target = project_dir / rel_file
    try:
        old_text = target.read_text(encoding="utf-8")
    except OSError:
        return None
    old_lines = old_text.splitlines()
    import_line = f"from {defining} import {name}"
    if import_line in old_lines:
        return None  # already imported; the failure lies elsewhere
    insert_at = 0
    for idx, line in enumerate(old_lines):
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")) and " " in stripped:
            insert_at = idx + 1
    new_lines = old_lines[:insert_at] + [import_line] + old_lines[insert_at:]
    return _unified_diff(rel_file, old_lines, new_lines)


def _strategy_add_requirement(project_dir: Path, detail: str,
                              impl: Dict[str, Any]) -> Optional[str]:
    """Fix ``ModuleNotFoundError: No module named 'X'`` for third-party deps.

    Appends the missing package to requirements.txt (creating it when
    absent). Declines when the repo itself provides the module — that
    failure wants an import fix, not a new dependency.
    """
    match = re.search(r"No module named '([\w.]+)'", detail or "")
    if not match:
        return None
    top = match.group(1).split(".")[0]
    if _local_module_exists(project_dir, top):
        log.info("Module %r exists locally; not a missing requirement", top)
        return None
    req_path = project_dir / "requirements.txt"
    if req_path.is_file():
        try:
            old_text = req_path.read_text(encoding="utf-8")
        except OSError:
            return None
        old_lines = old_text.splitlines()
        if any(line.strip().split("=")[0].split(">")[0].split("<")[0].split(
                ";")[0].strip().lower() == top.lower()
               for line in old_lines if line.strip()):
            return None  # already declared
        new_lines = old_lines + [top]
        return _unified_diff("requirements.txt", old_lines, new_lines)
    return _unified_diff("requirements.txt", [], [top])


def _failure_file(project_dir: Path, detail: str,
                  impl: Dict[str, Any]) -> Optional[str]:
    """Best guess at the repo-relative file the failure points to.

    Prefers ``impl["file"]``; otherwise scans traceback ``File "..."``
    frames for the last one inside the project.
    """
    candidate = impl.get("file")
    if isinstance(candidate, str) and candidate:
        rel = candidate.replace("\\", "/").lstrip("./")
        if (project_dir / rel).is_file() and not _is_test_path(rel):
            return rel
    frames = re.findall(r'File "([^"]+\.py)"', detail or "")
    for frame in reversed(frames):
        frame_path = Path(frame)
        if not frame_path.is_absolute():
            # Traceback frames are often repo-relative: resolve them
            # against the project, not the current working directory.
            rel = frame.replace("\\", "/").lstrip("./")
        else:
            try:
                rel = frame_path.relative_to(project_dir).as_posix()
            except ValueError:
                continue
        if rel.startswith(".."):
            continue
        if (project_dir / rel).is_file() and not _is_test_path(rel):
            return rel
    return None


_STRATEGIES: Dict[str, Callable[[Path, str, Dict[str, Any]], Optional[str]]] = {
    "add_missing_import": _strategy_add_missing_import,
    "add_requirement": _strategy_add_requirement,
}


# ---------------------------------------------------------------------------
# History and evidence
# ---------------------------------------------------------------------------


def _read_history(project_dir: Path) -> List[Dict[str, Any]]:
    """Read the repair history JSONL; corrupt lines are skipped."""
    path = project_dir / _HISTORY_FILE
    records: List[Dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return records
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _append_history(project_dir: Path, record: Dict[str, Any]) -> None:
    """Append one attempt record to the history JSONL. Never raises."""
    try:
        path = project_dir / _HISTORY_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    except OSError as exc:
        log.warning("Could not append repair history: %s", exc)


def _write_evidence(project_dir: Path, task_id: str, attempt_no: int,
                    hint: str, patch: Optional[str],
                    outcome: str) -> Optional[str]:
    """Write the attempt log under .mythis/evidence/repair/. Never raises."""
    try:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        evidence_dir = project_dir / _EVIDENCE_SUBDIR
        evidence_dir.mkdir(parents=True, exist_ok=True)
        safe_task = re.sub(r"[^A-Za-z0-9_-]", "_", task_id or "unknown")
        path = evidence_dir / f"{stamp}_{safe_task}_a{attempt_no}.log"
        body = (
            f"# Repair attempt {attempt_no} for task {task_id} — {stamp} UTC\n"
            f"# hint: {hint}\n"
            f"# outcome: {outcome}\n"
            f"{'#' * 60}\n"
            f"## patch\n{(patch or '(no patch)')}\n"
            f"{'#' * 60}\n"
        )
        path.write_text(body, encoding="utf-8")
        return str(path)
    except OSError as exc:
        log.warning("Could not write repair evidence: %s", exc)
        return None


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


class RepairEngine:
    """Attempts bounded self-repairs of failed forge tasks.

    Args:
        project_dir: Root of the forge project holding ``.mythis/``.
    """

    def __init__(self, project_dir: Union[str, Path]) -> None:
        self.project_dir = Path(project_dir)

    def attempt(self, task: ForgeTask, failure: FailureRecord,
                impl: Optional[Dict[str, Any]] = None) -> RepairResult:
        """Try to repair one failure; never raises.

        Args:
            task: The failed forge task.
            failure: What broke (carries its :class:`FailureClass`).
            impl: The implementation record; may carry ``"patch"`` (a
                candidate unified diff) and/or ``"file"`` (the file the
                failure points at).

        Returns:
            A :class:`RepairResult`. ``repaired`` is True only when the
            patch applied within bounds *and* the Tester re-ran green.
        """
        impl = dict(impl or {})
        task_id = getattr(task, "task_id", "") or ""
        history = _read_history(self.project_dir)
        prior = sum(1 for r in history
                    if str(r.get("task_id", "")) == task_id)

        failure_class = failure.failure_class
        if failure_class is FailureClass.UNKNOWN:
            try:
                failure_class = classify_failure(context=failure.detail or "")
            except Exception:
                failure_class = FailureClass.UNKNOWN

        if prior >= MAX_ATTEMPTS:
            log.info("Repair budget exhausted for task %s (%d attempts)",
                     task_id, prior)
            return RepairResult(repaired=False, patch=None,
                                new_failure=failure, attempts=MAX_ATTEMPTS)

        if failure_class not in REPAIRABLE:
            log.info("Failure class %s is not repairable; replanning",
                     failure_class.value)
            return RepairResult(repaired=False, patch=None,
                                new_failure=failure, attempts=prior)

        attempt_no = prior + 1
        hint, strategy_name = select_hint(failure_class, failure.detail or "")

        patch_text = impl.get("patch")
        if isinstance(patch_text, str) and patch_text.strip():
            log.info("Repair attempt %d for %s uses the impl's own patch",
                     attempt_no, task_id)
        else:
            strategy = _STRATEGIES.get(strategy_name)
            patch_text = None
            if strategy is not None:
                try:
                    patch_text = strategy(self.project_dir,
                                          failure.detail or "", impl)
                except Exception as exc:
                    log.warning("Repair strategy %s failed: %s",
                                strategy_name, exc)

        if not patch_text or not str(patch_text).strip():
            outcome = (f"no bounded patch available (strategy: "
                       f"{strategy_name}); hint recorded")
            _write_evidence(self.project_dir, task_id, attempt_no, hint,
                            None, outcome)
            _append_history(self.project_dir, {
                "ts": datetime.now(timezone.utc).isoformat(),
                "task_id": task_id, "attempt": attempt_no,
                "failure_class": failure_class.value,
                "hint": hint, "strategy": strategy_name,
                "repaired": False, "reason": "no_patch",
            })
            return RepairResult(repaired=False, patch=None,
                                new_failure=failure, attempts=attempt_no)

        patch_text = str(patch_text)
        ok, reason = _within_bounds(patch_text)
        if not ok:
            outcome = f"patch refused: {reason}"
            _write_evidence(self.project_dir, task_id, attempt_no, hint,
                            patch_text, outcome)
            _append_history(self.project_dir, {
                "ts": datetime.now(timezone.utc).isoformat(),
                "task_id": task_id, "attempt": attempt_no,
                "failure_class": failure_class.value,
                "hint": hint, "strategy": strategy_name,
                "repaired": False, "reason": f"patch_refused: {reason}",
            })
            refusal = FailureRecord(
                failure_class=failure_class,
                task_id=task_id,
                detail=(f"repair attempt {attempt_no} refused: {reason}; "
                        f"original failure: {failure.detail or ''}"),
                attempt=attempt_no,
            )
            return RepairResult(repaired=False, patch=patch_text,
                                new_failure=refusal, attempts=attempt_no)

        try:
            changed = apply_patch(str(self.project_dir), patch_text)
        except PatchError as exc:
            outcome = f"patch failed to apply: {exc}"
            _write_evidence(self.project_dir, task_id, attempt_no, hint,
                            patch_text, outcome)
            _append_history(self.project_dir, {
                "ts": datetime.now(timezone.utc).isoformat(),
                "task_id": task_id, "attempt": attempt_no,
                "failure_class": failure_class.value,
                "hint": hint, "strategy": strategy_name,
                "repaired": False, "reason": f"apply_failed: {exc}",
            })
            return RepairResult(repaired=False, patch=patch_text,
                                new_failure=failure, attempts=attempt_no)
        except OSError as exc:
            log.warning("Patch I/O error: %s", exc)
            return RepairResult(repaired=False, patch=patch_text,
                                new_failure=failure, attempts=attempt_no)

        # The witnesses must confirm the mending.
        try:
            test_result = run_tests(str(self.project_dir))
        except Exception as exc:
            log.warning("Test re-run failed: %s", exc)
            test_result = None

        if test_result is not None and test_result.ok:
            outcome = (f"tests green after repair "
                       f"({test_result.passed} passed)")
            _write_evidence(self.project_dir, task_id, attempt_no, hint,
                            patch_text, outcome)
            _append_history(self.project_dir, {
                "ts": datetime.now(timezone.utc).isoformat(),
                "task_id": task_id, "attempt": attempt_no,
                "failure_class": failure_class.value,
                "hint": hint, "strategy": strategy_name,
                "repaired": True, "changed": changed,
            })
            return RepairResult(repaired=True, patch=patch_text,
                                new_failure=None, attempts=attempt_no)

        detail = ""
        if test_result is not None:
            detail = (f"still failing after repair attempt {attempt_no}: "
                      f"{test_result.failed} failed, {test_result.errors} "
                      f"errors\n{test_result.raw_output[-2000:]}")
        new_failure = FailureRecord(
            failure_class=FailureClass.TEST_FAILURE,
            task_id=task_id,
            detail=detail or "test re-run produced no usable output",
            attempt=attempt_no,
        )
        outcome = "tests still red after repair"
        _write_evidence(self.project_dir, task_id, attempt_no, hint,
                        patch_text, outcome)
        _append_history(self.project_dir, {
            "ts": datetime.now(timezone.utc).isoformat(),
            "task_id": task_id, "attempt": attempt_no,
            "failure_class": failure_class.value,
            "hint": hint, "strategy": strategy_name,
            "repaired": False, "reason": "tests_still_red",
        })
        return RepairResult(repaired=False, patch=patch_text,
                            new_failure=new_failure, attempts=attempt_no)
