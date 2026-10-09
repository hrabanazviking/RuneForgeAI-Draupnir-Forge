"""context.py — Context Compiler (slice 21).

Huginn flies out over the project's memory and returns with only what
the worker needs: the task, a sip of the vision, the files of the one
affected domain, the interfaces, the standing invariants, the decisions
that bear on the task, and the scars of its previous failures. Everything
else stays in the nest.

:class:`ContextCompiler` builds a :class:`ContextPackage` from the
``.mythis/`` canonical docs and the project tree, estimates its token
weight (characters // 4), and then applies the truncation policy from
``data/context_policy.yaml`` — dropping, front-first, prior failures,
decisions, domain files, and the vision excerpt until the package fits
``max_tokens``. :func:`render` flattens a package into one prompt-ready
string with clear section headers.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, List

from draupnir_forge import _paths
from draupnir_forge.tasks import ForgeTask

log = logging.getLogger("draupnir_forge.context")

POLICY_FILE = "context_policy.yaml"

# Project files that are never admitted into a context package.
_SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".mythis", ".venv", "venv"
}
_SKIP_SUFFIXES = {".pyc", ".pyo", ".o", ".so", ".dll", ".exe"}

# Mapping of policy drop_order names to ContextPackage attributes and
# the empty value each takes when dropped.
_DROPPABLE = {
    "prior_failures": ("prior_failures", []),
    "decisions": ("decisions", []),
    "domain_files": ("domain_files", {}),
    "vision_excerpt": ("vision_excerpt", ""),
}


@dataclass
class ContextPackage:
    """Everything a worker role needs to understand one task.

    Attributes:
        task: The :class:`ForgeTask` being prepared for.
        vision_excerpt: Leading characters of ``SYSTEM_VISION.md``.
        domain_files: Mapping of project-relative path -> file content,
            holding only files of the task's affected domain.
        interfaces: Text of the interfaces doc (``INTERFACES.md``).
        invariants: Individual invariant lines (``INVARIANTS.md``).
        decisions: Decision entries keyword-matched from ``DECISIONS.md``.
        prior_failures: Failure summaries previously recorded for this task.
        token_estimate: Estimated token weight (characters // 4).
    """

    task: ForgeTask
    vision_excerpt: str = ""
    domain_files: Dict[str, str] = field(default_factory=dict)
    interfaces: str = ""
    invariants: List[str] = field(default_factory=list)
    decisions: List[str] = field(default_factory=list)
    prior_failures: List[str] = field(default_factory=list)
    token_estimate: int = 0


def _read_text(path: Path, max_bytes: int) -> str:
    """Read *path* as UTF-8 text, capped at *max_bytes*.

    Returns ``""`` on any failure (missing file, binary content,
    permissions) — a missing doc is a gap, never a crash.
    """
    try:
        raw = path.read_bytes()[:max_bytes]
        return raw.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _mythis(project_dir: Path) -> Path:
    return Path(project_dir) / ".mythis"


def _task_keywords(task: ForgeTask, policy: Dict) -> List[str]:
    """Extract matching keywords from the task title and goal.

    Words of at least ``min_keyword_len`` characters, excluding the
    policy's stopwords. Order is kept, duplicates removed.
    """
    min_len = int(policy.get("min_keyword_len", 4) or 4)
    stopwords = {str(w).lower() for w in policy.get("stopwords", []) or []}
    words = re.findall(r"[a-z0-9]+", f"{task.title} {task.goal}".lower())
    seen = set()
    keywords = []
    for word in words:
        if len(word) >= min_len and word not in stopwords and word not in seen:
            seen.add(word)
            keywords.append(word)
    return keywords


class ContextCompiler:
    """Builds :class:`ContextPackage` for forge tasks from project memory.

    Args:
        project_dir: Root of the project whose ``.mythis/`` docs and tree
            are the source material.
    """

    def __init__(self, project_dir: str | Path) -> None:
        self.project_dir = Path(project_dir)
        self.policy: Dict = self._load_policy()

    # -- policy ----------------------------------------------------------
    @staticmethod
    def _load_policy() -> Dict:
        """Load ``data/context_policy.yaml``; fall back to safe defaults.

        A broken or missing policy file degrades to a conservative
        default rather than sinking the compile.
        """
        defaults = {
            "max_tokens": 32000,
            "drop_order": [
                "prior_failures",
                "decisions",
                "domain_files",
                "vision_excerpt",
            ],
            "max_file_bytes": 51200,
            "vision_excerpt_chars": 4000,
            "max_decisions": 20,
            "max_prior_failures": 20,
            "min_keyword_len": 4,
            "stopwords": [],
        }
        try:
            data = _paths.load_data_yaml(POLICY_FILE)
        except Exception as exc:  # Huginn reports, never panics.
            log.warning("Using default context policy: %s", exc)
            return defaults
        if not isinstance(data, dict):
            return defaults
        merged = dict(defaults)
        merged.update(data)
        return merged

    # -- the build -------------------------------------------------------
    def build(self, task: ForgeTask) -> ContextPackage:
        """Compile a :class:`ContextPackage` for *task*.

        Reads the ``.mythis/`` canonical docs and the project tree, then
        applies the truncation policy so the package always fits
        ``max_tokens``. Never raises on missing or malformed docs —
        gaps surface as empty sections.
        """
        mythis = _mythis(self.project_dir)
        max_file = int(self.policy.get("max_file_bytes", 51200) or 51200)

        vision_text = _read_text(mythis / "SYSTEM_VISION.md", max_file * 4)
        excerpt_chars = int(
            self.policy.get("vision_excerpt_chars", 4000) or 4000
        )
        vision_excerpt = vision_text[:excerpt_chars]

        package = ContextPackage(
            task=task,
            vision_excerpt=vision_excerpt,
            domain_files=self._domain_files(task, max_file),
            interfaces=_read_text(mythis / "INTERFACES.md", max_file * 4),
            invariants=self._invariants(mythis, max_file),
            decisions=self._decisions(task, mythis, max_file),
            prior_failures=self._prior_failures(task, mythis),
        )
        package.token_estimate = self._estimate(package)
        return self._apply_policy(package)

    # -- sources ---------------------------------------------------------
    def _domain_files(self, task: ForgeTask, max_file: int) -> Dict[str, str]:
        """Read the files of the task's domain, and nothing else.

        The domain's files come from ``.mythis/architecture.json`` when a
        domain matches the task's domain name; otherwise the project tree
        is scanned for files whose names mention the domain (fallback).
        """
        files: List[str] = self._domain_files_from_architecture(task)
        if not files:
            files = self._domain_files_by_name(task.domain)
        collected: Dict[str, str] = {}
        for rel in files:
            try:
                full = (self.project_dir / rel).resolve()
            except OSError:
                continue
            base = self.project_dir.resolve()
            if base != full and base not in full.parents:
                continue  # never admit paths outside the project
            content = _read_text(full, max_file)
            if not content:
                continue
            display = str(full.relative_to(base)).replace("\\", "/")
            collected[display] = content
        return collected

    def _domain_files_from_architecture(self, task: ForgeTask) -> List[str]:
        """Return the file list for the domain matching the task.

        Reads ``.mythis/architecture.json`` (the Architect's machine map)
        and returns the ``files`` of the domain whose name matches
        ``task.domain`` (case-insensitive). Returns ``[]`` when the file
        or the domain is missing — the caller falls back to name search.
        """
        path = _mythis(self.project_dir) / "architecture.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        domains = data.get("domains") if isinstance(data, dict) else None
        if not isinstance(domains, list):
            return []
        want = str(task.domain).strip().lower()
        for entry in domains:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name", "")).strip().lower()
            if name == want or (want and (want in name or name in want)):
                raw = entry.get("files", []) or []
                return [str(f) for f in raw if isinstance(f, str)]
        return []

    def _domain_files_by_name(self, domain: str) -> List[str]:
        """Fallback: project files whose path mentions the domain name.

        Walks the tree (skipping VCS/build dirs) and collects files whose
        name or parent directory contains the domain name, so a task
        still gets a relevant slice when no architecture map exists.
        """
        needle = re.sub(r"[^a-z0-9]+", "", str(domain).lower())
        if not needle:
            return []
        hits: List[str] = []
        base = self.project_dir.resolve()
        try:
            stack = [base]
            while stack:
                current = stack.pop()
                try:
                    entries = list(current.iterdir())
                except OSError:
                    continue
                for entry in entries:
                    name = entry.name
                    if entry.is_dir():
                        if name in _SKIP_DIRS or name.startswith("."):
                            continue
                        stack.append(entry)
                        continue
                    lowered = re.sub(r"[^a-z0-9]+", "", name.lower())
                    if entry.suffix.lower() in _SKIP_SUFFIXES:
                        continue
                    if needle in lowered or needle in re.sub(
                        r"[^a-z0-9]+", "", str(entry.parent.name).lower()
                    ):
                        rel = str(entry.relative_to(base)).replace("\\", "/")
                        hits.append(rel)
        except OSError:
            pass
        return sorted(hits)

    def _invariants(self, mythis: Path, max_file: int) -> List[str]:
        """Split ``INVARIANTS.md`` into one-line invariant entries."""
        text = _read_text(mythis / "INVARIANTS.md", max_file * 4)
        lines = []
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            line = re.sub(r"^[-*]\s+", "", line)
            if line:
                lines.append(line)
        return lines

    def _decisions(
        self, task: ForgeTask, mythis: Path, max_file: int
    ) -> List[str]:
        """Keyword-match decision entries in ``DECISIONS.md``.

        Entries are split on ``##``/``###`` headings; an entry is kept
        when any task keyword appears in it. Capped by ``max_decisions``.
        """
        text = _read_text(mythis / "DECISIONS.md", max_file * 4)
        if not text.strip():
            return []
        keywords = _task_keywords(task, self.policy)
        if not keywords:
            return []
        chunks = re.split(r"(?m)^#{2,3}\s+", text)
        max_decisions = int(self.policy.get("max_decisions", 20) or 20)
        matched = []
        for chunk in chunks:
            entry = chunk.strip()
            if not entry or len(entry) < 10:
                continue
            lowered = entry.lower()
            if any(kw in lowered for kw in keywords):
                matched.append(entry[:4000])
                if len(matched) >= max_decisions:
                    break
        return matched

    def _prior_failures(self, task: ForgeTask, mythis: Path) -> List[str]:
        """Collect failure history for this task.

        Two wells: ``KNOWN_ISSUES.md`` lines naming the task id, and the
        event log's ``TASK_FAILED``/``TEST_FAILED`` events whose payload
        names the task. Capped by ``max_prior_failures``.
        """
        failures: List[str] = []
        max_failures = int(self.policy.get("max_prior_failures", 20) or 20)
        task_id = str(task.task_id)

        known_issues = _read_text(mythis / "KNOWN_ISSUES.md", 204800)
        for raw in known_issues.splitlines():
            line = raw.strip()
            if task_id and task_id in line and line not in failures:
                failures.append(re.sub(r"^[-*]\s+", "", line)[:2000])
                if len(failures) >= max_failures:
                    return failures

        events_path = mythis / "events.jsonl"
        if events_path.is_file():
            try:
                from draupnir_forge.events import EventLog, EventType

                wanted = {EventType.TASK_FAILED, EventType.TEST_FAILED}
                for event in EventLog(str(self.project_dir)).query(limit=500):
                    if event.type not in wanted:
                        continue
                    payload = event.payload or {}
                    if str(payload.get("task_id", "")) != task_id:
                        continue
                    summary = str(
                        payload.get("summary")
                        or payload.get("reason")
                        or payload.get("class")
                        or event.type.value
                    )[:2000]
                    if summary and summary not in failures:
                        failures.append(summary)
                    if len(failures) >= max_failures:
                        break
            except Exception as exc:  # advisory, never fatal
                log.warning("Could not read prior failures: %s", exc)
        return failures

    # -- sizing and truncation -------------------------------------------
    @staticmethod
    def _estimate(package: ContextPackage) -> int:
        """Estimate tokens for the whole package (characters // 4)."""
        chars = (
            len(package.task.title)
            + len(package.task.goal)
            + len(package.vision_excerpt)
            + len(package.interfaces)
            + sum(
                len(path) + len(content)
                for path, content in package.domain_files.items()
            )
            + sum(len(item) for item in package.invariants)
            + sum(len(item) for item in package.decisions)
            + sum(len(item) for item in package.prior_failures)
        )
        return chars // 4

    def _apply_policy(self, package: ContextPackage) -> ContextPackage:
        """Drop sections per ``drop_order`` until the package fits.

        Sections are emptied front-first; the task, interfaces, and
        invariants are never dropped.
        """
        max_tokens = int(self.policy.get("max_tokens", 32000) or 32000)
        drop_order = self.policy.get("drop_order", []) or []
        for name in drop_order:
            if package.token_estimate <= max_tokens:
                break
            mapping = _DROPPABLE.get(str(name))
            if mapping is None:
                log.warning("Unknown context drop section %r; skipping.", name)
                continue
            attribute, empty = mapping
            package = replace(package, **{attribute: empty})
            package.token_estimate = self._estimate(package)
            log.info(
                "Context truncated: dropped %s (estimate now %d tokens)",
                attribute,
                package.token_estimate,
            )
        return package


def render(package: ContextPackage) -> str:
    """Render a :class:`ContextPackage` as one prompt-ready string.

    Sections carry clear headers so the consuming model can tell the
    task from the vision from the evidence of past failures.
    """
    task = package.task
    lines = [
        f"# FORGE CONTEXT — {task.task_id}: {task.title}",
        "",
        "## TASK",
        f"Goal: {task.goal}",
        f"Domain: {task.domain}",
        f"Status: {task.status}",
    ]
    if task.depends_on:
        lines.append(f"Depends on: {', '.join(task.depends_on)}")
    if task.constraints:
        lines.append("Constraints:")
        lines.extend(f"- {c}" for c in task.constraints)
    if task.acceptance:
        lines.append("Acceptance:")
        lines.extend(f"- {a}" for a in task.acceptance)
    if task.verification:
        lines.append("Verification:")
        lines.extend(f"- {v}" for v in task.verification)

    lines.extend(["", "## VISION EXCERPT", package.vision_excerpt or "(none)"])

    lines.extend(["", "## DOMAIN FILES"])
    if package.domain_files:
        for path in sorted(package.domain_files):
            lines.append(f"### {path}")
            lines.append("```")
            lines.append(package.domain_files[path])
            lines.append("```")
    else:
        lines.append("(none)")

    lines.extend(["", "## INTERFACES", package.interfaces or "(none)"])

    lines.extend(["", "## INVARIANTS"])
    if package.invariants:
        lines.extend(f"- {item}" for item in package.invariants)
    else:
        lines.append("(none)")

    lines.extend(["", "## RELEVANT DECISIONS"])
    if package.decisions:
        lines.extend(f"- {item}" for item in package.decisions)
    else:
        lines.append("(none)")

    lines.extend(["", "## PRIOR FAILURES"])
    if package.prior_failures:
        lines.extend(f"- {item}" for item in package.prior_failures)
    else:
        lines.append("(none)")

    lines.append("")
    return "\n".join(lines)


__all__ = ["ContextPackage", "ContextCompiler", "render"]
