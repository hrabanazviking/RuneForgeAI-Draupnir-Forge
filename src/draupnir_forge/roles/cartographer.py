"""cartographer.py — Cartographer role: repository mapper (slice 11).

The Cartographer walks a repository the way a scout walks unknown land:
every file charted by tongue (language), every gate found (entry points),
every proving ground marked (test dirs), every supply cache noted
(dependency files), and the secret paths between modules traced through
their imports (AST import graph, stdlib only), with cycles called out.

Human-readable map: ``.mythis/DOMAIN_MAP.md``.
Machine-readable map: ``.mythis/domain_map.json``.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Set

from draupnir_forge.roles.base import Role, RoleContext, RoleResult, register_role

_LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module data — charting tables, not hardcoded behavior.
# ---------------------------------------------------------------------------

#: Directories the Cartographer never enters.
SKIP_DIRS = frozenset({
    ".git", "__pycache__", "node_modules", ".venv", "venv", ".hg", ".svn",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    "eggs", ".eggs", "__pypackages__", "target", ".idea", ".vscode",
})

#: Binary / non-text extensions the Cartographer charts by omission.
BINARY_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp",
    ".pdf", ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".a", ".o", ".obj", ".class", ".jar",
    ".war", ".pyc", ".pyo", ".pyd", ".woff", ".woff2", ".ttf", ".otf",
    ".eot", ".mp3", ".mp4", ".wav", ".avi", ".mov", ".ogg", ".flac",
    ".sqlite", ".sqlite3", ".db", ".bin", ".dat", ".iso", ".dmg",
})

#: Language by file extension.
LANGUAGE_BY_EXTENSION = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".java": "java", ".kt": "kotlin", ".kts": "kotlin", ".scala": "scala",
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp",
    ".cxx": "cpp", ".cs": "csharp",
    ".go": "go", ".rs": "rust", ".rb": "ruby", ".php": "php",
    ".swift": "swift", ".dart": "dart", ".lua": "lua", ".r": "r",
    ".pl": "perl", ".pm": "perl", ".jl": "julia",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell", ".ps1": "powershell",
    ".sql": "sql",
    ".html": "html", ".htm": "html", ".xml": "xml", ".css": "css",
    ".scss": "scss", ".less": "less", ".vue": "vue", ".svelte": "svelte",
    ".json": "json", ".jsonl": "json", ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml", ".ini": "ini", ".cfg": "ini", ".env": "env",
    ".md": "markdown", ".markdown": "markdown", ".rst": "rst", ".txt": "text",
    ".tex": "latex",
}

#: File basenames that look like program entry points.
ENTRY_POINT_NAMES = frozenset({
    "__main__.py", "cli.py", "main.py", "app.py", "manage.py", "setup.py",
    "run.py", "server.py", "wsgi.py", "asgi.py",
})

#: Directory names that mark proving grounds (tests).
TEST_DIR_NAMES = frozenset({"test", "tests", "spec", "specs"})

#: Dependency / supply-cache file names (or prefixes for requirements*).
DEP_FILE_NAMES = frozenset({
    "pyproject.toml", "setup.py", "setup.cfg", "package.json",
    "Pipfile", "Pipfile.lock", "poetry.lock", "go.mod", "go.sum",
    "Cargo.toml", "Cargo.lock", "pom.xml", "build.gradle", "Gemfile",
    "Gemfile.lock", "composer.json", "composer.lock",
})

#: Fallback stdlib module list when sys.stdlib_module_names is absent
#: (Python < 3.10); sys provides the real list wherever possible.
_FALLBACK_STDLIB = frozenset({
    "os", "sys", "re", "json", "ast", "io", "math", "time", "datetime",
    "pathlib", "logging", "argparse", "unittest", "typing", "dataclasses",
    "collections", "itertools", "functools", "subprocess", "shutil",
    "tempfile", "enum", "abc", "copy", "csv", "hashlib", "random",
    "string", "textwrap", "warnings", "traceback", "inspect", "importlib",
    "threading", "queue", "socket", "http", "urllib", "email", "html",
    "xml", "sqlite3", "pickle", "struct", "codecs", "unicodedata",
})


def _stdlib_names() -> Set[str]:
    """Return the set of stdlib top-level module names."""
    names = getattr(sys, "stdlib_module_names", None)
    if names:
        return set(names)
    return set(_FALLBACK_STDLIB)


@dataclass
class FileInfo:
    """One charted file."""

    path: str        # repo-relative, forward slashes
    language: str    # from LANGUAGE_BY_EXTENSION, "unknown" if unmapped
    size: int        # bytes


@dataclass
class DomainMap:
    """The Cartographer's full chart of a repository."""

    files: List[FileInfo] = field(default_factory=list)
    entry_points: List[str] = field(default_factory=list)
    test_dirs: List[str] = field(default_factory=list)
    dep_files: List[str] = field(default_factory=list)
    import_graph: Dict[str, List[str]] = field(default_factory=dict)
    cycles: List[List[str]] = field(default_factory=list)
    domains: Dict[str, List[str]] = field(default_factory=dict)


def _rel(path: str, root: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


def _collect_import_names(source: str) -> List[str]:
    """Parse Python source and return absolute import module names.

    Collects full dotted names from top-level ``import x.y`` and
    ``from x.y import ...`` statements; relative imports and parse
    failures yield nothing. Never raises.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    names: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.append(node.module)
    return names


def _collect_top_level_imports(source: str) -> List[str]:
    """Top-level (first-component) absolute import names. Never raises."""
    return [name.split(".")[0] for name in _collect_import_names(source)]


def _module_name(rel_path: str) -> str:
    stem = rel_path[:-3] if rel_path.endswith(".py") else rel_path
    parts = [p for p in stem.split("/") if p]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _find_cycles(graph: Dict[str, List[str]]) -> List[List[str]]:
    """Find import cycles between charted modules (DFS, deduped)."""
    cycles: List[List[str]] = []
    seen: Set[str] = set()
    visiting: List[str] = []
    in_path: Set[str] = set()

    def visit(node: str) -> None:
        if node in in_path:
            idx = visiting.index(node)
            cycle = visiting[idx:] + [node]
            key = "->".join(sorted(set(cycle)))
            if key not in seen:
                seen.add(key)
                cycles.append(cycle)
            return
        if node in seen or node not in graph:
            return
        in_path.add(node)
        visiting.append(node)
        for dep in graph[node]:
            visit(dep)
        visiting.pop()
        in_path.discard(node)
        seen.add(node)

    for node in graph:
        visit(node)
    return cycles


def map_repo(project_dir: str) -> DomainMap:
    """Walk ``project_dir`` and chart it into a DomainMap. Never raises."""
    chart = DomainMap()
    stdlib = _stdlib_names()
    # module name -> top-level stdlib imports, for cycle detection later
    local_imports: Dict[str, List[str]] = {}

    try:
        walker = os.walk(project_dir)
    except OSError as exc:
        _LOG.warning("cartographer cannot walk %s: %s", project_dir, exc)
        return chart

    for dirpath, dirnames, filenames in walker:
        # Keep the scout out of forbidden ground.
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        rel_dir = _rel(dirpath, project_dir)
        if rel_dir.split("/")[0] in TEST_DIR_NAMES or rel_dir in TEST_DIR_NAMES:
            # record test dirs once, still chart their files
            if rel_dir not in chart.test_dirs:
                chart.test_dirs.append(rel_dir)

        for filename in filenames:
            full = os.path.join(dirpath, filename)
            rel_path = _rel(full, project_dir)
            ext = os.path.splitext(filename)[1].lower()
            if ext in BINARY_EXTENSIONS:
                continue
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            language = LANGUAGE_BY_EXTENSION.get(ext, "unknown")
            chart.files.append(FileInfo(path=rel_path,
                                        language=language, size=size))

            if filename in ENTRY_POINT_NAMES:
                chart.entry_points.append(rel_path)
            if (filename in DEP_FILE_NAMES
                    or filename.startswith("requirements") and
                    filename.endswith(".txt")):
                chart.dep_files.append(rel_path)

            if language == "python":
                try:
                    with open(full, "r", encoding="utf-8",
                              errors="replace") as handle:
                        source = handle.read()
                except OSError:
                    source = ""
                if source:
                    all_names = _collect_import_names(source)
                    stdlib_imports = sorted({
                        name.split(".")[0] for name in all_names
                        if name.split(".")[0] in stdlib
                    })
                    mod = _module_name(rel_path)
                    chart.import_graph[mod] = stdlib_imports
                    # Full dotted names of non-stdlib imports, for cycle
                    # tracing between charted modules.
                    local_imports[mod] = [
                        name for name in all_names
                        if name.split(".")[0] not in stdlib
                    ]

    # Cycle detection over module-to-module edges: resolve each local
    # import to charted modules it names (exact module or package prefix).
    modules = set(chart.import_graph)
    module_edges: Dict[str, List[str]] = {}
    for mod, raw_imports in local_imports.items():
        edges = []
        for imp in raw_imports:
            for candidate in modules:
                if candidate == imp or candidate.startswith(imp + "."):
                    edges.append(candidate)
        module_edges[mod] = sorted(set(edges))
    chart.cycles = _find_cycles(module_edges)

    chart.files.sort(key=lambda f: f.path)
    chart.entry_points.sort()
    chart.test_dirs.sort()
    chart.dep_files.sort()

    # Domains: top-level directory -> files; root files under "root".
    domains: Dict[str, List[str]] = {}
    for info in chart.files:
        parts = info.path.split("/")
        domain = parts[0] if len(parts) > 1 else "root"
        domains.setdefault(domain, []).append(info.path)
    chart.domains = {k: domains[k] for k in sorted(domains)}

    return chart


def _render_markdown(chart: DomainMap, project_dir: str) -> str:
    """Render the human-readable DOMAIN_MAP.md."""
    lines = [
        "# Domain Map",
        "",
        f"_Charted by the Cartographer from `{project_dir}`._",
        "",
        f"- Files charted: {len(chart.files)}",
        f"- Entry points: {len(chart.entry_points)}",
        f"- Test dirs: {len(chart.test_dirs)}",
        f"- Dependency files: {len(chart.dep_files)}",
        f"- Modules in import graph: {len(chart.import_graph)}",
        f"- Import cycles: {len(chart.cycles)}",
        f"- Domains: {len(chart.domains)}",
        "",
        "## Languages",
        "",
        "| Language | Files |",
        "| --- | --- |",
    ]
    lang_counts: Dict[str, int] = {}
    for info in chart.files:
        lang_counts[info.language] = lang_counts.get(info.language, 0) + 1
    for lang in sorted(lang_counts):
        lines.append(f"| {lang} | {lang_counts[lang]} |")

    lines += ["", "## Entry points", ""]
    lines += [f"- {p}" for p in chart.entry_points] or ["_None found._"]
    lines += ["", "## Test directories", ""]
    lines += [f"- {p}" for p in chart.test_dirs] or ["_None found._"]
    lines += ["", "## Dependency files", ""]
    lines += [f"- {p}" for p in chart.dep_files] or ["_None found._"]

    lines += ["", "## Domains (top-level directory -> files)", ""]
    for domain, files in chart.domains.items():
        lines.append(f"### {domain} ({len(files)} files)")
        lines.append("")
        for path in files:
            lines.append(f"- {path}")
        lines.append("")

    lines += ["## Import graph (module -> stdlib imports)", ""]
    if chart.import_graph:
        for mod in sorted(chart.import_graph):
            imports = ", ".join(chart.import_graph[mod]) or "_none_"
            lines.append(f"- `{mod}` -> {imports}")
    else:
        lines.append("_No Python modules charted._")

    lines += ["", "## Import cycles", ""]
    if chart.cycles:
        for cycle in chart.cycles:
            lines.append(f"- {' -> '.join(cycle)}")
    else:
        lines.append("_No cycles found._")
    lines.append("")
    return "\n".join(lines)


@register_role
class Cartographer(Role):
    """The Cartographer maps a repository into a DomainMap."""

    name = "cartographer"
    purpose = "maps a repository into a domain map"

    def run(self, ctx: RoleContext) -> RoleResult:
        """Chart ``ctx.project_dir``; write DOMAIN_MAP.md + domain_map.json."""
        try:
            chart = map_repo(ctx.project_dir)
            mythis_dir = os.path.join(ctx.project_dir, ".mythis")
            os.makedirs(mythis_dir, exist_ok=True)
            md_path = os.path.join(mythis_dir, "DOMAIN_MAP.md")
            json_path = os.path.join(mythis_dir, "domain_map.json")
            with open(md_path, "w", encoding="utf-8") as handle:
                handle.write(_render_markdown(chart, ctx.project_dir))
            with open(json_path, "w", encoding="utf-8") as handle:
                json.dump(asdict(chart), handle, indent=2)
                handle.write("\n")
        except OSError as exc:
            return RoleResult(
                ok=False,
                summary=f"cartographer could not write domain map: {exc}",
                escalation=f"Check that {ctx.project_dir} is writable.",
            )
        except Exception as exc:  # never let the role crash the forge
            _LOG.warning("cartographer failed: %s", exc)
            return RoleResult(
                ok=False,
                summary=f"cartographer failed unexpectedly: {exc}",
            )

        self.emit(ctx, "DOMAIN_MAPPED",
                  {"domain_map_path": md_path,
                   "files": len(chart.files),
                   "cycles": len(chart.cycles)})
        summary = (f"mapped {len(chart.files)} files across "
                   f"{len(chart.domains)} domains, "
                   f"{len(chart.cycles)} import cycles")
        return RoleResult(
            ok=True,
            summary=summary,
            artifacts={"domain_map": chart,
                       "domain_map_path": md_path,
                       "domain_map_json": json_path},
        )
