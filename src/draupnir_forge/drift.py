"""drift.py — Slice 36: drift detection, the Forge's early-warning raven.

The architecture the Architect designed (``.mythis/architecture.json``)
is a map of how the code *should* look. The live repository is how it
*does* look. When the two diverge — a new domain sprouting, a domain
vanishing, imports crossing boundaries the architecture forbids, the
public interface shifting underfoot — the Forge must notice before the
rot spreads.

:class:`DriftDetector` compares the baseline against the living tree:

- **new_top_level_dir** — a fresh top-level source directory the
  architecture never named (medium severity);
- **new_domain** — a fresh top-level directory big enough to be a whole
  domain in disguise (high severity);
- **missing_domain** — an architecture top-level directory gone from the
  repo (high severity);
- **cross_domain_import** — an import edge crossing domain boundaries in
  a direction the architecture's ``dependency_direction`` does not allow
  (high severity). The import edges are rebuilt from the Cartographer's
  AST helpers, not guessed;
- **interface_changed** — the public API surface drifted from
  ``.mythis/api_snapshot.json`` (high when names were *removed*,
  medium when only added).

:meth:`DriftDetector.report` files the findings in
``.mythis/KNOWN_ISSUES.md`` and returns True when any finding is
high-severity, which is the caller's signal to trigger REGROUNDING.
"""

from __future__ import annotations

import ast
import json
import logging
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union

from draupnir_forge.roles import architect
from draupnir_forge.roles import cartographer

log = logging.getLogger("draupnir_forge.drift")

ARCHITECTURE_FILE = "architecture.json"
API_SNAPSHOT_FILE = "api_snapshot.json"
KNOWN_ISSUES_FILE = "KNOWN_ISSUES.md"

#: A fresh top-level directory with this many source files is treated as
#: a whole new domain rather than a stray directory.
NEW_DOMAIN_FILE_THRESHOLD = 5

#: Severity levels, ordered for the report() high-severity check.
SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"


class DriftKind(str, Enum):
    """The five shapes drift can take."""

    NEW_DOMAIN = "new_domain"
    MISSING_DOMAIN = "missing_domain"
    CROSS_DOMAIN_IMPORT = "cross_domain_import"
    INTERFACE_CHANGED = "interface_changed"
    NEW_TOP_LEVEL_DIR = "new_top_level_dir"


@dataclass
class DriftFinding:
    """One place where the living repo diverges from the architecture."""

    kind: DriftKind
    detail: str
    severity: str = SEVERITY_MEDIUM

    def to_dict(self) -> Dict[str, str]:
        """Serialize to a plain JSON-compatible dict."""
        return {
            "kind": self.kind.value,
            "detail": self.detail,
            "severity": self.severity,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> "DriftFinding":
        """Rebuild a finding from :meth:`to_dict` output."""
        return cls(
            kind=DriftKind(str(data.get("kind", ""))),
            detail=str(data.get("detail", "")),
            severity=str(data.get("severity", SEVERITY_MEDIUM)),
        )


# ---------------------------------------------------------------------------
# Loading the baseline
# ---------------------------------------------------------------------------


def _load_architecture(
    project_dir: Path,
) -> Optional[architect.Architecture]:
    """Load ``.mythis/architecture.json``; None when absent or invalid."""
    path = project_dir / ".mythis" / ARCHITECTURE_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.warning("No architecture baseline at %s; drift skipped", path)
        return None
    except (ValueError, OSError) as exc:
        log.warning("Architecture baseline unreadable (%s); drift skipped", exc)
        return None
    try:
        data = raw if isinstance(raw, dict) else {}
        return architect.Architecture.from_dict(data)
    except Exception as exc:
        log.warning("Architecture baseline malformed (%s); drift skipped", exc)
        return None


def _arch_top_dirs(arch: architect.Architecture) -> Set[str]:
    """Top-level directories claimed by the architecture's domain files."""
    claimed: Set[str] = set()
    for domain in arch.domains:
        for raw in domain.files:
            parts = str(raw).replace("\\", "/").split("/")
            if len(parts) > 1 and parts[0] and parts[0] not in cartographer.SKIP_DIRS:
                claimed.add(parts[0])
    return claimed


# ---------------------------------------------------------------------------
# Live-repo scanning
# ---------------------------------------------------------------------------


def _live_top_dirs(project_dir: Path) -> Dict[str, int]:
    """Top-level dirs holding source files -> source file counts.

    Skips the Cartographer's forbidden ground plus hidden directories
    and the Forge's own ``.mythis`` ledger.
    """
    counts: Dict[str, int] = {}
    try:
        entries = list(project_dir.iterdir())
    except OSError as exc:
        log.warning("Cannot scan %s: %s", project_dir, exc)
        return counts
    for entry in entries:
        name = entry.name
        if not entry.is_dir():
            continue
        if name in cartographer.SKIP_DIRS or name.startswith("."):
            continue
        n_files = 0
        for dirpath, dirnames, filenames in os.walk(entry):
            dirnames[:] = [d for d in dirnames
                           if d not in cartographer.SKIP_DIRS
                           and not d.startswith(".")]
            for filename in filenames:
                ext = os.path.splitext(filename)[1].lower()
                if ext in cartographer.LANGUAGE_BY_EXTENSION:
                    n_files += 1
        if n_files:
            counts[name] = n_files
    return counts


def scan_public_api(project_dir: Union[str, Path]) -> Dict[str, List[str]]:
    """Scan the repo's Python public API: module -> public top-level names.

    A name is public when it does not start with an underscore. The
    shape matches what :class:`VerificationEngine` baselines in
    ``.mythis/api_snapshot.json``, so drift can diff against it.
    """
    root = Path(project_dir)
    api: Dict[str, List[str]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in cartographer.SKIP_DIRS
                       and not d.startswith(".")]
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            full = Path(dirpath) / filename
            rel = full.relative_to(root).as_posix()
            try:
                tree = ast.parse(full.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, ValueError):
                continue
            names: Set[str] = set()
            for node in tree.body:
                target_names: List[str] = []
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                     ast.ClassDef)):
                    target_names = [node.name]
                elif isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            target_names.append(target.id)
                elif isinstance(node, ast.AnnAssign):
                    if isinstance(node.target, ast.Name):
                        target_names = [node.target.id]
                for name in target_names:
                    if name and not name.startswith("_"):
                        names.add(name)
            module = cartographer._module_name(rel)
            api[module] = sorted(names)
    return api


# ---------------------------------------------------------------------------
# Import edges (reusing the Cartographer's AST helpers)
# ---------------------------------------------------------------------------


def _module_edges(project_dir: Path) -> List[Tuple[str, str, str, str]]:
    """Local module-to-module import edges as (src_mod, dst_mod, src_file, dst_file).

    Reuses :mod:`cartographer`'s import-name collection and module naming,
    then resolves each import to charted modules the way the
    Cartographer's cycle detection does.
    """
    modules: Dict[str, str] = {}  # module name -> repo-relative file
    for dirpath, dirnames, filenames in os.walk(project_dir):
        dirnames[:] = [d for d in dirnames
                       if d not in cartographer.SKIP_DIRS
                       and not d.startswith(".")]
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            full = Path(dirpath) / filename
            rel = full.relative_to(project_dir).as_posix()
            modules[cartographer._module_name(rel)] = rel

    edges: List[Tuple[str, str, str, str]] = []
    names = set(modules)
    for mod, rel in modules.items():
        try:
            source = (project_dir / rel).read_text(encoding="utf-8")
        except OSError:
            continue
        for imp in cartographer._collect_import_names(source):
            for candidate in names:
                if candidate == imp or candidate.startswith(imp + "."):
                    if candidate != mod:
                        edges.append((mod, candidate, rel,
                                      modules[candidate]))
    # Deduplicate while keeping a stable order.
    seen: Set[Tuple[str, str]] = set()
    unique: List[Tuple[str, str, str, str]] = []
    for edge in edges:
        key = (edge[0], edge[1])
        if key not in seen:
            seen.add(key)
            unique.append(edge)
    return unique


def _domain_of_file(rel_path: str,
                    arch: architect.Architecture) -> Optional[str]:
    """Name the architecture domain that owns ``rel_path``.

    Ownership is decided by longest matching directory prefix among the
    domain's files: ``src/web/b.py`` belongs to the domain owning
    ``src/web/`` rather than one merely owning ``src/``. Falls back to
    top-level-directory matching when no deeper prefix fits.
    """
    rel = rel_path.replace("\\", "/")
    top = rel.split("/")[0]
    best: Optional[str] = None
    best_len = -1
    fallback: Optional[str] = None
    for domain in arch.domains:
        for raw in domain.files:
            owned = str(raw).replace("\\", "/")
            prefix = owned.rsplit("/", 1)[0] + "/" if "/" in owned else ""
            if prefix and rel.startswith(prefix) and len(prefix) > best_len:
                best, best_len = domain.name, len(prefix)
            parts = owned.split("/")
            if len(parts) > 1 and parts[0] == top and fallback is None:
                fallback = domain.name
    return best if best is not None else fallback


# ---------------------------------------------------------------------------
# The detector
# ---------------------------------------------------------------------------


class DriftDetector:
    """Compares the architecture baseline against the living repository.

    Args:
        project_dir: Root of the forge project holding ``.mythis/``.
    """

    def __init__(self, project_dir: Union[str, Path]) -> None:
        self.project_dir = Path(project_dir)
        self._mythis = self.project_dir / ".mythis"

    def compare(self) -> List[DriftFinding]:
        """Diff the baseline against the live repo; never raises."""
        findings: List[DriftFinding] = []
        try:
            arch = _load_architecture(self.project_dir)
        except Exception as exc:  # Huginn reports, never panics.
            log.warning("Drift baseline load failed: %s", exc)
            return findings
        if arch is None:
            return findings

        findings.extend(self._check_top_level_dirs(arch))
        findings.extend(self._check_cross_domain_imports(arch))
        findings.extend(self._check_interface_changes())
        # Deterministic order: severity first, then kind, then detail.
        order = {SEVERITY_HIGH: 0, SEVERITY_MEDIUM: 1, SEVERITY_LOW: 2}
        findings.sort(key=lambda f: (order.get(f.severity, 3),
                                     f.kind.value, f.detail))
        return findings

    # -- top-level directories --------------------------------------

    def _check_top_level_dirs(
        self, arch: architect.Architecture
    ) -> List[DriftFinding]:
        """New and vanished top-level source directories."""
        findings: List[DriftFinding] = []
        claimed = _arch_top_dirs(arch)
        live = _live_top_dirs(self.project_dir)
        for dirname in sorted(set(live) - claimed):
            count = live[dirname]
            if count >= NEW_DOMAIN_FILE_THRESHOLD:
                findings.append(DriftFinding(
                    kind=DriftKind.NEW_DOMAIN,
                    detail=(f"top-level directory '{dirname}/' holds "
                            f"{count} source files but the architecture "
                            f"names no domain there"),
                    severity=SEVERITY_HIGH,
                ))
            else:
                findings.append(DriftFinding(
                    kind=DriftKind.NEW_TOP_LEVEL_DIR,
                    detail=(f"new top-level source directory '{dirname}/' "
                            f"({count} source files) not in the architecture"),
                    severity=SEVERITY_MEDIUM,
                ))
        for dirname in sorted(claimed - set(live)):
            findings.append(DriftFinding(
                kind=DriftKind.MISSING_DOMAIN,
                detail=(f"architecture claims top-level directory "
                        f"'{dirname}/' but it no longer exists in the repo"),
                severity=SEVERITY_HIGH,
            ))
        return findings

    # -- cross-domain imports ----------------------------------------

    def _check_cross_domain_imports(
        self, arch: architect.Architecture
    ) -> List[DriftFinding]:
        """Import edges the architecture's dependency_direction forbids."""
        findings: List[DriftFinding] = []
        direction = arch.dependency_direction or {}
        if not direction:
            return findings  # no declared directions, nothing to violate
        try:
            edges = _module_edges(self.project_dir)
        except Exception as exc:
            log.warning("Import edge scan failed: %s", exc)
            return findings
        seen: Set[Tuple[str, str]] = set()
        for _src, _dst, src_file, dst_file in edges:
            src_domain = _domain_of_file(src_file, arch)
            dst_domain = _domain_of_file(dst_file, arch)
            if not src_domain or not dst_domain or src_domain == dst_domain:
                continue
            allowed = set(direction.get(src_domain, []))
            key = (src_domain, dst_domain)
            if dst_domain not in allowed and key not in seen:
                seen.add(key)
                findings.append(DriftFinding(
                    kind=DriftKind.CROSS_DOMAIN_IMPORT,
                    detail=(f"'{src_file}' (domain '{src_domain}') imports "
                            f"'{dst_file}' (domain '{dst_domain}'), but the "
                            f"architecture allows '{src_domain}' to depend "
                            f"only on {sorted(allowed) or 'nothing'}"),
                    severity=SEVERITY_HIGH,
                ))
        return findings

    # -- public interface --------------------------------------------

    def _check_interface_changes(self) -> List[DriftFinding]:
        """Public API surface vs ``.mythis/api_snapshot.json``."""
        findings: List[DriftFinding] = []
        path = self._mythis / API_SNAPSHOT_FILE
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return findings  # no baseline yet: nothing to drift from
        except (ValueError, OSError) as exc:
            log.warning("API snapshot unreadable (%s); interface check skipped",
                        exc)
            return findings
        if not isinstance(snapshot, dict):
            log.warning("API snapshot is not a mapping; interface check skipped")
            return findings
        try:
            live = scan_public_api(self.project_dir)
        except Exception as exc:
            log.warning("Public API scan failed: %s", exc)
            return findings
        for module in sorted(set(live) | set(snapshot)):
            old = set(snapshot.get(module, []) or [])
            new = set(live.get(module, []) or [])
            added = sorted(new - old)
            removed = sorted(old - new)
            if not added and not removed:
                continue
            parts = []
            if added:
                parts.append(f"added: {', '.join(added)}")
            if removed:
                parts.append(f"removed: {', '.join(removed)}")
            findings.append(DriftFinding(
                kind=DriftKind.INTERFACE_CHANGED,
                detail=(f"public API of module '{module}' changed — "
                        + "; ".join(parts)),
                severity=SEVERITY_HIGH if removed else SEVERITY_MEDIUM,
            ))
        return findings

    # -- reporting ----------------------------------------------------

    def report(self, findings: List[DriftFinding]) -> bool:
        """Append findings to ``.mythis/KNOWN_ISSUES.md``.

        Returns:
            True when any finding is high-severity — the caller's cue to
            trigger REGROUNDING. Never raises.
        """
        try:
            self._mythis.mkdir(parents=True, exist_ok=True)
            path = self._mythis / KNOWN_ISSUES_FILE
            if not path.exists():
                header = "# Known Issues\n\n"
            else:
                header = ""
            from datetime import datetime, timezone
            stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            lines = [header, f"## Drift scan — {stamp}\n"]
            if not findings:
                lines.append("- No drift found: the repo matches the "
                             "architecture baseline.\n")
            for finding in findings:
                lines.append(
                    f"- **[{finding.severity}]** `{finding.kind.value}`: "
                    f"{finding.detail}\n"
                )
            lines.append("\n")
            with open(path, "a", encoding="utf-8") as handle:
                handle.write("".join(lines))
        except OSError as exc:
            log.warning("Could not append drift report: %s", exc)
        return any(f.severity == SEVERITY_HIGH for f in findings)
