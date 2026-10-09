"""Slice 7 — Failure classification.

Every failed hammer blow should leave knowledge, not just noise (Forge Law
"Failure should produce knowledge, not blind repetition"). This module gives
the Forge a shared vocabulary for *why* something failed so the Orchestrator
can decide whether to retry, reroute to another role, or ask the human.

The classification rules are DATA, not code: ``_RULE_TABLE`` holds
(regex-pattern, class) pairs scanned in order, and ``_EXCEPTION_TYPE_MAP``
maps exception class names to classes. To teach the Forge a new failure
signature, add a row — the matching logic below does not change.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum


class FailureClass(Enum):
    """The fourteen failure classes (§16 of the Forge spec).

    Each names a distinct *kind* of breakdown so the retry/escalation
    machinery can react to the cause rather than the symptom.
    """

    IMPLEMENTATION_ERROR = "implementation_error"
    TEST_FAILURE = "test_failure"
    ARCHITECTURE_MISMATCH = "architecture_mismatch"
    CONTEXT_MISSING = "context_missing"
    FALSE_ASSUMPTION = "false_assumption"
    DEPENDENCY_FAILURE = "dependency_failure"
    ENVIRONMENT_FAILURE = "environment_failure"
    MODEL_LIMITATION = "model_limitation"
    TASK_TOO_LARGE = "task_too_large"
    AMBIGUOUS_REQUIREMENT = "ambiguous_requirement"
    USER_DECISION_REQUIRED = "user_decision_required"
    RESOURCE_LIMIT = "resource_limit"
    SECURITY_BLOCK = "security_block"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Rule tables (DATA). First matching row wins.
# ---------------------------------------------------------------------------

# (regex pattern, failure class) pairs. The haystack is the exception text
# plus its class name, the test output, and any surrounding context, so one
# table covers signals from all three sources.
#
# Row with FailureClass.TEST_FAILURE only fires when test_output is non-empty
# (an assert/Traceback mentioned in prose with no test run behind it is an
# implementation error, not a test failure). The row directly below it is the
# same pattern falling back to IMPLEMENTATION_ERROR for that case.
_RULE_TABLE: list[tuple[str, FailureClass]] = [
    (
        r"ModuleNotFoundError|ImportError|pip|requirements|No module named",
        FailureClass.DEPENDENCY_FAILURE,
    ),
    (
        r"permission denied|AuthorityDenied|access denied|AccessDenied",
        FailureClass.SECURITY_BLOCK,
    ),
    (
        r"out of memory|OutOfMemoryError|CUDA out of memory|CUDA",
        FailureClass.RESOURCE_LIMIT,
    ),
    (
        r"context missing|missing context|file not found|FileNotFoundError|no such file",
        FailureClass.CONTEXT_MISSING,
    ),
    (
        r"architecture mismatch|incompatible (with|interface)|interface mismatch|does not fit the architecture",
        FailureClass.ARCHITECTURE_MISMATCH,
    ),
    (
        r"false assumption|assumption (was |is )?wrong|incorrectly assumed|assumed incorrectly",
        FailureClass.FALSE_ASSUMPTION,
    ),
    (
        r"model limitation|context window exceeded|token limit|beyond (my|its) capabilities",
        FailureClass.MODEL_LIMITATION,
    ),
    (
        r"task too large|too complex|exceeds step limit|too many steps",
        FailureClass.TASK_TOO_LARGE,
    ),
    (
        r"ambiguous|unclear requirement|vague requirement",
        FailureClass.AMBIGUOUS_REQUIREMENT,
    ),
    (
        r"user decision|human decision|needs approval|approval required|ask the (user|human)",
        FailureClass.USER_DECISION_REQUIRED,
    ),
    (
        r"syntax error|SyntaxError|IndentationError",
        FailureClass.IMPLEMENTATION_ERROR,
    ),
    (
        r"timeout|timed out|TimeoutError|deadline exceeded",
        FailureClass.ENVIRONMENT_FAILURE,
    ),
    (
        r"assert|FAILED|Traceback \(most recent call last\)|Traceback",
        FailureClass.TEST_FAILURE,
    ),
    (
        r"assert|FAILED|Traceback \(most recent call last\)|Traceback",
        FailureClass.IMPLEMENTATION_ERROR,
    ),
]

# Exception class name -> failure class, consulted after the regex table.
# Covers exceptions whose str() carries no useful signal of their own.
_EXCEPTION_TYPE_MAP: dict[str, FailureClass] = {
    "ModuleNotFoundError": FailureClass.DEPENDENCY_FAILURE,
    "ImportError": FailureClass.DEPENDENCY_FAILURE,
    "SyntaxError": FailureClass.IMPLEMENTATION_ERROR,
    "IndentationError": FailureClass.IMPLEMENTATION_ERROR,
    "TypeError": FailureClass.IMPLEMENTATION_ERROR,
    "ValueError": FailureClass.IMPLEMENTATION_ERROR,
    "AttributeError": FailureClass.IMPLEMENTATION_ERROR,
    "NameError": FailureClass.IMPLEMENTATION_ERROR,
    "KeyError": FailureClass.IMPLEMENTATION_ERROR,
    "IndexError": FailureClass.IMPLEMENTATION_ERROR,
    "AssertionError": FailureClass.TEST_FAILURE,
    "TimeoutError": FailureClass.ENVIRONMENT_FAILURE,
    "PermissionError": FailureClass.SECURITY_BLOCK,
    "MemoryError": FailureClass.RESOURCE_LIMIT,
    "FileNotFoundError": FailureClass.CONTEXT_MISSING,
    "ConnectionError": FailureClass.ENVIRONMENT_FAILURE,
}


def _compile_rules() -> list[tuple[re.Pattern[str], FailureClass]]:
    """Compile the module-level rule table once per call set.

    Kept as a helper so ``classify_failure`` reads as: build haystack,
    scan table, consult type map, fall back. Compilation is cheap enough
    here that caching buys nothing measurable.
    """
    return [(re.compile(pattern, re.IGNORECASE), cls) for pattern, cls in _RULE_TABLE]


def classify_failure(
    exc: BaseException | None = None,
    test_output: str = "",
    context: str = "",
) -> FailureClass:
    """Classify a failure from its evidence.

    Args:
        exc: The exception raised, if any.
        test_output: Captured output of a test run, if any.
        context: Any surrounding prose (logs, model notes, operator remarks).

    Returns:
        The matching ``FailureClass``. ``UNKNOWN`` is returned only when no
        evidence at all was supplied; unrecognized evidence with a real
        exception defaults to ``IMPLEMENTATION_ERROR``.
    """
    exc_text = "" if exc is None else f"{type(exc).__name__}: {exc}"
    parts = [exc_text, test_output or "", context or ""]
    haystack = " ".join(part for part in parts if part).strip()

    if not haystack:
        return FailureClass.UNKNOWN

    has_test_output = bool((test_output or "").strip())
    for pattern, cls in _compile_rules():
        if not pattern.search(haystack):
            continue
        # A test-failure row needs an actual test run behind it; otherwise
        # the same pattern falls through to the IMPLEMENTATION_ERROR row.
        if cls is FailureClass.TEST_FAILURE and not has_test_output:
            continue
        return cls

    if exc is not None:
        mapped = _EXCEPTION_TYPE_MAP.get(type(exc).__name__)
        if mapped is FailureClass.TEST_FAILURE and not has_test_output:
            return FailureClass.IMPLEMENTATION_ERROR
        if mapped is not None:
            return mapped
        return FailureClass.IMPLEMENTATION_ERROR

    return FailureClass.IMPLEMENTATION_ERROR


@dataclass
class FailureRecord:
    """One witnessed failure: what broke, where, when, on which attempt.

    Note: the field is ``failure_class`` rather than ``class`` because
    ``class`` is a reserved word in Python.
    """

    failure_class: FailureClass
    task_id: str = ""
    ts: float = field(default_factory=time.time)
    detail: str = ""
    attempt: int = 1


# Escalation ladder: how many times the same task has failed with the same
# class -> who should take the next look. Ordered so the first threshold met
# wins; 0-1 occurrences need no escalation.
_ESCALATION_LADDER: tuple[tuple[int, str], ...] = (
    (4, "human"),
    (3, "architect"),
    (2, "auditor"),
)


def escalation_for(history: list[FailureRecord]) -> str:
    """Decide escalation from a failure history.

    Looks at the most recent record and counts how many records share its
    task_id *and* failure class. Repeated identical failures signal a blind
    spot: 2x -> "auditor", 3x -> "architect", 4x+ -> "human". Anything less
    returns "none".

    Args:
        history: Chronological (or any-order) list of failure records.

    Returns:
        One of "none", "auditor", "architect", "human".
    """
    if not history:
        return "none"
    latest = history[-1]
    same = sum(
        1
        for record in history
        if record.task_id == latest.task_id
        and record.failure_class is latest.failure_class
    )
    for threshold, target in _ESCALATION_LADDER:
        if same >= threshold:
            return target
    return "none"
