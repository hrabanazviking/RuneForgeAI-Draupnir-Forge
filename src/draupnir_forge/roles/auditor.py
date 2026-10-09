"""auditor.py — Auditor role (slice 15).

The Auditor is the Forge's adversarial reviewer: it reads a set of files
and reports findings — hardcoded secrets, bare ``except:``, stray
``print()`` calls, TODO markers, excessive nesting, duplicated blocks,
and architecture drift — each with a severity. Findings are advisory;
the Orchestrator decides what they mean. The Auditor therefore always
returns ``ok=True`` (unless its own context was unusable).

Check behaviour is tuned by ``data/audit_checks.yaml``; the Python here
implements the checks. If the YAML is missing the Auditor falls back to
built-in defaults and keeps working.
"""

from __future__ import annotations

import ast
import copy
import hashlib
import io
import json
import os
import re
import tokenize
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import yaml

from .base import Role, RoleContext, RoleResult, register_role


_SEVERITIES = ("high", "medium", "low")

# Control-flow nodes that deepen the nesting count.
_NEST_NODES = (
    ast.If, ast.For, ast.AsyncFor, ast.While,
    ast.With, ast.AsyncWith, ast.Try,
)
if hasattr(ast, "TryStar"):  # Python 3.11+
    _NEST_NODES = _NEST_NODES + (ast.TryStar,)  # type: ignore[assignment]
# Definitions also deepen nesting: code buried in nested defs is just as lost.
_NEST_NODES = _NEST_NODES + (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)


@dataclass
class Finding:
    """One advisory finding from an audit."""

    severity: str            # "high" | "medium" | "low"
    check: str               # check key, e.g. "hardcoded_secrets"
    file: str                # path relative to project_dir
    line: Optional[int]      # 1-based line number, None if not line-specific
    message: str

    def __post_init__(self) -> None:
        if self.severity not in _SEVERITIES:
            raise ValueError(
                f"severity must be one of {_SEVERITIES}, got {self.severity!r}")

    def to_dict(self) -> Dict[str, Any]:
        """JSON-safe form for RoleResult artifacts."""
        return {
            "severity": self.severity,
            "check": self.check,
            "file": self.file,
            "line": self.line,
            "message": self.message,
        }


# ---------------------------------------------------------------------------
# Check configuration (YAML-driven, with safe fallbacks)
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG: Dict[str, Any] = {
    "checks": {
        "hardcoded_secrets": {
            "enabled": True, "severity": "high",
            "patterns": [
                r"(?i)\b(api[_-]?key|api[_-]?secret|secret[_-]?key"
                r"|client[_-]?secret)\b\s*[:=]\s*['\"][^'\"]{3,}['\"]",
                r"(?i)\b(passw(or)?d|passwd|pwd)\b\s*[:=]\s*['\"][^'\"]+['\"]",
                r"(?i)\b(auth[_-]?token|access[_-]?token|bearer[_-]?token)"
                r"\b\s*[:=]\s*['\"][^'\"]{3,}['\"]",
            ],
        },
        "bare_except": {"enabled": True, "severity": "medium"},
        "print_statements": {"enabled": True, "severity": "medium",
                             "allowed_files": ["cli.py"]},
        "todo_markers": {"enabled": True, "severity": "low",
                         "markers": ["TODO", "FIXME", "XXX"]},
        "nesting_depth": {"enabled": True, "severity": "medium",
                          "max_depth": 4},
        "duplicated_blocks": {"enabled": True, "severity": "medium",
                              "min_lines": 6},
        "architecture_drift": {"enabled": True, "severity": "medium",
                               "architecture_file": "architecture.json"},
    }
}


def _candidate_config_paths(project_dir: Optional[str]) -> List[str]:
    """Where audit_checks.yaml might live, most specific first."""
    paths: List[str] = []
    if project_dir:
        paths.append(os.path.join(project_dir, "data", "audit_checks.yaml"))
    here = os.path.dirname(os.path.abspath(__file__))
    paths.append(os.path.normpath(
        os.path.join(here, "..", "..", "..", "data", "audit_checks.yaml")))
    paths.append(os.path.join(os.getcwd(), "data", "audit_checks.yaml"))
    return paths


def load_check_config(project_dir: Optional[str] = None) -> Dict[str, Any]:
    """Load audit check settings from data/audit_checks.yaml.

    Falls back to built-in defaults when the file is missing or broken,
    so the Auditor never fails for want of configuration.
    """
    for path in _candidate_config_paths(project_dir):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(data, dict):
            continue
        merged = copy.deepcopy(_DEFAULT_CONFIG)
        for key, val in data.get("checks", {}).items():
            if isinstance(val, dict) and key in merged["checks"]:
                merged["checks"][key].update(val)
            else:
                merged["checks"][key] = val
        return merged
    return copy.deepcopy(_DEFAULT_CONFIG)


def _check_cfg(config: Dict[str, Any], key: str) -> Dict[str, Any]:
    cfg = config.get("checks", {}).get(key, {})
    return cfg if isinstance(cfg, dict) else {}


def _enabled(config: Dict[str, Any], key: str) -> bool:
    return bool(_check_cfg(config, key).get("enabled", True))


def _severity(config: Dict[str, Any], key: str) -> str:
    sev = str(_check_cfg(config, key).get("severity", "medium")).lower()
    return sev if sev in _SEVERITIES else "medium"


# ---------------------------------------------------------------------------
# Small parsing helpers
# ---------------------------------------------------------------------------

def _read_source(abs_path: str) -> Optional[str]:
    """Read a file as text; None when unreadable (binary, missing, ...)."""
    try:
        with open(abs_path, "r", encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def _tokens(source: str) -> List[tokenize.TokenInfo]:
    """Tokenize source; empty list when tokenization fails."""
    try:
        return list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return []


def _parse_ast(source: str) -> Optional[ast.AST]:
    """Parse source to an AST; None when it does not parse."""
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError):
        return None


def _rel(project_dir: str, abs_path: str) -> str:
    rel = os.path.relpath(abs_path, project_dir)
    return rel.replace(os.sep, "/")


# ---------------------------------------------------------------------------
# Per-file checks
# ---------------------------------------------------------------------------

def check_hardcoded_secrets(rel_path: str, source: str,
                            config: Dict[str, Any]) -> List[Finding]:
    """Flag literal secret values assigned in code (regex, per line)."""
    key = "hardcoded_secrets"
    findings: List[Finding] = []
    if not _enabled(config, key):
        return findings
    patterns = []
    for pat in _check_cfg(config, key).get("patterns", []):
        try:
            patterns.append(re.compile(str(pat)))
        except re.error:
            continue  # a broken pattern must not sink the audit
    for lineno, line in enumerate(source.splitlines(), start=1):
        for pat in patterns:
            if pat.search(line):
                findings.append(Finding(
                    severity=_severity(config, key), check=key,
                    file=rel_path, line=lineno,
                    message="possible hardcoded secret assigned in code"))
                break
    return findings


def check_bare_except(rel_path: str, source: str,
                      config: Dict[str, Any]) -> List[Finding]:
    """Flag bare `except:` clauses (token-based, immune to strings)."""
    key = "bare_except"
    findings: List[Finding] = []
    if not _enabled(config, key):
        return findings
    toks = _tokens(source)
    if not toks:  # fall back to a careful line scan
        for lineno, line in enumerate(source.splitlines(), start=1):
            if re.match(r"^\s*except\s*:\s*(#.*)?$", line):
                findings.append(Finding(
                    severity=_severity(config, key), check=key,
                    file=rel_path, line=lineno,
                    message="bare 'except:' swallows all errors"))
        return findings
    for i, tok in enumerate(toks):
        if tok.type == tokenize.NAME and tok.string == "except":
            nxt = next((t for t in toks[i + 1:]
                        if t.type not in (tokenize.NL, tokenize.NEWLINE,
                                          tokenize.COMMENT,
                                          tokenize.INDENT,
                                          tokenize.DEDENT)),
                       None)
            if nxt is not None and nxt.type == tokenize.OP \
                    and nxt.string == ":":
                findings.append(Finding(
                    severity=_severity(config, key), check=key,
                    file=rel_path, line=tok.start[0],
                    message="bare 'except:' swallows all errors"))
    return findings


def check_print_statements(rel_path: str, source: str,
                           config: Dict[str, Any]) -> List[Finding]:
    """Flag print() in library code; cli.py entry points are exempt."""
    key = "print_statements"
    findings: List[Finding] = []
    if not _enabled(config, key):
        return findings
    allowed = {str(n).lower()
               for n in _check_cfg(config, key).get("allowed_files", [])}
    if os.path.basename(rel_path).lower() in allowed:
        return findings
    toks = _tokens(source)
    for i, tok in enumerate(toks):
        if tok.type == tokenize.NAME and tok.string == "print":
            nxt = next((t for t in toks[i + 1:]
                        if t.type not in (tokenize.NL, tokenize.NEWLINE,
                                          tokenize.COMMENT)),
                       None)
            if nxt is not None and nxt.type == tokenize.OP \
                    and nxt.string == "(":
                findings.append(Finding(
                    severity=_severity(config, key), check=key,
                    file=rel_path, line=tok.start[0],
                    message="print() in library code; use logging/events"))
    return findings


def check_todo_markers(rel_path: str, source: str,
                       config: Dict[str, Any]) -> List[Finding]:
    """Flag TODO/FIXME/XXX markers in comments."""
    key = "todo_markers"
    findings: List[Finding] = []
    if not _enabled(config, key):
        return findings
    markers = [str(m) for m in _check_cfg(config, key).get("markers", [])]
    if not markers:
        return findings
    marker_re = re.compile(r"\b(" + "|".join(re.escape(m)
                                             for m in markers) + r")\b")
    for tok in _tokens(source):
        if tok.type == tokenize.COMMENT:
            match = marker_re.search(tok.string)
            if match:
                findings.append(Finding(
                    severity=_severity(config, key), check=key,
                    file=rel_path, line=tok.start[0],
                    message=f"{match.group(1)} marker left in comment"))
    return findings


def _nesting_violations(tree: ast.AST, max_depth: int,
                        rel_path: str, config: Dict[str, Any],
                        key: str) -> List[Finding]:
    """Walk the AST tracking control-flow depth; flag depth > max."""
    findings: List[Finding] = []

    def visit(node: ast.AST, depth: int, flagged: bool) -> None:
        if isinstance(node, _NEST_NODES):
            depth += 1
            if depth > max_depth and not flagged:
                findings.append(Finding(
                    severity=_severity(config, key), check=key,
                    file=rel_path, line=getattr(node, "lineno", None),
                    message=(f"nesting depth {depth} exceeds maximum "
                             f"{max_depth}")))
                flagged = True
        for child in ast.iter_child_nodes(node):
            visit(child, depth, flagged)

    visit(tree, 0, False)
    return findings


def check_nesting_depth(rel_path: str, source: str,
                        config: Dict[str, Any]) -> List[Finding]:
    """Flag control-flow nesting deeper than the configured maximum."""
    key = "nesting_depth"
    if not _enabled(config, key):
        return []
    tree = _parse_ast(source)
    if tree is None:
        return []  # unparseable files are not the Auditor's prey
    max_depth = _check_cfg(config, key).get("max_depth", 4)
    try:
        max_depth = int(max_depth)
    except (TypeError, ValueError):
        max_depth = 4
    return _nesting_violations(tree, max_depth, rel_path, config, key)


# ---------------------------------------------------------------------------
# Cross-file checks
# ---------------------------------------------------------------------------

def _normalized_lines(source: str) -> List[Tuple[int, str]]:
    """(lineno, stripped line) pairs, skipping blanks and comments."""
    out: List[Tuple[int, str]] = []
    for lineno, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append((lineno, stripped))
    return out


def check_duplicated_blocks(files: Sequence[Tuple[str, str]],
                            config: Dict[str, Any]) -> List[Finding]:
    """Flag verbatim code blocks of min_lines+ lines repeated (hash-based).

    ``files`` is a sequence of (rel_path, source) pairs.
    """
    key = "duplicated_blocks"
    if not _enabled(config, key):
        return []
    min_lines = _check_cfg(config, key).get("min_lines", 6)
    try:
        min_lines = int(min_lines)
    except (TypeError, ValueError):
        min_lines = 6
    if min_lines < 2:
        min_lines = 2
    seen: Dict[str, List[Tuple[str, int]]] = {}
    order: List[str] = []
    for rel_path, source in files:
        norm = _normalized_lines(source)
        for i in range(len(norm) - min_lines + 1):
            window = norm[i:i + min_lines]
            digest = hashlib.sha256(
                "\n".join(text for _, text in window).encode("utf-8")
            ).hexdigest()
            if digest not in seen:
                seen[digest] = []
                order.append(digest)
            seen[digest].append((rel_path, window[0][0]))
    findings: List[Finding] = []
    for digest in order:
        locs = seen[digest]
        # Deduplicate identical (file, line) hits; need 2+ distinct spots.
        unique = sorted(set(locs))
        if len(unique) < 2:
            continue
        first_file, first_line = unique[0]
        for rel_path, lineno in unique[1:]:
            findings.append(Finding(
                severity=_severity(config, key), check=key,
                file=rel_path, line=lineno,
                message=(f"duplicated {min_lines}+ line block also found at "
                         f"{first_file}:{first_line}")))
    return findings


# ---------------------------------------------------------------------------
# Architecture drift
# ---------------------------------------------------------------------------

def _load_architecture(project_dir: str,
                       config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Load architecture.json; None when absent or invalid (graceful skip)."""
    key = "architecture_drift"
    filename = str(_check_cfg(config, key).get("architecture_file",
                                              "architecture.json"))
    path = os.path.join(project_dir, filename)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("domains"), dict):
        return None
    return data


def _longest_path_prefix(rel_path: str, prefixes: Sequence[str]) -> str:
    """Longest directory prefix of rel_path found in prefixes, else ''."""
    best = ""
    for prefix in prefixes:
        norm = str(prefix).rstrip("/") + "/"
        if (rel_path + "/").startswith(norm) and len(norm) > len(best):
            best = norm
    return best


def _longest_module_prefix(dotted: str, prefixes: Sequence[str]) -> str:
    """Longest dotted-module prefix of `dotted` found in prefixes, else ''."""
    best = ""
    for prefix in prefixes:
        prefix = str(prefix).strip(".")
        if (dotted == prefix or dotted.startswith(prefix + ".")) \
                and len(prefix) > len(best):
            best = prefix
    return best


def _domain_for_file(rel_path: str, domains: Dict[str, Any]) -> Optional[str]:
    best_domain, best_len = None, -1
    for name, spec in domains.items():
        if not isinstance(spec, dict):
            continue
        match = _longest_path_prefix(rel_path, spec.get("paths", []) or [])
        if match and len(match) > best_len:
            best_domain, best_len = name, len(match)
    return best_domain


def _domain_for_module(dotted: str, domains: Dict[str, Any]) -> Optional[str]:
    best_domain, best_len = None, -1
    for name, spec in domains.items():
        if not isinstance(spec, dict):
            continue
        match = _longest_module_prefix(dotted, spec.get("modules", []) or [])
        if match and len(match) > best_len:
            best_domain, best_len = name, len(match)
    return best_domain


def _imported_modules(tree: ast.AST) -> List[Tuple[str, Optional[int]]]:
    """(dotted module, lineno) for absolute imports in the tree."""
    imports: List[Tuple[str, Optional[int]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, getattr(node, "lineno", None)))
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative imports are intra-domain by definition
                continue
            if node.module:
                imports.append((node.module, getattr(node, "lineno", None)))
    return imports


def check_architecture_drift(rel_path: str, source: str, project_dir: str,
                             architecture: Optional[Dict[str, Any]],
                             config: Dict[str, Any]) -> List[Finding]:
    """Flag imports from a different domain than the file's own domain."""
    key = "architecture_drift"
    if not _enabled(config, key) or architecture is None:
        return []  # no map of the realms: the check stands down gracefully
    domains = architecture["domains"]
    file_domain = _domain_for_file(rel_path, domains)
    if file_domain is None:
        return []
    tree = _parse_ast(source)
    if tree is None:
        return []
    findings: List[Finding] = []
    seen: Set[Tuple[str, Optional[int]]] = set()
    for dotted, lineno in _imported_modules(tree):
        if (dotted, lineno) in seen:
            continue
        seen.add((dotted, lineno))
        import_domain = _domain_for_module(dotted, domains)
        if import_domain is None or import_domain == file_domain:
            continue
        findings.append(Finding(
            severity=_severity(config, key), check=key,
            file=rel_path, line=lineno,
            message=(f"import of '{dotted}' crosses domain boundary: "
                     f"file is in '{file_domain}', import in "
                     f"'{import_domain}'")))
    return findings


# ---------------------------------------------------------------------------
# audit() entry point
# ---------------------------------------------------------------------------

def _iter_py_files(project_dir: str,
                   files: Sequence[str]) -> List[Tuple[str, str]]:
    """Resolve candidate paths to (rel_path, abs_path) for .py files."""
    resolved: List[Tuple[str, str]] = []
    for entry in files:
        abs_path = (entry if os.path.isabs(entry)
                    else os.path.join(project_dir, entry))
        abs_path = os.path.normpath(abs_path)
        if not abs_path.endswith(".py"):
            continue
        if not os.path.isfile(abs_path):
            continue
        # Refuse to audit outside the project (same oath as the worker).
        base = os.path.abspath(project_dir)
        if os.path.commonpath([base, os.path.abspath(abs_path)]) != base:
            continue
        resolved.append((_rel(project_dir, abs_path), abs_path))
    return resolved


def audit(files: List[str], project_dir: str,
          config: Optional[Dict[str, Any]] = None) -> List[Finding]:
    """Audit Python files and return advisory findings.

    ``files`` are paths relative to project_dir (absolute also accepted).
    """
    config = config if config is not None else load_check_config(project_dir)
    resolved = _iter_py_files(project_dir, files)
    sources: List[Tuple[str, str]] = []
    for rel_path, abs_path in resolved:
        source = _read_source(abs_path)
        if source is not None:
            sources.append((rel_path, source))

    architecture: Optional[Dict[str, Any]] = None
    if _enabled(config, "architecture_drift"):
        architecture = _load_architecture(project_dir, config)

    findings: List[Finding] = []
    for rel_path, source in sources:
        findings.extend(check_hardcoded_secrets(rel_path, source, config))
        findings.extend(check_bare_except(rel_path, source, config))
        findings.extend(check_print_statements(rel_path, source, config))
        findings.extend(check_todo_markers(rel_path, source, config))
        findings.extend(check_nesting_depth(rel_path, source, config))
        findings.extend(check_architecture_drift(rel_path, source,
                                                 project_dir, architecture,
                                                 config))
    findings.extend(check_duplicated_blocks(
        [(rel, src) for rel, src in sources], config))
    findings.sort(key=lambda f: (f.file, f.line or 0, f.check))
    return findings


# ---------------------------------------------------------------------------
# The role
# ---------------------------------------------------------------------------

@register_role
class Auditor(Role):
    """Adversarial reviewer: reports advisory findings, never blocks."""

    name = "auditor"
    purpose = ("Reviews changed files against the audit checklist "
               "(secrets, bare except, print, TODOs, nesting, duplication, "
               "architecture drift) and returns advisory findings.")

    def run(self, ctx: RoleContext) -> RoleResult:
        """Audit the files; findings are advisory so ok is always True."""
        try:
            return self._execute(ctx)
        except Exception as exc:  # the watchman never abandons the wall
            return RoleResult(
                ok=True,
                summary=f"audit incomplete due to internal error: {exc}",
                artifacts={"findings": []})

    def _execute(self, ctx: RoleContext) -> RoleResult:
        project_dir = os.path.abspath(ctx.project_dir)
        files = ctx.artifacts.get("changed_files")
        if not files:
            # No diff to review: sweep every Python file under src/.
            src_root = os.path.join(project_dir, "src")
            files = []
            if os.path.isdir(src_root):
                for root, _dirs, names in os.walk(src_root):
                    for name in names:
                        if name.endswith(".py"):
                            files.append(os.path.join(root, name))
        if not isinstance(files, list):
            files = []
        config = load_check_config(project_dir)
        findings = audit([str(f) for f in files], project_dir, config)
        payload = [f.to_dict() for f in findings]
        by_severity: Dict[str, int] = {}
        for f in findings:
            by_severity[f.severity] = by_severity.get(f.severity, 0) + 1
        self.emit(ctx, "auditor.findings_reported",
                  {"count": len(findings), "by_severity": by_severity})
        return RoleResult(
            ok=True,
            summary=(f"audited {len(files)} file(s): {len(findings)} "
                     f"finding(s) {by_severity}"),
            artifacts={"findings": payload})
