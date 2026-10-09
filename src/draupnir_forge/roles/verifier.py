"""Slice 17 — Verifier role: the acceptance judge of the Forge.

After the Tester has witnessed the run, the Verifier holds the
implementation against the eight gates of §17: code, build, test,
interface, invariant, runtime, goal, and documentation. Each gate
returns pass, fail, or skip — missing evidence means *skipped*, never
punished, except the goal gate: acceptance criteria with no evidence
text is a failure, because a promise with no witness is no promise.

The v1 goal gate checks keyword presence of each acceptance criterion
in the implementation's evidence text.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from draupnir_forge.roles.base import Role, RoleContext, RoleResult, register_role

log = logging.getLogger("draupnir_forge.roles.verifier")


# ---------------------------------------------------------------------------
# Verdict model
# ---------------------------------------------------------------------------


@dataclass
class GateResult:
    """The outcome of one acceptance gate."""

    gate: str          # one of: code, build, test, interface, invariant,
                       # runtime, goal, documentation
    passed: bool
    evidence: str      # human-readable account of what was checked
    skipped: bool = False


@dataclass
class Verdict:
    """The judge's ruling over all eight gates."""

    gates: List[GateResult]
    summary: str
    passed: bool = False  # recomputed in __post_init__

    def __post_init__(self) -> None:
        evaluated = [g for g in self.gates if not g.skipped]
        self.passed = bool(evaluated) and all(g.passed for g in evaluated)

    def by_name(self, gate: str) -> Optional[GateResult]:
        """Fetch one gate's result by name (None if absent)."""
        for result in self.gates:
            if result.gate == gate:
                return result
        return None


# ---------------------------------------------------------------------------
# Helpers for reading the implementation record
# ---------------------------------------------------------------------------


def _as_dict(value: Any) -> Dict[str, Any]:
    """Coerce the implementation record to a dict (never raises)."""
    if isinstance(value, dict):
        return value
    get = getattr(value, "__dict__", None)
    if isinstance(get, dict):
        return dict(get)
    return {}


def _acceptance_criteria(task: Any) -> List[str]:
    """Pull acceptance criteria from a ForgeTask (or dict-shaped task)."""
    if task is None:
        return []
    if isinstance(task, dict):
        raw = task.get("acceptance") or task.get("acceptance_criteria") or []
    else:
        raw = getattr(task, "acceptance", None)
        if raw is None:
            raw = getattr(task, "acceptance_criteria", None)
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, Sequence):
        return [str(item) for item in raw if str(item).strip()]
    return []


def _test_counts(test_result: Any) -> Optional[Dict[str, int]]:
    """Extract failed/errors counts from a TestResult or dict (None if absent)."""
    if test_result is None:
        return None
    if isinstance(test_result, dict):
        if "failed" in test_result or "errors" in test_result:
            return {
                "failed": int(test_result.get("failed", 0) or 0),
                "errors": int(test_result.get("errors", 0) or 0),
                "passed": int(test_result.get("passed", 0) or 0),
                "skipped": int(test_result.get("skipped", 0) or 0),
            }
        return None
    failed = getattr(test_result, "failed", None)
    errors = getattr(test_result, "errors", None)
    if failed is None and errors is None:
        return None
    return {
        "failed": int(failed or 0),
        "errors": int(errors or 0),
        "passed": int(getattr(test_result, "passed", 0) or 0),
        "skipped": int(getattr(test_result, "skipped", 0) or 0),
    }


def _project_base(impl: Dict[str, Any]) -> Path:
    """Directory that changed_files paths are relative to."""
    base = impl.get("project_dir") or os.getcwd()
    try:
        return Path(base)
    except Exception:
        return Path(os.getcwd())


# ---------------------------------------------------------------------------
# The eight gates
# ---------------------------------------------------------------------------


def _gate_code(impl: Dict[str, Any]) -> GateResult:
    """Gate 1 — code: every expected changed file exists."""
    changed = impl.get("changed_files")
    if changed is None:
        return GateResult("code", False, "no changed_files recorded", skipped=True)
    if not isinstance(changed, (list, tuple)) or not changed:
        return GateResult("code", False, "changed_files is empty", skipped=True)
    base = _project_base(impl)
    missing = [f for f in changed if not (base / str(f)).exists()]
    if missing:
        return GateResult(
            "code", False,
            f"{len(missing)} expected file(s) missing: {', '.join(map(str, missing))}",
        )
    return GateResult(
        "code", True,
        f"all {len(changed)} changed file(s) exist under {base}",
    )


def _gate_build(impl: Dict[str, Any]) -> GateResult:
    """Gate 2 — build: the recorded build flag."""
    if "build_ok" not in impl:
        return GateResult("build", False, "no build_ok recorded", skipped=True)
    ok = bool(impl.get("build_ok"))
    return GateResult(
        "build", ok,
        "build reported ok" if ok else "build reported failure",
    )


def _gate_test(test_result: Any) -> GateResult:
    """Gate 3 — test: the suite witnessed no failures or errors."""
    counts = _test_counts(test_result)
    if counts is None:
        return GateResult("test", False, "no test result available", skipped=True)
    ok = counts["failed"] == 0 and counts["errors"] == 0
    return GateResult(
        "test", ok,
        f"{counts['passed']} passed, {counts['failed']} failed, "
        f"{counts['errors']} errors, {counts['skipped']} skipped",
    )


def _gate_interface(impl: Dict[str, Any]) -> GateResult:
    """Gate 4 — interface: the public API is unchanged (v1: honor the flag)."""
    if "api_unchanged" not in impl:
        return GateResult("interface", False, "no api_unchanged recorded", skipped=True)
    ok = bool(impl.get("api_unchanged"))
    return GateResult(
        "interface", ok,
        "public API unchanged" if ok else "public API changed without approval",
    )


def _gate_invariant(impl: Dict[str, Any]) -> GateResult:
    """Gate 5 — invariant: no invariant violations were recorded."""
    if "invariant_violations" not in impl:
        return GateResult(
            "invariant", False, "no invariant_violations recorded", skipped=True
        )
    violations = impl.get("invariant_violations") or []
    ok = len(violations) == 0
    return GateResult(
        "invariant", ok,
        "no invariant violations" if ok else f"violations: {violations}",
    )


def _gate_runtime(impl: Dict[str, Any]) -> GateResult:
    """Gate 6 — runtime: the implementation left runtime evidence."""
    if "runtime_evidence" not in impl:
        return GateResult(
            "runtime", False, "no runtime_evidence recorded", skipped=True
        )
    evidence = impl.get("runtime_evidence")
    ok = bool(evidence) and bool(str(evidence).strip())
    return GateResult(
        "runtime", ok,
        "runtime evidence present" if ok else "runtime_evidence is empty",
    )


def _gate_goal(task: Any, impl: Dict[str, Any]) -> GateResult:
    """Gate 7 — goal: each acceptance criterion appears in the evidence text.

    v1 keyword presence: a criterion string must occur in
    ``impl['evidence_text']``. Acceptance criteria with no evidence text
    at all is a failure (not a skip) — the goal was sworn but never shown.
    """
    criteria = _acceptance_criteria(task)
    if not criteria:
        return GateResult(
            "goal", False, "no acceptance criteria to judge", skipped=True
        )
    evidence_text = str(impl.get("evidence_text") or "")
    if not evidence_text.strip():
        return GateResult(
            "goal", False,
            "acceptance criteria exist but no evidence text was recorded",
        )
    missing = [c for c in criteria if c not in evidence_text]
    if missing:
        return GateResult(
            "goal", False,
            f"{len(missing)}/{len(criteria)} criteria not evidenced: "
            + "; ".join(missing[:5]),
        )
    return GateResult(
        "goal", True,
        f"all {len(criteria)} acceptance criteria found in evidence text",
    )


def _gate_documentation(impl: Dict[str, Any]) -> GateResult:
    """Gate 8 — documentation: docs were updated (v1: honor the flag)."""
    if "docs_updated" not in impl:
        return GateResult(
            "documentation", False, "no docs_updated recorded", skipped=True
        )
    ok = bool(impl.get("docs_updated"))
    return GateResult(
        "documentation", ok,
        "docs updated" if ok else "docs not updated",
    )


# ---------------------------------------------------------------------------
# evaluate()
# ---------------------------------------------------------------------------


def evaluate(task: Any, impl: Any, test_result: Any) -> Verdict:
    """Judge an implementation against the eight gates of §17.

    Args:
        task: The ForgeTask (or dict) with acceptance criteria.
        impl: The implementation record — a dict honoring the keys
            ``changed_files``, ``project_dir``, ``build_ok``,
            ``api_unchanged``, ``invariant_violations``,
            ``runtime_evidence``, ``evidence_text``, ``docs_updated``.
            Missing keys make their gate *skip*, not fail.
        test_result: A TestResult (or dict with failed/errors counts);
            ``None`` skips the test gate.

    Returns:
        A :class:`Verdict` — ``passed`` is True only when every
        non-skipped gate passed.
    """
    record = _as_dict(impl)
    gates = [
        _gate_code(record),
        _gate_build(record),
        _gate_test(test_result),
        _gate_interface(record),
        _gate_invariant(record),
        _gate_runtime(record),
        _gate_goal(task, record),
        _gate_documentation(record),
    ]
    evaluated = [g for g in gates if not g.skipped]
    failed = [g.gate for g in evaluated if not g.passed]
    if failed:
        summary = (
            f"{len(evaluated) - len(failed)}/{len(evaluated)} gates passed; "
            f"failing: {', '.join(failed)}"
        )
    else:
        summary = (
            f"all {len(evaluated)} evaluated gates passed "
            f"({len(gates) - len(evaluated)} skipped)"
        )
    return Verdict(gates=gates, summary=summary)


# ---------------------------------------------------------------------------
# The Role itself
# ---------------------------------------------------------------------------


@register_role
class Verifier(Role):
    """Judges implementations against the eight gates of acceptance."""

    name = "verifier"
    purpose = (
        "Evaluate a task's implementation through the 8-gate checklist "
        "(code/build/test/interface/invariant/runtime/goal/documentation) "
        "and rule whether the task may complete."
    )

    def run(self, ctx: RoleContext) -> RoleResult:
        try:
            impl = _as_dict(ctx.artifacts.get("implementation"))
            if "project_dir" not in impl:
                impl["project_dir"] = ctx.project_dir
            test_result = ctx.artifacts.get("test_result")
            verdict = evaluate(ctx.task, impl, test_result)
        except Exception as exc:
            # The contract says: never raise on bad input.
            return RoleResult(ok=False, summary=f"Verifier failed: {exc}")

        return RoleResult(
            ok=verdict.passed,
            summary=verdict.summary,
            artifacts={"verdict": verdict},
        )
