"""archdocs.py — Architecture document generator (slice 34).

Pure rendering functions: an architecture dict goes in, polished
markdown comes out. No I/O, no model calls, no side effects — the
Skald's verses are deterministic, so tests can pin them word for word.

``arch`` follows ``Architecture.to_dict()`` (see
``roles/architect.py``)::

    {
      "domains": [{"name": str, "owner": str, "files": [str, ...]}, ...],
      "interfaces": [{"module": str, "functions": [str, ...]}, ...],
      "invariants": [str, ...],
      "dependency_direction": {domain: [domain, ...], ...},
    }

Every renderer tolerates missing keys and empty lists.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _domains(arch: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = arch.get("domains") or []
    return [d for d in raw if isinstance(d, dict)]


def _interfaces(arch: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = arch.get("interfaces") or []
    return [i for i in raw if isinstance(i, dict)]


def _invariants(arch: Dict[str, Any]) -> List[str]:
    raw = arch.get("invariants") or []
    return [str(x) for x in raw]


def _dependency_edges(arch: Dict[str, Any]) -> List[tuple]:
    raw = arch.get("dependency_direction") or {}
    known = {str(d.get("name", "")) for d in _domains(arch)}
    edges: List[tuple] = []
    if isinstance(raw, dict):
        for source, targets in raw.items():
            if not isinstance(targets, (list, tuple)):
                continue
            for target in targets:
                # Only edges between charted domains are drawn — a
                # dependency on an unknown domain is a data problem,
                # not a diagram feature.
                if str(source) in known and str(target) in known:
                    edges.append((str(source), str(target)))
    return edges


def _mermaid_id(name: str) -> str:
    """Mermaid-safe node id: alphanumerics and underscores only."""
    safe = re.sub(r"\W", "_", name.strip())
    if not safe:
        safe = "unnamed"
    if safe[0].isdigit():
        safe = "d_" + safe
    return safe


def _mermaid_diagram(arch: Dict[str, Any]) -> str:
    """Mermaid flowchart: domains as nodes, dependency edges between them."""
    domains = _domains(arch)
    edges = _dependency_edges(arch)
    names = {str(d.get("name", "")) for d in domains}
    node_ids = {_mermaid_id(n): n for n in names if n}

    lines = ["```mermaid", "flowchart LR"]
    for node_id in sorted(node_ids):
        lines.append(f'    {node_id}["{node_ids[node_id]}"]')
    if edges:
        for source, target in sorted(set(edges)):
            sid, tid = _mermaid_id(source), _mermaid_id(target)
            if sid in node_ids and tid in node_ids:
                lines.append(f"    {sid} --> {tid}")
    else:
        lines.append("    _no_dependencies_([no inter-domain dependencies])")
    lines.append("```")
    return "\n".join(lines)


def render_architecture(arch: Dict[str, Any]) -> str:
    """Render the polished ARCHITECTURE.md: diagram + domain tables."""
    domains = _domains(arch)
    interfaces = _interfaces(arch)
    invariants = _invariants(arch)
    edges = _dependency_edges(arch)

    lines = [
        "# Architecture",
        "",
        f"_Rendered {_utc_stamp()}._",
        "",
        "## Domain dependency diagram",
        "",
        _mermaid_diagram(arch),
        "",
        "## Domains",
        "",
        "| Domain | Owner | Files | Dependencies |",
        "| --- | --- | --- | --- |",
    ]
    deps_by_domain: Dict[str, List[str]] = {}
    for source, target in edges:
        deps_by_domain.setdefault(source, []).append(target)
    if domains:
        for domain in domains:
            name = str(domain.get("name", ""))
            owner = str(domain.get("owner", "worker") or "worker")
            files = domain.get("files") or []
            deps = ", ".join(sorted(set(deps_by_domain.get(name, [])))) or "_none_"
            lines.append(
                f"| {name} | {owner} | {len(files)} | {deps} |"
            )
    else:
        lines.append("| _none_ | _none_ | _none_ | _none_ |")

    for domain in domains:
        name = str(domain.get("name", ""))
        files = domain.get("files") or []
        owner = str(domain.get("owner", "worker") or "worker")
        lines += ["", f"### {name} (owner: {owner})", ""]
        if files:
            for path in files:
                lines.append(f"- `{path}`")
        else:
            lines.append("_No files listed._")

    lines += ["", "## Interfaces", ""]
    lines += ["| Module | Public symbols |", "| --- | --- |"]
    if interfaces:
        for iface in interfaces:
            module = str(iface.get("module", ""))
            symbols = iface.get("functions") or []
            shown = ", ".join(f"`{s}`" for s in symbols) or "_none_"
            lines.append(f"| `{module}` | {shown} |")
    else:
        lines.append("| _none_ | _none_ |")

    lines += ["", "## Invariants", ""]
    if invariants:
        for invariant in invariants:
            lines.append(f"- {invariant}")
    else:
        lines.append("_No invariants recorded._")
    lines.append("")
    return "\n".join(lines)


def render_interfaces(arch: Dict[str, Any]) -> str:
    """Render INTERFACES.md: one function/class table per module."""
    interfaces = _interfaces(arch)
    lines = [
        "# Interfaces",
        "",
        "Public functions and classes per module. "
        "Kind is a naming heuristic: ``CamelCase`` names are treated as "
        "classes, everything else as functions.",
        "",
    ]
    if not interfaces:
        lines.append("_No interfaces recorded._")
        lines.append("")
        return "\n".join(lines)
    for iface in interfaces:
        module = str(iface.get("module", ""))
        symbols = [str(s) for s in (iface.get("functions") or [])]
        lines += [f"## `{module}`", "", "| Symbol | Kind |", "| --- | --- |"]
        if symbols:
            for symbol in symbols:
                kind = "class" if symbol[:1].isupper() else "function"
                lines.append(f"| `{symbol}` | {kind} |")
        else:
            lines.append("| _none_ | _none_ |")
        lines.append("")
    return "\n".join(lines)


def render_invariants(invariants: List[str]) -> str:
    """Render INVARIANTS.md: one unchecked checklist item per invariant."""
    lines = [
        "# Invariants",
        "",
        "Laws the codebase must never break. Tick each one off only "
        "after it has been verified against the code.",
        "",
    ]
    items = [str(x) for x in invariants or []]
    if items:
        for item in items:
            lines.append(f"- [ ] {item}")
    else:
        lines.append("_No invariants recorded._")
    lines.append("")
    return "\n".join(lines)
