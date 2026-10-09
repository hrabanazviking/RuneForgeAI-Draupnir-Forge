"""escalation.py — Human escalation engine (slice 27).

The Forge aggressively avoids unnecessary permission loops. This module
implements DRAUPNIR_FORGE_SPEC.md §13: ask the human only when one of the
eight ask-conditions holds, never for routine next steps.

Public surface:
    EscalationPolicy.needs_human(task, failure_history, context)
        -> (bool, reason)
    request_escalation(project_dir, question, context) -> path written
    answer_escalation(project_dir, answer) -> {"answer":..., "question":...}
    has_pending(project_dir) -> bool
    Escalation — persisted record of one escalation round-trip.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml  # pyyaml is a declared dependency
except Exception:  # pragma: no cover - defensive; pyyaml ships with the forge
    yaml = None  # type: ignore

from draupnir_forge import _paths

_LOG = logging.getLogger(__name__)

# The file the human reads and the Forge watches for, per slice design.
AWAITING_FILE = "awaiting_human.md"
# Append-only ledger of every escalation round-trip.
ESCALATIONS_LOG = "escalations.jsonl"

# Same-class failures reaching this count force escalation (§13:
# "repeated autonomous attempts cannot resolve a blocker").
REPEATED_FAILURE_THRESHOLD = 4


# ---------------------------------------------------------------------------
# Negative list (data-driven: data/no_ask_needed.yaml)
# ---------------------------------------------------------------------------

def _data_path(name: str) -> str:
    """Locate a data file next to the installed package or the repo."""
    try:
        found = _paths.find_data_file(name)
    except Exception:  # pragma: no cover - defensive fallback
        found = None
    if found is not None:
        return str(found)
    # Last resort: repo-relative data dir (load_negative_list logs a
    # warning when the file still cannot be read).
    return os.path.join(os.path.dirname(__file__), "..", "..", "data", name)


def load_negative_list() -> List[Dict[str, Any]]:
    """Load the never-ask list from data/no_ask_needed.yaml.

    Returns an empty list (with a warning) rather than crashing when the
    data file is missing or unreadable — the Forge must not fall over
    because a data file went missing.
    """
    path = _data_path("no_ask_needed.yaml")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) if yaml else {}
    except OSError as exc:
        _LOG.warning("escalation: cannot read %s: %s", path, exc)
        return []
    except Exception as exc:  # malformed YAML
        _LOG.warning("escalation: cannot parse %s: %s", path, exc)
        return []
    entries = (data or {}).get("negative_list", [])
    return [e for e in entries if isinstance(e, dict)]


def _task_text(task: Any) -> str:
    """Render a task (ForgeTask, dict, or bare string) as plain text."""
    if task is None:
        return ""
    if isinstance(task, str):
        return task
    if isinstance(task, dict):
        parts = [str(task.get("title", "")), str(task.get("goal", ""))]
        return " ".join(p for p in parts if p)
    # ForgeTask or any object with title/goal attributes.
    title = getattr(task, "title", "") or ""
    goal = getattr(task, "goal", "") or ""
    return f"{title} {goal}".strip()


def is_routine(task: Any) -> Optional[str]:
    """Return the negative-list entry name if ``task`` is routine.

    Returns ``None`` when the task is not covered by the never-ask list.
    """
    text = _task_text(task).lower()
    if not text.strip():
        return None
    for entry in load_negative_list():
        name = str(entry.get("name", "routine"))
        for pattern in entry.get("patterns", []) or []:
            if str(pattern).lower() in text:
                return name
    return None


# ---------------------------------------------------------------------------
# Ask-conditions (§13), expressed as data-driven predicates.
# ---------------------------------------------------------------------------

def _failure_classes(failure_history: List[Any]) -> List[str]:
    """Extract failure-class names from a history of records or dicts."""
    classes: List[str] = []
    for record in failure_history or []:
        cls = None
        if isinstance(record, dict):
            cls = record.get("failure_class", record.get("class"))
        else:
            cls = getattr(record, "failure_class", None)
        if cls is None:
            continue
        # FailureClass is an Enum; use the human name.
        name = getattr(cls, "name", None) or str(cls)
        classes.append(str(name))
    return classes


def _cond_ambiguous_directions(context: Dict[str, Any]) -> bool:
    return int(context.get("ambiguous_directions", 0) or 0) >= 2


def _cond_conflicts_requirements(context: Dict[str, Any]) -> bool:
    conflicts = (context.get("requirements_conflict")
                 or context.get("conflicting_requirements") or [])
    return bool(conflicts)


def _cond_destructive(context: Dict[str, Any]) -> bool:
    return bool(context.get("destructive")) and not bool(
        context.get("destructive_authorized"))


def _cond_external_consequences(context: Dict[str, Any]) -> bool:
    return bool(context.get("external_consequences"))


def _cond_missing_credentials(context: Dict[str, Any]) -> bool:
    return bool(context.get("missing_credentials"))


def _cond_goal_ambiguous(context: Dict[str, Any]) -> bool:
    return bool(context.get("goal_ambiguous"))


def _cond_breaks_user_decision(context: Dict[str, Any]) -> bool:
    return bool(context.get("breaks_user_decision"))


def _cond_repeated_failures(context: Dict[str, Any],
                            failure_history: List[Any]) -> bool:
    from collections import Counter
    counts = Counter(_failure_classes(failure_history))
    return any(n >= REPEATED_FAILURE_THRESHOLD for n in counts.values())


# The eight §13 ask-conditions as data: id, §13 summary, reason template,
# and the predicate. The order is the spec's order; the first hit wins.
ASK_CONDITIONS: List[Dict[str, Any]] = [
    {
        "id": "ambiguous_directions",
        "summary": "two or more materially different valid directions exist "
                   "and preference matters",
        "check": lambda ctx, hist: _cond_ambiguous_directions(ctx),
        "reason": ("two or more materially different valid directions exist "
                   "({n}); human preference decides"),
    },
    {
        "id": "conflicts_requirements",
        "summary": "the requested behavior conflicts with an existing "
                   "explicit user requirement",
        "check": lambda ctx, hist: _cond_conflicts_requirements(ctx),
        "reason": ("requested behavior conflicts with explicit user "
                   "requirement(s): {conflicts}"),
    },
    {
        "id": "destructive",
        "summary": "a destructive action exceeds previously granted authority",
        "check": lambda ctx, hist: _cond_destructive(ctx),
        "reason": "destructive action exceeds previously granted authority",
    },
    {
        "id": "external_consequences",
        "summary": "legal, financial, security, privacy, or deployment "
                   "consequences require human authorization",
        "check": lambda ctx, hist: _cond_external_consequences(ctx),
        "reason": ("legal, financial, security, privacy, or deployment "
                   "consequences require human authorization"),
    },
    {
        "id": "missing_credentials",
        "summary": "required credentials or external access are unavailable",
        "check": lambda ctx, hist: _cond_missing_credentials(ctx),
        "reason": ("required credentials or external access unavailable: "
                   "{missing}"),
    },
    {
        "id": "goal_ambiguous",
        "summary": "the project goal itself is ambiguous",
        "check": lambda ctx, hist: _cond_goal_ambiguous(ctx),
        "reason": "the project goal itself is ambiguous",
    },
    {
        "id": "breaks_user_decision",
        "summary": "a major architectural change would invalidate an "
                   "explicit user decision",
        "check": lambda ctx, hist: _cond_breaks_user_decision(ctx),
        "reason": ("a major change would invalidate an explicit user "
                   "decision"),
    },
    {
        "id": "repeated_failures",
        "summary": "repeated autonomous attempts cannot resolve a blocker",
        "check": _cond_repeated_failures,
        "reason": ("{n} failures of class '{cls}' — repeated autonomous "
                   "attempts cannot resolve the blocker"),
    },
]


class EscalationPolicy:
    """Decides when the Forge must ask the human (§13)."""

    def needs_human(self, task: Any, failure_history: List[Any],
                    context: Optional[Dict[str, Any]] = None
                    ) -> Tuple[bool, str]:
        """Return (escalate?, reason).

        The negative list is consulted first: routine work returns
        ``(False, "routine: <entry>")`` and is never escalated. Then the
        eight §13 predicates run in spec order; the first hit wins.
        """
        context = dict(context or {})

        routine_name = is_routine(task)
        if routine_name is not None:
            return False, f"routine: {routine_name}"

        for condition in ASK_CONDITIONS:
            try:
                hit = bool(condition["check"](context, failure_history))
            except Exception as exc:  # a sick predicate never blocks work
                _LOG.warning("escalation predicate %s failed: %s",
                             condition["id"], exc)
                continue
            if hit:
                return True, self._reason(condition, context, failure_history)
        return False, "no escalation condition met"

    @staticmethod
    def _reason(condition: Dict[str, Any], context: Dict[str, Any],
                failure_history: List[Any]) -> str:
        """Fill the condition's reason template from live context."""
        cid = condition["id"]
        template = str(condition["reason"])
        try:
            if cid == "ambiguous_directions":
                return template.format(
                    n=int(context.get("ambiguous_directions", 0) or 0))
            if cid == "conflicts_requirements":
                conflicts = (context.get("requirements_conflict")
                             or context.get("conflicting_requirements") or [])
                return template.format(
                    conflicts=", ".join(map(str, conflicts)))
            if cid == "missing_credentials":
                missing = context.get("missing_credentials") or []
                return template.format(missing=", ".join(map(str, missing)))
            if cid == "repeated_failures":
                from collections import Counter
                counts = Counter(_failure_classes(failure_history))
                cls, n = max(counts.items(), key=lambda kv: kv[1])
                return template.format(n=n, cls=cls)
            return template
        except Exception as exc:  # pragma: no cover - defensive
            _LOG.warning("escalation reason render failed: %s", exc)
            return str(condition.get("summary", cid))


# ---------------------------------------------------------------------------
# Escalation record + pause/resume file protocol.
# ---------------------------------------------------------------------------

@dataclass
class Escalation:
    """One persisted escalation round-trip."""

    question: str
    context: Dict[str, Any] = field(default_factory=dict)
    asked_at: str = ""
    answer: Optional[str] = None
    answered_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Escalation":
        data = dict(data or {})
        return cls(
            question=str(data.get("question", "")),
            context=dict(data.get("context") or {}),
            asked_at=str(data.get("asked_at", "")),
            answer=data.get("answer"),
            answered_at=data.get("answered_at"),
        )


def _mythis_dir(project_dir: str) -> str:
    path = os.path.join(os.path.abspath(project_dir), ".mythis")
    os.makedirs(path, exist_ok=True)
    return path


def _awaiting_path(project_dir: str) -> str:
    return os.path.join(_mythis_dir(project_dir), AWAITING_FILE)


def has_pending(project_dir: str) -> bool:
    """True when a question is currently awaiting a human answer."""
    return os.path.isfile(_awaiting_path(project_dir))


def _render_context(context: Dict[str, Any]) -> str:
    if yaml is not None:
        try:
            return yaml.safe_dump(dict(context or {}),
                                  default_flow_style=False,
                                  allow_unicode=True)
        except Exception:
            pass
    return json.dumps(dict(context or {}), indent=2, default=str)


def request_escalation(project_dir: str, question: str,
                       context: Optional[Dict[str, Any]] = None) -> str:
    """Pause the Forge with a clear question file for the human.

    Writes ``.mythis/awaiting_human.md`` (question + context + timestamp)
    and appends an :class:`Escalation` record to ``escalations.jsonl``.
    Returns the path of the question file.
    """
    context = dict(context or {})
    asked_at = datetime.now(timezone.utc).isoformat()
    path = _awaiting_path(project_dir)
    body = (
        "# Awaiting Human Decision\n"
        "\n"
        f"Question: {question}\n"
        "\n"
        f"Asked (UTC): {asked_at}\n"
        "\n"
        "## Context\n"
        "\n"
        "```yaml\n"
        f"{_render_context(context)}"
        "```\n"
        "\n"
        "Reply with `draupnir answer --text \"...\"` (or remove this file\n"
        "and place the answer where the Forge expects it) to resume.\n"
    )
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)

    record = Escalation(question=question, context=context, asked_at=asked_at)
    log_path = os.path.join(_mythis_dir(project_dir), ESCALATIONS_LOG)
    try:
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), default=str) + "\n")
    except OSError as exc:
        _LOG.warning("escalation: cannot append %s: %s", log_path, exc)
    return path


def answer_escalation(project_dir: str, answer: str) -> Dict[str, str]:
    """Consume the pending question and resume.

    Reads ``.mythis/awaiting_human.md``, deletes it, stamps the ledger,
    and returns ``{"answer": ..., "question": ...}``.

    Raises:
        FileNotFoundError: when nothing is awaiting the human.
    """
    path = _awaiting_path(project_dir)
    if not os.path.isfile(path):
        raise FileNotFoundError(
            f"no pending escalation under {os.path.abspath(project_dir)}")
    question = ""
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("Question:"):
                question = line[len("Question:"):].strip()
                break
    os.remove(path)

    # Stamp the ledger: mark the newest unanswered record as answered.
    log_path = os.path.join(_mythis_dir(project_dir), ESCALATIONS_LOG)
    try:
        lines: List[str] = []
        if os.path.isfile(log_path):
            with open(log_path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()
        for idx in range(len(lines) - 1, -1, -1):
            try:
                record = json.loads(lines[idx])
            except ValueError:
                continue
            if record.get("answer") is None:
                record["answer"] = answer
                record["answered_at"] = datetime.now(timezone.utc).isoformat()
                lines[idx] = json.dumps(record, default=str) + "\n"
                break
        with open(log_path, "w", encoding="utf-8") as handle:
            handle.writelines(lines)
    except OSError as exc:
        _LOG.warning("escalation: cannot update %s: %s", log_path, exc)

    return {"answer": answer, "question": question}
