"""discovery.py — Discovery report generator (slice 33).

The DiscoveryReport is the Forge's first scouting report: it takes a
project directory and a domain map (the Cartographer's chart, in
whatever forgiving shape it arrives) and produces a markdown survey
covering every discovery bullet of the Forge spec (§2): file inventory
by language, entry points, test directories and the test command,
dependency files, the domain list, documentation inventory, and the
runtime/build state (build files and CI configs found).

It also calls out risks the way a lookout calls out reefs: no tests,
no CI, giant files, binary blobs, unpinned dependencies, missing
README. The report is written to ``.mythis/DISCOVERY_REPORT.md``.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from draupnir_forge.roles.cartographer import (
    BINARY_EXTENSIONS,
    LANGUAGE_BY_EXTENSION,
    SKIP_DIRS,
)

_LOG = logging.getLogger(__name__)

MYTHIS_DIR = ".mythis"
REPORT_FILE = "DISCOVERY_REPORT.md"

#: CI / pipeline config paths worth charting (relative globs).
CI_GLOB_PATTERNS = (
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
    ".gitlab-ci.yml",
    ".travis.yml",
    ".circleci/config.yml",
    ".drone.yml",
    "Jenkinsfile",
    "azure-pipelines.yml",
    "tox.ini",
    "buildspec.yml",
    "appveyor.yml",
)

#: Build / runtime file names and the role each plays.
BUILD_FILE_ROLES = {
    "pyproject.toml": "python build / packaging",
    "setup.py": "python packaging",
    "setup.cfg": "python packaging / tool config",
    "Makefile": "make targets",
    "Dockerfile": "container build",
    "docker-compose.yml": "container orchestration",
    "package.json": "node packaging / scripts",
    "Cargo.toml": "rust build",
    "go.mod": "go modules",
    "pom.xml": "maven build",
    "build.gradle": "gradle build",
    "Gemfile": "ruby bundler",
    "composer.json": "php composer",
    "requirements.txt": "python requirements",
}

#: Files that look like test modules even outside a test dir.
_TEST_FILE_PATTERNS = (
    re.compile(r"(^|[/_.-])test[_-].*\.(py|js|ts|go|rb)$", re.IGNORECASE),
    re.compile(r".*[_.-]test\.(py|js|ts|go|rb)$", re.IGNORECASE),
    re.compile(r".*[_.-]spec\.(py|js|ts|go|rb)$", re.IGNORECASE),
)

#: Lines past this count make a file "huge".
_LINE_COUNT_CHUNK = 65536
_HUGE_LINES = 5000
_BLOB_BYTES = 1_000_000


def _extension_is_binary(rel_path: str) -> bool:
    ext = os.path.splitext(rel_path)[1].lower()
    return ext in BINARY_EXTENSIONS


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _language_of(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    return LANGUAGE_BY_EXTENSION.get(ext, "unknown")


def _is_binary(path: Path) -> bool:
    """Heuristic: binary extension, or the first chunk will not decode."""
    if path.suffix.lower() in BINARY_EXTENSIONS:
        return True
    try:
        with open(path, "rb") as handle:
            chunk = handle.read(8192)
        chunk.decode("utf-8")
        return False
    except (OSError, UnicodeDecodeError, ValueError):
        return True


def _walk_files(project_dir: Path) -> List[Tuple[str, str, int]]:
    """Walk the project, returning (rel_path, language, size) tuples."""
    found: List[Tuple[str, str, int]] = []
    for dirpath, dirnames, filenames in os.walk(project_dir):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for filename in filenames:
            full = Path(dirpath) / filename
            try:
                size = full.stat().st_size
            except OSError:
                continue
            rel = full.relative_to(project_dir).as_posix()
            found.append((rel, _language_of(filename), size))
    found.sort(key=lambda item: item[0])
    return found


def _count_lines(path: Path) -> Optional[int]:
    """Count lines in a text file without loading it whole. None if unreadable."""
    try:
        count = 0
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(_LINE_COUNT_CHUNK)
                if not chunk:
                    return count
                count += chunk.count(b"\n")
    except OSError:
        return None


class DiscoveryReport:
    """Generates the Forge discovery report for a project directory.

    ``domain_map`` may be the Cartographer's chart (``DomainMap`` as a
    dict, or a mapping with ``files``/``entry_points``/``test_dirs`` /
    ``dep_files``/``domains`` keys) or the Architect-style mapping of
    domain name -> ``{"owner": ..., "files": [...]}`` / list of files.
    Anything missing is re-derived by walking the project directory.
    """

    def __init__(self, project_dir: str, domain_map: Dict[str, Any]) -> None:
        self.project_dir = Path(project_dir)
        self.domain_map = domain_map if isinstance(domain_map, dict) else {}
        # Huginn scouts the land: the raw file inventory, reused by
        # every section of the report.
        self._files: Optional[List[Tuple[str, str, int]]] = None

    # -- inventory -------------------------------------------------------

    def _inventory(self) -> List[Tuple[str, str, int]]:
        if self._files is None:
            charted = self.domain_map.get("files")
            if isinstance(charted, list) and charted:
                entries: List[Tuple[str, str, int]] = []
                for item in charted:
                    if isinstance(item, dict):
                        rel = str(item.get("path", ""))
                    else:
                        rel = str(getattr(item, "path", "") or item)
                    if rel:
                        entries.append(
                            (rel, _language_of(rel), int(getattr(item, "size", 0) or 0))
                        )
                entries.sort(key=lambda e: e[0])
                self._files = entries
            else:
                self._files = _walk_files(self.project_dir)
        return self._files

    def _paths(self) -> List[str]:
        return [rel for rel, _, _ in self._inventory()]

    def entry_points(self) -> List[str]:
        charted = self.domain_map.get("entry_points")
        if isinstance(charted, list) and charted:
            return sorted(str(p) for p in charted)
        names = {"__main__.py", "cli.py", "main.py", "app.py", "manage.py",
                 "setup.py", "run.py", "server.py", "wsgi.py", "asgi.py"}
        return sorted(
            rel for rel in self._paths()
            if os.path.basename(rel) in names
        )

    def test_dirs(self) -> List[str]:
        charted = self.domain_map.get("test_dirs")
        if isinstance(charted, list) and charted:
            return sorted(str(p) for p in charted)
        seen = set()
        for rel in self._paths():
            parts = rel.split("/")
            if any(part.lower() in {"test", "tests", "spec", "specs"}
                   for part in parts[:-1]):
                seen.add("/".join(parts[:-1]))
        return sorted(seen)

    def dep_files(self) -> List[str]:
        charted = self.domain_map.get("dep_files")
        if isinstance(charted, list) and charted:
            return sorted(str(p) for p in charted)
        names = {"pyproject.toml", "setup.py", "setup.cfg", "package.json",
                 "Pipfile", "Pipfile.lock", "poetry.lock", "go.mod", "go.sum",
                 "Cargo.toml", "Cargo.lock", "pom.xml", "build.gradle",
                 "Gemfile", "Gemfile.lock", "composer.json", "composer.lock"}
        return sorted(
            rel for rel in self._paths()
            if os.path.basename(rel) in names
            or (os.path.basename(rel).startswith("requirements")
                and rel.endswith(".txt"))
        )

    def domains(self) -> Dict[str, Dict[str, Any]]:
        """Domain name -> {"owner": str, "files": [paths]}."""
        raw = self.domain_map.get("domains", self.domain_map)
        out: Dict[str, Dict[str, Any]] = {}
        if isinstance(raw, dict):
            for name, spec in raw.items():
                if isinstance(spec, dict):
                    out[str(name)] = {
                        "owner": str(spec.get("owner", "worker") or "worker"),
                        "files": [str(f) for f in spec.get("files", []) or []],
                    }
                elif isinstance(spec, (list, tuple)):
                    out[str(name)] = {
                        "owner": "worker",
                        "files": [str(f) for f in spec],
                    }
        if not out:
            # Fall back to top-level directory domains, as the
            # Cartographer does.
            buckets: Dict[str, List[str]] = {}
            for rel in self._paths():
                parts = rel.split("/")
                domain = parts[0] if len(parts) > 1 else "root"
                buckets.setdefault(domain, []).append(rel)
            out = {k: {"owner": "worker", "files": v}
                   for k, v in sorted(buckets.items())}
        return out

    def doc_inventory(self) -> Dict[str, List[str]]:
        """README files, docs/*.md, and other markdown found."""
        readmes = sorted(
            rel for rel in self._paths()
            if re.match(r"(?i)^readme(\.|$)", os.path.basename(rel))
        )
        docs_md = sorted(
            rel for rel in self._paths()
            if (rel.startswith("docs/") or rel.startswith("doc/"))
            and rel.lower().endswith((".md", ".markdown", ".rst"))
        )
        other_md = sorted(
            rel for rel in self._paths()
            if rel.lower().endswith((".md", ".markdown"))
            and rel not in readmes
            and rel not in docs_md
        )
        return {"readmes": readmes, "docs_md": docs_md, "other_md": other_md}

    def ci_configs(self) -> List[str]:
        found: List[str] = []
        for pattern in CI_GLOB_PATTERNS:
            try:
                found.extend(
                    p.relative_to(self.project_dir).as_posix()
                    for p in sorted(self.project_dir.glob(pattern))
                    if p.is_file()
                )
            except OSError:
                continue
        return sorted(set(found))

    def build_files(self) -> List[Tuple[str, str]]:
        found: List[Tuple[str, str]] = []
        for rel in self._paths():
            base = os.path.basename(rel)
            role = BUILD_FILE_ROLES.get(base)
            if role:
                found.append((rel, role))
            elif base == "Makefile":
                found.append((rel, "make targets"))
        return sorted(found)

    def test_command(self) -> str:
        """Best-effort guess at the project's test command."""
        paths = set(self._paths())
        lowers = {p.lower() for p in paths}
        if any(p.lower().startswith("package.json") for p in paths):
            return "npm test"
        if any("pytest" in p for p in ("pytest.ini", "tox.ini")) or \
                "pytest.ini" in lowers or "tox.ini" in lowers:
            return "pytest"
        if any(base in lowers for base in
               ("cargo.toml", "go.mod", "pom.xml", "build.gradle")):
            return "cargo test" if "cargo.toml" in lowers else "project build tool"
        test_dirs = self.test_dirs()
        if test_dirs or any(_looks_like_test(rel) for rel in paths):
            return "pytest"
        return "unknown — no test runner detected"

    # -- risks -----------------------------------------------------------

    def risks(self) -> List[str]:
        """Forge risks: no tests, no CI, huge files, blobs, no pinning, no README."""
        found: List[str] = []
        paths = self._paths()

        if not self.test_dirs() and not any(_looks_like_test(p) for p in paths):
            found.append("No tests detected: no test directories or test "
                         "files were found.")

        if not self.ci_configs():
            found.append("No CI config detected: no workflow or pipeline "
                         "configuration found.")

        huge: List[str] = []
        for rel, language, _size in self._inventory():
            if language == "unknown" or _extension_is_binary(rel):
                continue
            lines = _count_lines(self.project_dir / rel)
            if lines is not None and lines > _HUGE_LINES:
                huge.append(f"{rel} ({lines} lines)")
        if huge:
            found.append("Files over 5000 lines: " + "; ".join(sorted(huge)))

        blobs: List[str] = []
        for rel, _, size in self._inventory():
            if size > _BLOB_BYTES and _is_binary(self.project_dir / rel):
                blobs.append(f"{rel} ({size // 1024} KiB)")
        if blobs:
            found.append("Binary blobs over 1 MiB: " + "; ".join(sorted(blobs)))

        if not self._has_dependency_pinning():
            found.append("No dependency pinning detected: no lockfiles or "
                         "pinned versions found.")

        if not self.doc_inventory()["readmes"]:
            found.append("No README found at the project root.")
        return found

    def _has_dependency_pinning(self) -> bool:
        paths = self._paths()
        lockfiles = {
            "poetry.lock", "Pipfile.lock", "package-lock.json", "yarn.lock",
            "pnpm-lock.yaml", "Cargo.lock", "go.sum", "Gemfile.lock",
            "composer.lock",
        }
        if any(os.path.basename(p) in lockfiles for p in paths):
            return True
        for rel in paths:
            base = os.path.basename(rel)
            if base.startswith("requirements") and rel.endswith(".txt"):
                try:
                    text = (self.project_dir / rel).read_text(
                        encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for line in text.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "==" in line:
                        return True
        pyproject = self.project_dir / "pyproject.toml"
        if pyproject.is_file():
            try:
                text = pyproject.read_text(encoding="utf-8", errors="replace")
                if re.search(r'"[^"]*==[^"]*"', text) or \
                        re.search(r"'[^']*==[^']*'", text):
                    return True
            except OSError:
                pass
        return False

    # -- report ----------------------------------------------------------

    def generate(self) -> str:
        """Render the full discovery report as markdown. Never raises."""
        lines = [
            "# Discovery Report",
            "",
            f"_Surveyed {_utc_stamp()} from `{self.project_dir}`._",
            "",
            "## File inventory by language",
            "",
            "| Language | Files | Total size |",
            "| --- | --- | --- |",
        ]
        lang_files: Dict[str, int] = {}
        lang_bytes: Dict[str, int] = {}
        for _rel, language, size in self._inventory():
            lang_files[language] = lang_files.get(language, 0) + 1
            lang_bytes[language] = lang_bytes.get(language, 0) + size
        for language in sorted(lang_files):
            total = lang_bytes[language]
            human = f"{total / 1024:.1f} KiB" if total < 1024 * 1024 \
                else f"{total / 1024 / 1024:.1f} MiB"
            lines.append(f"| {language} | {lang_files[language]} | {human} |")

        lines += ["", "## Entry points", ""]
        entry = self.entry_points()
        lines += [f"- `{p}`" for p in entry] or ["_None found._"]

        lines += ["", "## Test directories", ""]
        test_dirs = self.test_dirs()
        lines += [f"- `{p}`" for p in test_dirs] or ["_None found._"]
        lines.append("")
        lines.append(f"**Test command:** `{self.test_command()}`")

        lines += ["", "## Dependency files", ""]
        deps = self.dep_files()
        lines += [f"- `{p}`" for p in deps] or ["_None found._"]

        lines += ["", "## Domains", ""]
        lines += ["| Domain | Owner | Files |", "| --- | --- | --- |"]
        for name, spec in self.domains().items():
            lines.append(f"| {name} | {spec['owner']} | {len(spec['files'])} |")

        docs = self.doc_inventory()
        lines += ["", "## Documentation inventory", ""]
        lines.append("**README:**")
        lines.append("")
        lines += [f"- `{p}`" for p in docs["readmes"]] or ["_None found._"]
        lines.append("")
        lines.append("**Docs directory:**")
        lines.append("")
        lines += [f"- `{p}`" for p in docs["docs_md"]] or ["_None found._"]
        lines.append("")
        lines.append("**Other markdown:**")
        lines.append("")
        lines += [f"- `{p}`" for p in docs["other_md"]] or ["_None found._"]

        lines += ["", "## Runtime / build state", ""]
        builds = self.build_files()
        lines += ["| File | Purpose |", "| --- | --- |"]
        lines += [f"| `{p}` | {role} |" for p, role in builds] or \
            ["| _none_ | _no build files found_ |"]
        lines.append("")
        lines.append("**CI configs:**")
        lines.append("")
        ci = self.ci_configs()
        lines += [f"- `{p}`" for p in ci] or ["_None found._"]

        lines += ["", "## Risks", ""]
        found = self.risks()
        lines += [f"- {risk}" for risk in found] or ["_No risks detected._"]
        lines.append("")
        return "\n".join(lines)

    def write(self) -> Dict[str, Any]:
        """Write ``.mythis/DISCOVERY_REPORT.md``; return report path + risks."""
        mythis = self.project_dir / MYTHIS_DIR
        mythis.mkdir(parents=True, exist_ok=True)
        report_path = mythis / REPORT_FILE
        try:
            report_path.write_text(self.generate(), encoding="utf-8")
        except OSError as exc:
            _LOG.warning("discovery report write failed: %s", exc)
        return {"report_path": str(report_path), "risks": self.risks()}


def _looks_like_test(rel_path: str) -> bool:
    """True if a path looks like a test module by name."""
    return any(pattern.search(rel_path) for pattern in _TEST_FILE_PATTERNS)
