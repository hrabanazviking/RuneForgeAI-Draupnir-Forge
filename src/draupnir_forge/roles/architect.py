"""architect.py — Architect role (slice 12).

The Architect is the master-builder of the Forge: given a project vision
and a domain map (from the Cartographer), it designs a coherent system
architecture — bounded domains with owners, explicit public interfaces
per module (mined from the source with ``ast``), the invariants the
codebase must never break, and the allowed dependency direction between
domains. It writes the canonical ``.mythis/ARCHITECTURE.md``,
``INTERFACES.md``, ``INVARIANTS.md`` plus ``architecture.json``.

v1 is heuristic: top-level source directories become domains, public
functions/classes are found by parsing each module, invariants are
seeded from ``data/default_invariants.yaml`` and then extended with
generated per-domain laws. A future model-backed refinement pass will
use the prompt templates in ``data/architect_prompts.yaml``.
"""

from __future__ import annotations

import ast
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from draupnir_forge._paths import load_data_yaml
from draupnir_forge.roles.base import (
    Role,
    RoleContext,
    RoleResult,
    register_role,
)

log = logging.getLogger("draupnir_forge.roles.architect")

DEFAULT_INVARIANTS_FILE = "default_invariants.yaml"
ARCHITECTURE_MD = "ARCHITECTURE.md"
INTERFACES_MD = "INTERFACES.md"
INVARIANTS_MD = "INVARIANTS.md"
ARCHITECTURE_JSON = "architecture.json"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Domain:
    """One bounded domain: a name, an owning role, and the files it owns."""

    name: str
    owner: str
    files: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "owner": self.owner, "files": list(self.files)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Domain":
        return cls(
            name=str(data.get("name", "")),
            owner=str(data.get("owner", "")),
            files=[str(f) for f in data.get("files", []) or []],
        )


@dataclass
class Interface:
    """The public surface of one module: dotted name + public callables."""

    module: str
    functions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"module": self.module, "functions": list(self.functions)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Interface":
        return cls(
            module=str(data.get("module", "")),
            functions=[str(f) for f in data.get("functions", []) or []],
        )


@dataclass
class Architecture:
    """The full design produced by the Architect."""

    domains: List[Domain] = field(default_factory=list)
    interfaces: List[Interface] = field(default_factory=list)
    invariants: List[str] = field(default_factory=list)
    dependency_direction: Dict[str, List[str]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "domains": [d.to_dict() for d in self.domains],
            "interfaces": [i.to_dict() for i in self.interfaces],
            "invariants": list(self.invariants),
            "dependency_direction": {
                k: list(v) for k, v in self.dependency_direction.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Architecture":
        return cls(
            domains=[Domain.from_dict(d) for d in data.get("domains", []) or []],
            interfaces=[Interface.from_dict(i) for i in data.get("interfaces", []) or []],
            invariants=[str(x) for x in data.get("invariants", []) or []],
            dependency_direction={
                str(k): [str(x) for x in v or []]
                for k, v in (data.get("dependency_direction", {}) or {}).items()
            },
        )


# ---------------------------------------------------------------------------
# Role
# ---------------------------------------------------------------------------


@register_role
class Architect(Role):
    """Designs the system architecture from vision + domain map."""

    name = "architect"
    purpose = (
        "Turn a vision and a domain map into a coherent architecture: "
        "bounded domains, explicit module interfaces, invariants, and "
        "dependency direction."
    )

    # -- design ----------------------------------------------------------

    def design(
        self,
        vision: Dict[str, Any],
        domain_map: Dict[str, Any],
        project_dir: Optional[str] = None,
    ) -> Architecture:
        """Build an :class:`Architecture` from a vision and a domain map.

        ``domain_map`` maps a domain name to ``{"owner": str,
        "files": [path, ...]}``. File paths may be absolute or relative
        to ``project_dir``. Interfaces are mined from the real source
        with ``ast``; dependency direction is mined from real imports.

        Raises:
            ValueError: If the domain map is empty or the built-in
                invariants data file cannot be loaded.
        """
        if not isinstance(domain_map, dict) or not domain_map:
            raise ValueError("design() needs a non-empty domain_map")

        root = Path(project_dir) if project_dir else None
        domains = [self._build_domain(name, spec, root) for name, spec in domain_map.items()]
        interfaces = self._extract_interfaces(domains, root)
        dependency_direction = self._dependency_direction(domains, root)
        invariants = self._build_invariants(domains, dependency_direction)

        log.info(
            "Designed architecture: %d domains, %d interfaces, %d invariants",
            len(domains),
            len(interfaces),
            len(invariants),
        )
        return Architecture(
            domains=domains,
            interfaces=interfaces,
            invariants=invariants,
            dependency_direction=dependency_direction,
        )

    def _build_domain(
        self, name: str, spec: Any, root: Optional[Path]
    ) -> Domain:
        """Normalize one domain entry from the (forgiving) domain map."""
        owner = "worker"
        files: List[str] = []
        if isinstance(spec, dict):
            owner = str(spec.get("owner", owner) or owner)
            raw_files = spec.get("files", []) or []
            files = [str(f) for f in raw_files]
        elif isinstance(spec, (list, tuple)):
            files = [str(f) for f in spec]
        # Resolve relative paths against the project root, like a raven
        # finding its way home no matter where it took flight.
        if root is not None:
            files = [
                f if Path(f).is_absolute() else str(root / f) for f in files
            ]
        return Domain(name=str(name), owner=owner, files=files)

    def _iter_py_files(self, domain: Domain):
        """Yield existing ``.py`` files owned by a domain."""
        for raw in domain.files:
            path = Path(raw)
            if path.suffix == ".py" and path.is_file():
                yield path
            else:
                log.debug("Skipping non-python or missing file: %s", raw)

    @staticmethod
    def _parse_tree(path: Path) -> Optional[ast.Module]:
        """Parse a module; syntax errors are reported, never raised."""
        try:
            return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            log.warning("Could not parse %s: %s", path, exc)
            return None

    def _module_name(self, path: Path, root: Optional[Path]) -> str:
        """Best-effort dotted module name for a file path."""
        try:
            rel = path.relative_to(root) if root is not None else Path(path.name)
        except ValueError:
            rel = Path(path.name)
        parts = list(rel.with_suffix("").parts)
        # Drop a leading src/ or lib/ layout directory.
        if parts and parts[0] in ("src", "lib"):
            parts = parts[1:]
        # An __init__.py names its package, not itself.
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        return ".".join(parts) if parts else path.stem

    def _extract_interfaces(
        self, domains: List[Domain], root: Optional[Path]
    ) -> List[Interface]:
        """Mine public functions/classes per module with ``ast``.

        Only module-level, non-underscore names count as public —
        the face a module shows to other domains.
        """
        interfaces: List[Interface] = []
        for domain in domains:
            for path in self._iter_py_files(domain):
                tree = self._parse_tree(path)
                if tree is None:
                    continue
                public: List[str] = []
                for node in tree.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        if not node.name.startswith("_"):
                            public.append(node.name)
                interfaces.append(
                    Interface(module=self._module_name(path, root), functions=public)
                )
        return interfaces

    def _domain_of_module(
        self, dotted: str, domains: List[Domain], root: Optional[Path]
    ) -> Optional[str]:
        """Find which domain owns a dotted module name, if any."""
        for domain in domains:
            for raw in domain.files:
                path = Path(raw)
                if path.suffix != ".py":
                    continue
                if self._module_name(path, root) == dotted:
                    return domain.name
                # A package module also covers its submodules.
                pkg = self._module_name(path, root)
                if dotted == pkg or dotted.startswith(pkg + "."):
                    return domain.name
        return None

    @staticmethod
    def _import_roots(tree: ast.Module) -> List[str]:
        """Top-level imported module names from a parsed module."""
        roots: List[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    roots.append(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    roots.append(node.module.split(".")[0])
        return roots

    def _dependency_direction(
        self, domains: List[Domain], root: Optional[Path]
    ) -> Dict[str, List[str]]:
        """Map each domain to the other domains its code really imports.

        This is observed dependency, mined from the source — not a guess.
        """
        direction: Dict[str, List[str]] = {d.name: [] for d in domains}
        for domain in domains:
            deps = set()
            for path in self._iter_py_files(domain):
                tree = self._parse_tree(path)
                if tree is None:
                    continue
                for imported_root in self._import_roots(tree):
                    # Match the import root against owned modules by
                    # comparing first dotted component.
                    for other in domains:
                        if other.name == domain.name:
                            continue
                        for raw in other.files:
                            other_path = Path(raw)
                            if other_path.suffix != ".py":
                                continue
                            mod = self._module_name(other_path, root)
                            if mod.split(".")[0] == imported_root:
                                deps.add(other.name)
                                break
            direction[domain.name] = sorted(deps)
        return direction

    def _build_invariants(
        self, domains: List[Domain], direction: Dict[str, List[str]]
    ) -> List[str]:
        """Seed invariants from data, then add generated per-domain laws."""
        data = load_data_yaml(DEFAULT_INVARIANTS_FILE)
        seeded = [str(x) for x in data.get("invariants", []) or []]
        generated: List[str] = []
        for domain in domains:
            shown = ", ".join(domain.files[:5])
            if len(domain.files) > 5:
                shown += f", ... ({len(domain.files)} total)"
            generated.append(
                f"domain '{domain.name}' owns files [{shown or 'none listed'}] — "
                "no other domain may claim them."
            )
            deps = direction.get(domain.name, [])
            if deps:
                generated.append(
                    f"domain '{domain.name}' may depend on "
                    f"{', '.join(deps)} and on no other domain."
                )
            else:
                generated.append(
                    f"domain '{domain.name}' is a leaf: "
                    "it depends on no other domain."
                )
        return seeded + generated

    # -- run -------------------------------------------------------------

    def domains_from_directories(self, project_dir: str) -> Dict[str, Any]:
        """v1 heuristic fallback: top-level source dirs become domains.

        Used when no domain map was provided — each immediate
        subdirectory holding ``.py`` files becomes a domain owned by
        the worker role.
        """
        root = Path(project_dir)
        domain_map: Dict[str, Any] = {}
        try:
            entries = sorted(p for p in root.iterdir() if p.is_dir())
        except OSError as exc:
            log.warning("Could not list %s: %s", project_dir, exc)
            return domain_map
        for entry in entries:
            if entry.name.startswith("."):
                continue
            py_files = sorted(str(p) for p in entry.rglob("*.py") if p.is_file())
            if py_files:
                domain_map[entry.name] = {"owner": "worker", "files": py_files}
        return domain_map

    def run(self, ctx: RoleContext) -> RoleResult:
        """Design the architecture and write the canonical .mythis/ docs."""
        try:
            project_dir = ctx.project_dir
            vision = ctx.artifacts.get("vision") or {}
            domain_map = ctx.artifacts.get("domain_map") or {}
            if not isinstance(vision, dict):
                vision = {}
            if not isinstance(domain_map, dict):
                # The Cartographer hands over its full DomainMap chart
                # object; the domain table is what we build from.
                chart_domains = getattr(domain_map, "domains", None)
                if isinstance(chart_domains, dict):
                    domain_map = chart_domains
            if not isinstance(domain_map, dict):
                return RoleResult(
                    ok=False,
                    summary="architect: domain_map artifact must be a mapping",
                )
            if not domain_map:
                domain_map = self.domains_from_directories(project_dir)
                if not domain_map:
                    return RoleResult(
                        ok=False,
                        summary=(
                            "architect: no domain_map artifact and no "
                            "source directories found to derive one from"
                        ),
                    )

            arch = self.design(vision, domain_map, project_dir=project_dir)

            mythis = Path(project_dir) / ".mythis"
            mythis.mkdir(parents=True, exist_ok=True)
            json_path = mythis / ARCHITECTURE_JSON
            # Slice 15: stamp the drift baseline with its creation time
            # (ISO-8601 UTC) so drift.py can report its age and staleness.
            # The Architecture dataclass is untouched — readers use .get(),
            # so the extra key is invisible to them.
            payload = arch.to_dict()
            payload["created_ts"] = datetime.now(timezone.utc).isoformat()
            json_path.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            (mythis / ARCHITECTURE_MD).write_text(
                self._render_architecture_md(vision, arch), encoding="utf-8"
            )
            (mythis / INTERFACES_MD).write_text(
                self._render_interfaces_md(arch), encoding="utf-8"
            )
            (mythis / INVARIANTS_MD).write_text(
                self._render_invariants_md(arch), encoding="utf-8"
            )

            try:
                from draupnir_forge.events import EventType

                self.emit(
                    ctx,
                    EventType.ARCHITECTURE_UPDATED,
                    {
                        "domains": [d.name for d in arch.domains],
                        "interfaces": len(arch.interfaces),
                        "invariants": len(arch.invariants),
                    },
                )
            except Exception as exc:  # event emission is best-effort
                log.debug("Event emission skipped: %s", exc)

            return RoleResult(
                ok=True,
                summary=(
                    f"architect: designed {len(arch.domains)} domains, "
                    f"{len(arch.interfaces)} interfaces, "
                    f"{len(arch.invariants)} invariants"
                ),
                artifacts={
                    "architecture": arch.to_dict(),
                    "architecture_path": str(json_path),
                },
            )
        except Exception as exc:  # the contract: never raise on bad input
            log.warning("architect failed: %s", exc)
            return RoleResult(ok=False, summary=f"architect failed: {exc}")

    # -- rendering --------------------------------------------------------

    @staticmethod
    def _render_architecture_md(
        vision: Dict[str, Any], arch: Architecture
    ) -> str:
        title = str(vision.get("name") or vision.get("title") or "Project")
        vision_text = str(vision.get("description") or vision.get("vision") or "")
        lines = [
            f"# Architecture — {title}",
            "",
            "Designed by the Architect role (heuristic v1).",
            "",
        ]
        if vision_text:
            lines += ["## Vision", "", vision_text, ""]
        lines += ["## Domains", ""]
        for domain in arch.domains:
            deps = arch.dependency_direction.get(domain.name, [])
            lines.append(f"### {domain.name} (owner: {domain.owner})")
            lines.append("")
            lines.append(f"Depends on: {', '.join(deps) if deps else 'nothing'}")
            lines.append("")
            lines.append("Files:")
            lines.append("")
            for f in domain.files:
                lines.append(f"- `{f}`")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_interfaces_md(arch: Architecture) -> str:
        lines = ["# Interfaces", "", "Public functions and classes per module.", ""]
        for iface in arch.interfaces:
            lines.append(f"## `{iface.module}`")
            lines.append("")
            if iface.functions:
                for fn in iface.functions:
                    lines.append(f"- `{fn}`")
            else:
                lines.append("_No public functions or classes._")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _render_invariants_md(arch: Architecture) -> str:
        lines = ["# Invariants", "", "Laws the codebase must never break.", ""]
        for i, inv in enumerate(arch.invariants, 1):
            lines.append(f"{i}. {inv}")
        lines.append("")
        return "\n".join(lines)
