"""report.py — the saga's closing words: FINAL_REPORT.md (slice 49).

Spec §26: when a project reaches PROJECT_COMPLETE the Forge emits
``.mythis/FINAL_REPORT.md`` carrying:

- what was built (roadmap.json's completed tasks);
- architecture summary (an excerpt of .mythis/ARCHITECTURE.md);
- how to run it (from README if the project has one);
- verification performed (TEST_PASSED counts from the event ledger);
- remaining limitations (from .mythis/KNOWN_ISSUES.md);
- future roadmap ideas (roadmap.json's incomplete tasks);
- known risks (the Risks section of .mythis/DISCOVERY_REPORT.md).

:class:`FinalReport.generate` assembles the markdown; :meth:`write`
persists it to ``.mythis/FINAL_REPORT.md``. Every source is optional —
a missing one degrades its section gracefully instead of failing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

__all__ = ["FinalReport"]

_REPORT_FILENAME = "FINAL_REPORT.md"
# Caps keep a giant README from becoming a giant report; they prompt,
# not truncate blindly — the section still names the source file.
_MAX_EXCERPT_LINES = 60
_MAX_DOC_LINES = 40


def _read_text(path: Path) -> Optional[str]:
    """Read a text file; None when missing/unreadable/empty."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return text if text.strip() else None


def _excerpt(text: str, max_lines: int) -> str:
    """First ``max_lines`` lines of a document, honest about the cut."""
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return "\n".join(lines)
    kept = "\n".join(lines[:max_lines])
    return f"{kept}\n\n_(excerpt — full text in the source file)_"


def _task_title(task: Any) -> str:
    """Human-readable title for a roadmap task of unknown shape."""
    if isinstance(task, dict):
        for key in ("title", "name", "summary", "goal"):
            value = task.get(key)
            if value:
                return str(value)
        for key in ("task_id", "id", "slice"):
            value = task.get(key)
            if value:
                return f"task {value}"
        return "untitled task"
    return str(task)


def _task_id(task: Any) -> str:
    """Best-effort id for a roadmap task of unknown shape."""
    if isinstance(task, dict):
        for key in ("task_id", "id", "slice"):
            value = task.get(key)
            if value:
                return str(value)
    return ""


def _task_status(task: Any) -> str:
    """Lowercased status of a roadmap task; '' when unknown."""
    if isinstance(task, dict):
        return str(task.get("status", task.get("state", "")) or "").lower()
    return ""


_DONE_STATUSES = {"done", "completed", "complete", "finished", "verified", "passed"}


def _split_tasks(tasks: List[Any]) -> Tuple[List[Any], List[Any]]:
    """Split roadmap tasks into (completed, incomplete) by status."""
    completed = [t for t in tasks if _task_status(t) in _DONE_STATUSES]
    incomplete = [t for t in tasks if _task_status(t) not in _DONE_STATUSES]
    return completed, incomplete


def _extract_tasks(data: Any) -> List[Any]:
    """Pull a task list out of a roadmap document of unknown shape."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("tasks", "slices", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def _extract_risks_section(text: str) -> Optional[str]:
    """The Risks section of a discovery report (``## Risks`` heading).

    Returns the heading plus its body lines, or None when the document
    has no such section.
    """
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        stripped = line.strip().lower()
        if stripped in ("## risks", "## risk", "# risks", "# risk"):
            start = index
            break
    if start is None:
        return None
    body = [lines[start]]
    for line in lines[start + 1:]:
        if line.lstrip().startswith("#"):
            break
        body.append(line)
    return "\n".join(body).rstrip()


class FinalReport:
    """Assembles the §26 final report for one project.

    Args:
        project_dir: Project root. Reads ``.mythis/roadmap.json``,
            ``.mythis/ARCHITECTURE.md``, ``.mythis/KNOWN_ISSUES.md``,
            ``.mythis/DISCOVERY_REPORT.md``, ``.mythis/events.jsonl``,
            and the project's ``README.md`` (repo root first, then
            ``.mythis/``). Writes to ``.mythis/FINAL_REPORT.md``.
    """

    def __init__(self, project_dir: Union[str, Path]) -> None:
        self._project_dir = Path(project_dir)
        self._mythis = self._project_dir / ".mythis"

    # -- sources ------------------------------------------------------------
    def _roadmap_tasks(self) -> List[Any]:
        """Tasks from .mythis/roadmap.json; [] when unreadable."""
        text = _read_text(self._mythis / "roadmap.json")
        if text is None:
            return []
        try:
            return _extract_tasks(json.loads(text))
        except (json.JSONDecodeError, TypeError, ValueError):
            return []

    def _event_counts(self) -> Dict[str, int]:
        """Counts of verification-relevant event types from the ledger."""
        path = self._mythis / "events.jsonl"
        counts = {"TEST_PASSED": 0, "TEST_FAILED": 0, "TASK_COMPLETED": 0}
        text = _read_text(path)
        if text is None:
            return counts
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("type") in counts:
                counts[str(event["type"])] += 1
        return counts

    def _readme(self) -> Optional[str]:
        """Project README (root preferred, .mythis/ as fallback)."""
        for path in (
            self._project_dir / "README.md",
            self._mythis / "README.md",
        ):
            text = _read_text(path)
            if text is not None:
                return text
        return None

    # -- sections -------------------------------------------------------------
    def _built_section(self, completed: List[Any]) -> str:
        lines = ["## What was built", ""]
        if not completed:
            lines.append("_No completed roadmap tasks recorded._")
        else:
            for task in completed:
                task_id = _task_id(task)
                label = f" ({task_id})" if task_id else ""
                lines.append(f"- {_task_title(task)}{label}")
        return "\n".join(lines)

    def _architecture_section(self) -> str:
        lines = ["## Architecture summary", ""]
        text = _read_text(self._mythis / "ARCHITECTURE.md")
        if text is None:
            lines.append("_No ARCHITECTURE.md recorded for this project._")
        else:
            lines.append(_excerpt(text, _MAX_EXCERPT_LINES))
            lines.append("")
            lines.append("_(excerpted from .mythis/ARCHITECTURE.md)_")
        return "\n".join(lines)

    def _how_to_run_section(self) -> str:
        lines = ["## How to run", ""]
        text = self._readme()
        if text is None:
            lines.append("_No README found for this project._")
        else:
            lines.append(_excerpt(text, _MAX_DOC_LINES))
        return "\n".join(lines)

    def _verification_section(self) -> str:
        counts = self._event_counts()
        lines = [
            "## Verification performed",
            "",
            f"- TEST_PASSED events: {counts['TEST_PASSED']}",
            f"- TEST_FAILED events: {counts['TEST_FAILED']}",
            f"- TASK_COMPLETED events: {counts['TASK_COMPLETED']}",
        ]
        return "\n".join(lines)

    def _limitations_section(self) -> str:
        lines = ["## Remaining limitations", ""]
        text = _read_text(self._mythis / "KNOWN_ISSUES.md")
        if text is None:
            lines.append("_No KNOWN_ISSUES.md recorded._")
        else:
            lines.append(_excerpt(text, _MAX_DOC_LINES))
        return "\n".join(lines)

    def _future_section(self, incomplete: List[Any]) -> str:
        lines = ["## Future roadmap ideas", ""]
        if not incomplete:
            lines.append("_No incomplete roadmap tasks remain._")
        else:
            for task in incomplete:
                task_id = _task_id(task)
                label = f" ({task_id})" if task_id else ""
                status = _task_status(task)
                suffix = f" — status: {status}" if status else ""
                lines.append(f"- {_task_title(task)}{label}{suffix}")
        return "\n".join(lines)

    def _risks_section(self) -> str:
        lines = ["## Known risks", ""]
        text = _read_text(self._mythis / "DISCOVERY_REPORT.md")
        if text is None:
            lines.append("_No DISCOVERY_REPORT.md recorded for this project._")
        else:
            risks = _extract_risks_section(text)
            lines.append(risks if risks else "_No risks section found._")
        return "\n".join(lines)

    # -- assembly ---------------------------------------------------------------
    def generate(self) -> str:
        """Build the full FINAL_REPORT.md markdown (pure computation)."""
        project_name = self._project_dir.name or "project"
        completed, incomplete = _split_tasks(self._roadmap_tasks())
        sections = [
            f"# Final Report — {project_name}",
            "",
            "Generated by Draupnir Forge on project completion (spec §26).",
            "",
            self._built_section(completed),
            "",
            self._architecture_section(),
            "",
            self._how_to_run_section(),
            "",
            self._verification_section(),
            "",
            self._limitations_section(),
            "",
            self._future_section(incomplete),
            "",
            self._risks_section(),
        ]
        return "\n".join(sections) + "\n"

    def write(self) -> Path:
        """Write the report to ``.mythis/FINAL_REPORT.md``; return its path."""
        self._mythis.mkdir(parents=True, exist_ok=True)
        path = self._mythis / _REPORT_FILENAME
        path.write_text(self.generate(), encoding="utf-8")
        return path
