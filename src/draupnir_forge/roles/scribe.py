"""Slice 18a — Scribe role: keeper of the Forge's canonical memory.

The Scribe owns the ``.mythis/`` canonical docs. Every write is
append-only — history is never rewritten, only extended. If a document
does not exist yet, it is created with a header first.

Documents:
  - DECISIONS.md        — dated decision entries (decision, reason,
                          alternatives, evidence, consequences, role)
  - ROADMAP.md          — task status lines, updated in place by task id
                          (check-box marker flips), appended if the task
                          is not yet listed
  - KNOWN_ISSUES.md     — appended issue notes
  - CAPABILITY_LEDGER.md — appended capability notes

``run()`` processes ``ctx.artifacts["scribe_ops"]``: a list of op dicts
like ``{"op": "decision", "decision": ..., "reason": ..., ...}``.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence

from draupnir_forge.roles.base import Role, RoleContext, RoleResult, register_role

log = logging.getLogger("draupnir_forge.roles.scribe")

MYTHIS_DIR = ".mythis"
DECISIONS_FILE = "DECISIONS.md"
ROADMAP_FILE = "ROADMAP.md"
KNOWN_ISSUES_FILE = "KNOWN_ISSUES.md"
CAPABILITY_LEDGER_FILE = "CAPABILITY_LEDGER.md"

_HEADERS: Dict[str, str] = {
    DECISIONS_FILE: "# Decisions\n\n",
    ROADMAP_FILE: "# Roadmap\n\n",
    KNOWN_ISSUES_FILE: "# Known Issues\n\n",
    CAPABILITY_LEDGER_FILE: "# Capability Ledger\n\n",
}

# Status word -> roadmap check-box marker.
_STATUS_MARKERS: Dict[str, str] = {
    "done": "x",
    "complete": "x",
    "completed": "x",
    "in-progress": "~",
    "in_progress": "~",
    "failed": "!",
    "blocked": "!",
    "pending": " ",
}


def _utc_now() -> str:
    """Current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _doc_path(project_dir: str, filename: str) -> Path:
    return Path(project_dir) / MYTHIS_DIR / filename


def _append_entry(project_dir: str, filename: str, entry: str) -> Path:
    """Append an entry to a canonical doc, creating it with a header if missing.

    Returns the document path. Never raises — the caller gets the error
    as a log line and the operation reports failure in its own way.
    """
    path = _doc_path(project_dir, filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.stat().st_size == 0:
        header = _HEADERS.get(filename, "# " + filename + "\n\n")
        path.write_text(header, encoding="utf-8")
    with open(path, "a", encoding="utf-8") as handle:
        if not entry.endswith("\n"):
            entry += "\n"
        handle.write(entry)
    return path


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# The four canonical operations
# ---------------------------------------------------------------------------


def record_decision(
    project_dir: str,
    decision: str,
    reason: str,
    alternatives: Sequence[str],
    evidence: str,
    consequences: str,
    role: str,
) -> Path:
    """Append a dated decision entry to ``.mythis/DECISIONS.md``."""
    if alternatives:
        alt_text = "; ".join(str(a) for a in alternatives)
    else:
        alt_text = "none recorded"
    entry = (
        f"## {_utc_now()} — {decision}\n"
        f"- Role: {role}\n"
        f"- Reason: {reason}\n"
        f"- Alternatives considered: {alt_text}\n"
        f"- Evidence: {evidence}\n"
        f"- Consequences: {consequences}\n"
        "\n"
    )
    path = _append_entry(project_dir, DECISIONS_FILE, entry)
    log.info("Decision recorded in %s", path)
    return path


def update_roadmap_status(project_dir: str, task_id: str, status: str) -> Path:
    """Update the roadmap task line for ``task_id``.

    Flips the check-box marker of the first line mentioning the task id.
    If the task is not listed yet, a line is appended. Returns the
    roadmap path.
    """
    path = _doc_path(project_dir, ROADMAP_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    marker = _STATUS_MARKERS.get(str(status).strip().lower(), " ")
    task_id = str(task_id)

    text = _read_text(path)
    if not text:
        text = _HEADERS[ROADMAP_FILE]
    lines = text.splitlines(keepends=True)
    updated = False
    for i, line in enumerate(lines):
        if task_id in line:
            # Flip (or insert) the check-box marker at the line start.
            check_pat = r"^(\s*-\s*)\[[ x~!-]\]"
            new_line, n = re.subn(check_pat, rf"\1[{marker}]", line, count=1)
            if n == 0:
                new_line, n = re.subn(r"^(\s*-\s*)", rf"\1[{marker}] ", line, count=1)
            if n:
                lines[i] = new_line
                updated = True
                break
    if not updated:
        stamp = _utc_now()
        lines.append(f"- [{marker}] {task_id}: {status} (updated {stamp})\n")
    path.write_text("".join(lines), encoding="utf-8")
    log.info("Roadmap status for %s set to %s", task_id, status)
    return path


def note_issue(project_dir: str, issue: str) -> Path:
    """Append an issue note to ``.mythis/KNOWN_ISSUES.md``."""
    entry = f"- {_utc_now()}: {issue}\n"
    path = _append_entry(project_dir, KNOWN_ISSUES_FILE, entry)
    log.info("Issue noted in %s", path)
    return path


def log_capability(project_dir: str, cap: str) -> Path:
    """Append a capability note to ``.mythis/CAPABILITY_LEDGER.md``."""
    entry = f"- {_utc_now()}: {cap}\n"
    path = _append_entry(project_dir, CAPABILITY_LEDGER_FILE, entry)
    log.info("Capability logged in %s", path)
    return path


# ---------------------------------------------------------------------------
# Op dispatch for run()
# ---------------------------------------------------------------------------


def apply_op(project_dir: str, op: Dict[str, Any]) -> str:
    """Apply one scribe op dict. Returns a human-readable outcome line."""
    kind = str(op.get("op", "")).strip().lower()
    if kind == "decision":
        path = record_decision(
            project_dir,
            decision=str(op.get("decision", "")),
            reason=str(op.get("reason", "")),
            alternatives=op.get("alternatives", []) or [],
            evidence=str(op.get("evidence", "")),
            consequences=str(op.get("consequences", "")),
            role=str(op.get("role", "scribe")),
        )
        return f"decision recorded -> {path}"
    if kind == "roadmap":
        path = update_roadmap_status(
            project_dir,
            task_id=str(op.get("task_id", "")),
            status=str(op.get("status", "pending")),
        )
        return f"roadmap {op.get('task_id')} -> {op.get('status')} ({path})"
    if kind == "issue":
        path = note_issue(project_dir, str(op.get("issue", "")))
        return f"issue noted -> {path}"
    if kind == "capability":
        path = log_capability(project_dir, str(op.get("capability", "")))
        return f"capability logged -> {path}"
    raise ValueError(f"unknown scribe op: {kind!r}")


# ---------------------------------------------------------------------------
# The Role itself
# ---------------------------------------------------------------------------


@register_role
class Scribe(Role):
    """Keeps the canonical .mythis/ docs; writes only in append."""

    name = "scribe"
    purpose = (
        "Own the .mythis/ canonical documents: record decisions, update "
        "roadmap status, note issues, and log capabilities — append-only."
    )

    def run(self, ctx: RoleContext) -> RoleResult:
        ops: List[Dict[str, Any]] = ctx.artifacts.get("scribe_ops") or []
        outcomes: List[str] = []
        failures: List[str] = []
        for op in ops:
            try:
                if not isinstance(op, dict):
                    got = type(op).__name__
                    raise ValueError(f"scribe op must be a dict, got {got}")
                outcomes.append(apply_op(ctx.project_dir, op))
            except Exception as exc:
                failures.append(f"{op!r}: {exc}")
                log.warning("Scribe op failed: %s", exc)

        ok = not failures
        summary = (
            f"{len(outcomes)} scribe op(s) applied"
            + (f"; {len(failures)} failed: {'; '.join(failures)}" if failures else "")
            if ops else "no scribe ops to apply"
        )
        return RoleResult(
            ok=ok,
            summary=summary,
            artifacts={"scribe_outcomes": outcomes},
        )
