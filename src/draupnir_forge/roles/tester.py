"""Slice 16 — Tester role: the Forge's witness at the running of the tests.

The Tester discovers how a project runs its tests (pytest, unittest, or
npm), runs the suite with a timeout, parses the outcome with regexes kept
as DATA in ``data/test_output_patterns.yaml``, classifies each failing
test block with ``failures.py``, and writes the full output to
``.mythis/evidence/tests/<utc-ts>.log``.

Law of the Forge: tests are witnesses. The Tester NEVER edits a test to
make it pass; a red witness is reported, not silenced.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from draupnir_forge import _paths
from draupnir_forge.events import EventType
from draupnir_forge.failures import FailureClass, classify_failure
from draupnir_forge.roles.base import Role, RoleContext, RoleResult, register_role

log = logging.getLogger("draupnir_forge.roles.tester")

# Which data file holds the parsing regexes (DATA, never hardcoded).
PATTERNS_FILE = "test_output_patterns.yaml"

# Only the tail of the output rides along in the result; the full stream
# is preserved in the evidence log.
RAW_OUTPUT_TAIL_CHARS = 4000

# Where full test logs are kept, under the project directory.
EVIDENCE_SUBDIR = Path(".mythis") / "evidence" / "tests"

# How long we wait when probing for an importable test runner.
_PROBE_TIMEOUT_S = 30


# ---------------------------------------------------------------------------
# Pattern loading (cached; the Forge never crashes over missing data files
# in a role — run() converts that into an honest ok=False result).
# ---------------------------------------------------------------------------

_patterns_cache: Optional[Dict[str, Any]] = None


def load_patterns() -> Dict[str, Any]:
    """Load the test-output regex data file (cached after first read)."""
    global _patterns_cache
    if _patterns_cache is None:
        data = _paths.load_data_yaml(PATTERNS_FILE)
        _patterns_cache = data.get("runners", {}) or {}
    return _patterns_cache


# ---------------------------------------------------------------------------
# TestResult
# ---------------------------------------------------------------------------


@dataclass
class TestResult:
    """What one test run witnessed."""

    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration_s: float = 0.0
    raw_output: str = ""          # tail (RAW_OUTPUT_TAIL_CHARS) of the output
    failures: List[FailureClass] = field(default_factory=list)
    command: List[str] = field(default_factory=list)  # the argv that was run

    @property
    def ok(self) -> bool:
        """Green iff nothing failed and nothing errored."""
        return self.failed == 0 and self.errors == 0

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.errors + self.skipped


# ---------------------------------------------------------------------------
# Command discovery
# ---------------------------------------------------------------------------


def _pytest_importable(python: str) -> bool:
    """True when ``python -c 'import pytest'`` succeeds."""
    try:
        proc = subprocess.run(
            [python, "-c", "import pytest"],
            capture_output=True,
            timeout=_PROBE_TIMEOUT_S,
        )
        return proc.returncode == 0
    except Exception as exc:  # Huginn reports, never panics.
        log.warning("pytest probe failed: %s", exc)
        return False


def detect_command(project_dir: str) -> Optional[List[str]]:
    """Discover the command that runs this project's tests.

    Preference order: ``python -m pytest -q`` (if pytest is importable),
    then ``python -m unittest discover -s tests`` (if a tests/ dir exists),
    then ``npm test`` (if package.json defines a test script and npm is
    on PATH). Returns ``None`` when no test command can be found.

    Args:
        project_dir: Root of the project to inspect.

    Returns:
        The argv list to run, or ``None``.
    """
    project = Path(project_dir)
    python = sys.executable

    if _pytest_importable(python):
        return [python, "-m", "pytest", "-q"]

    tests_dir = project / "tests"
    if tests_dir.is_dir():
        return [python, "-m", "unittest", "discover", "-s", "tests"]

    package_json = project / "package.json"
    if package_json.is_file():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
            scripts = data.get("scripts") or {}
            if isinstance(scripts, dict) and "test" in scripts:
                if shutil.which("npm"):
                    return ["npm", "test"]
                log.warning("package.json has a test script but npm is missing")
        except Exception as exc:
            log.warning("Could not read %s: %s", package_json, exc)

    return None


# ---------------------------------------------------------------------------
# Output parsing (regexes come from the YAML data file)
# ---------------------------------------------------------------------------


def _int_last(matches: List[str]) -> int:
    """Last numeric match wins (the summary line sits at the end)."""
    if not matches:
        return 0
    try:
        return int(matches[-1])
    except (TypeError, ValueError):
        return 0


def _safe_findall(regex: str, output: str) -> List[str]:
    try:
        return re.findall(regex, output)
    except re.error as exc:
        log.warning("Bad test-output regex %r: %s", regex, exc)
        return []


def _parse_pytest(
    output: str, cfg: Dict[str, Any]
) -> Tuple[Dict[str, int], List[str], List[str]]:
    """Parse ``pytest -q`` output into counts and per-test blocks."""
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    for entry in cfg.get("summary", []) or []:
        if not isinstance(entry, dict):
            continue
        target = entry.get("target", "")
        if target in counts:
            counts[target] = _int_last(_safe_findall(entry.get("regex", ""), output))
    failure_blocks = _safe_findall(cfg.get("failure_line", r"$^"), output)
    error_blocks = _safe_findall(cfg.get("error_line", r"$^"), output)
    return counts, failure_blocks, error_blocks


def _parse_unittest(
    output: str, cfg: Dict[str, Any]
) -> Tuple[Dict[str, int], List[str], List[str]]:
    """Parse ``python -m unittest`` output into counts and per-test blocks."""
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    field_map = cfg.get("status_fields", {}) or {}
    ran = _int_last(_safe_findall(cfg.get("ran", r"$^"), output))

    status_matches = _safe_findall(cfg.get("status", r"$^"), output)
    for match in status_matches:
        # Pattern has two groups: (OK|FAILED, inner counts).
        status, inner = (match + ("", ""))[:2] if isinstance(match, tuple) else ("", "")
        for name, value in _safe_findall(cfg.get("count", r"$^"), inner or ""):
            target = field_map.get(name)
            if target in counts:
                try:
                    counts[target] = int(value)
                except ValueError:
                    pass
        # unittest never names passed; derive it from what ran.
        counts["passed"] = max(
            0, ran - counts["failed"] - counts["errors"] - counts["skipped"]
        )

    failure_blocks = _safe_findall(cfg.get("failure_line", r"$^"), output)
    error_blocks = _safe_findall(cfg.get("error_line", r"$^"), output)
    return counts, failure_blocks, error_blocks


def parse_test_output(
    output: str, command: Optional[List[str]] = None
) -> Tuple[Dict[str, int], List[str], List[str]]:
    """Parse raw test output into (counts, failure_blocks, error_blocks).

    The runner is guessed from the command when given, else from the
    output's shape (unittest's "Ran N tests" is unmistakable).

    Args:
        output: The combined stdout/stderr of the test run.
        command: The argv that produced the output, if known.

    Returns:
        (counts dict, failure-block list, error-block list).
    """
    patterns = load_patterns()
    text = output or ""
    joined = " ".join(command or [])
    if "pytest" in joined:
        runner = "pytest"
    elif "unittest" in joined:
        runner = "unittest"
    elif re.search(r"(?m)^Ran\s+\d+\s+tests?\b", text) or re.search(
        r"(?m)^(OK|FAILED)\b", text
    ):
        runner = "unittest"
    else:
        runner = "pytest"

    cfg = patterns.get(runner, {})
    if runner == "unittest":
        return _parse_unittest(text, cfg)
    return _parse_pytest(text, cfg)


# ---------------------------------------------------------------------------
# Evidence writing
# ---------------------------------------------------------------------------


def write_evidence_log(
    project_dir: str, command: List[str], full_output: str, counts: Dict[str, int]
) -> Optional[str]:
    """Write the full test output to ``.mythis/evidence/tests/<utc-ts>.log``.

    Returns the log path as a string, or ``None`` if the write failed.
    Never raises — the Forge reports a missing log, never crashes over one.
    """
    try:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        evidence_dir = Path(project_dir) / EVIDENCE_SUBDIR
        evidence_dir.mkdir(parents=True, exist_ok=True)
        path = evidence_dir / f"{ts}.log"
        header = (
            f"# Draupnir Forge test evidence — {ts} UTC\n"
            f"# command: {' '.join(command)}\n"
            f"# counts: {counts}\n"
            f"# {'-' * 60}\n"
        )
        path.write_text(header + (full_output or ""), encoding="utf-8")
        return str(path)
    except Exception as exc:
        log.warning("Could not write test evidence log: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Running the suite
# ---------------------------------------------------------------------------


def run_tests(project_dir: str, timeout: int = 600) -> TestResult:
    """Run the project's test suite and parse the outcome.

    Discovers the test command, runs it under ``timeout`` seconds, parses
    counts, classifies every failing/erroring test block with
    ``failures.classify_failure``, and writes the full output to the
    evidence log. NEVER modifies any test file.

    Args:
        project_dir: Root of the project under test.
        timeout: Seconds before the run is killed.

    Returns:
        A :class:`TestResult`. ``command`` is empty when no test command
        was discovered.
    """
    command = detect_command(project_dir)
    started = time.monotonic()

    if not command:
        log.warning("No test command found under %s", project_dir)
        return TestResult(
            passed=0, failed=0, errors=0, skipped=0,
            duration_s=0.0, raw_output="", failures=[], command=[],
        )

    full_output = ""
    try:
        proc = subprocess.run(
            command,
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        full_output = stdout + ("\n" if stdout and stderr else "") + stderr
        if proc.returncode != 0 and not full_output.strip():
            full_output = (
                f"(test command exited with code {proc.returncode} "
                "and produced no output)"
            )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        full_output = (
            stdout + ("\n" if stdout and stderr else "") + stderr
            + f"\n[TIMEOUT: test run killed after {timeout}s]"
        )
        log.warning("Test run timed out after %ss", timeout)
    except Exception as exc:
        full_output = f"(could not run tests: {exc})"
        log.warning("Test run failed to start: %s", exc)

    duration_s = time.monotonic() - started

    try:
        counts, failure_blocks, error_blocks = parse_test_output(full_output, command)
    except Exception as exc:
        log.warning("Test output parsing failed: %s", exc)
        counts, failure_blocks, error_blocks = (
            {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}, [], [],
        )

    failure_classes: List[FailureClass] = []
    for block in failure_blocks + error_blocks:
        try:
            failure_classes.append(classify_failure(test_output=str(block)))
        except Exception as exc:
            log.warning("Failure classification failed: %s", exc)

    log_path = write_evidence_log(project_dir, command, full_output, counts)
    if log_path:
        log.info("Test evidence written to %s", log_path)

    return TestResult(
        passed=counts.get("passed", 0),
        failed=counts.get("failed", 0),
        errors=counts.get("errors", 0),
        skipped=counts.get("skipped", 0),
        duration_s=duration_s,
        raw_output=full_output[-RAW_OUTPUT_TAIL_CHARS:],
        failures=failure_classes,
        command=command,
    )


# ---------------------------------------------------------------------------
# The Role itself
# ---------------------------------------------------------------------------


@register_role
class Tester(Role):
    """Runs the test suite and reports what the witnesses saw."""

    name = "tester"
    purpose = (
        "Discover and run the project's test suite, parse results, "
        "classify failures, and preserve the evidence. Never edits tests."
    )

    def run(self, ctx: RoleContext) -> RoleResult:
        try:
            result = run_tests(ctx.project_dir)
        except Exception as exc:
            # The contract says: never raise on bad input.
            return RoleResult(ok=False, summary=f"Tester failed: {exc}")

        ok = result.failed == 0 and result.errors == 0
        summary = (
            f"{result.passed} passed, {result.failed} failed, "
            f"{result.errors} errors, {result.skipped} skipped "
            f"in {result.duration_s:.1f}s"
        )
        if not result.command:
            summary = "no test command discovered; " + summary
            self.emit(
                ctx,
                EventType.TEST_FAILED,
                {"reason": "no test command discovered", "project": ctx.project_dir},
            )
        else:
            self.emit(
                ctx,
                EventType.TEST_PASSED if ok else EventType.TEST_FAILED,
                {
                    "passed": result.passed,
                    "failed": result.failed,
                    "errors": result.errors,
                    "skipped": result.skipped,
                    "failure_classes": [fc.value for fc in result.failures],
                },
            )

        return RoleResult(
            ok=ok,
            summary=summary,
            artifacts={"test_result": result},
        )
