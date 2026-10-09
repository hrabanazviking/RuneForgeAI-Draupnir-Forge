"""modes_existing.py — Existing-repo mode (slice 30).

SCAN -> MAP -> domain model -> detect tests -> detect architecture ->
docs-vs-code diff -> risks -> change roadmap -> (execute later, §22).

The mode's law is **"Map before modifying"**: no worker task may be
created before the domain map exists. :meth:`ExistingRepoMode.ready_for_worker`
returns False until ``.mythis/domain_map.json`` is on disk, and
:meth:`build_roadmap` refuses to run without it.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from draupnir_forge.roles import cartographer as cartographer_mod
from draupnir_forge.roles import skald as skald_mod
from draupnir_forge.roles import tester as tester_mod
from draupnir_forge.tasks import ForgeTask

_LOG = logging.getLogger(__name__)

# Files that count as CI configuration for the "no CI" risk.
CI_MARKERS = (
    os.path.join(".github", "workflows"),
    ".gitlab-ci.yml",
    os.path.join(".circleci", "config.yml"),
    "tox.ini",
    "Jenkinsfile",
    "azure-pipelines.yml",
    ".travis.yml",
)

# A file with more than this many lines is a "huge file" risk.
HUGE_FILE_LINES = 5000

# Doc file candidates inventoried during discovery.
_DOC_ROOT_NAMES = ("README.md", "readme.md", "Readme.md")
_DOC_DIR_NAMES = ("docs", "doc", "documentation")


class NotMappedError(RuntimeError):
    """Raised when worker/roadmap work is attempted before mapping."""


class ExistingRepoMode:
    """Map an existing repository, then plan changes against a goal."""

    def __init__(self, project_dir: str) -> None:
        self.project_dir = os.path.abspath(project_dir)
        self.domain_map_path = os.path.join(self.project_dir, ".mythis",
                                            "domain_map.json")
        self.roadmap_path = os.path.join(self.project_dir, ".mythis",
                                         "ROADMAP.md")
        self._domain_map: Optional[Dict[str, Any]] = None
        self._doc_files: List[str] = []
        self._risks: List[Dict[str, str]] = []

    # -- discovery ------------------------------------------------------

    def map_repository(self) -> Dict[str, Any]:
        """SCAN -> MAP: chart the repo and persist the domain map."""
        chart = cartographer_mod.map_repo(self.project_dir)
        self._domain_map = asdict(chart)
        os.makedirs(os.path.dirname(self.domain_map_path), exist_ok=True)
        with open(self.domain_map_path, "w", encoding="utf-8") as handle:
            json.dump(self._domain_map, handle, indent=2)
        return self._domain_map

    def ready_for_worker(self) -> bool:
        """The map-before-modifying law: no map file, no worker tasks."""
        return os.path.isfile(self.domain_map_path)

    def detect_tests(self) -> Optional[List[str]]:
        """Detect the command that runs this repo's test suite."""
        return tester_mod.detect_command(self.project_dir)

    def inventory_docs(self) -> List[str]:
        """Inventory human docs: README + docs/*.md (repo-relative)."""
        found: List[str] = []
        for name in _DOC_ROOT_NAMES:
            candidate = os.path.join(self.project_dir, name)
            if os.path.isfile(candidate):
                found.append(name)
        for dirname in _DOC_DIR_NAMES:
            docdir = os.path.join(self.project_dir, dirname)
            if not os.path.isdir(docdir):
                continue
            for dirpath, _dirnames, filenames in os.walk(docdir):
                for filename in filenames:
                    if filename.lower().endswith(".md"):
                        full = os.path.join(dirpath, filename)
                        found.append(os.path.relpath(full, self.project_dir)
                                     .replace(os.sep, "/"))
        self._doc_files = sorted(set(found))
        return self._doc_files

    # -- analysis -------------------------------------------------------

    @staticmethod
    def _modules_mentioned_in_docs(doc_texts: List[str]) -> List[str]:
        """Find .py module paths and dotted module names named in docs."""
        mentioned: set = set()
        path_re = re.compile(r"([A-Za-z0-9_./-]+\.py)")
        dotted_re = re.compile(
            r"`([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)+)`")
        for text in doc_texts:
            for match in path_re.findall(text):
                cleaned = match.strip("./")
                if cleaned:
                    mentioned.add(cleaned)
            for dotted in dotted_re.findall(text):
                mentioned.add(dotted.replace(".", "/") + ".py")
        return sorted(mentioned)

    def compare_docs_to_code(self) -> Dict[str, List[str]]:
        """Docs-vs-code diff: doc-mentioned modules missing from the repo,
        plus code modules no doc mentions."""
        if self._domain_map is None:
            raise NotMappedError("map_repository() must run first")
        doc_texts: List[str] = []
        for rel in self._doc_files:
            try:
                with open(os.path.join(self.project_dir, rel), "r",
                          encoding="utf-8", errors="replace") as handle:
                    doc_texts.append(handle.read())
            except OSError as exc:
                _LOG.warning("existing-repo: cannot read doc %s: %s",
                             rel, exc)
        code_modules = {
            f["path"] for f in self._domain_map.get("files", [])
            if f.get("language") == "python"
        }
        mentioned = set(self._modules_mentioned_in_docs(doc_texts))
        mentioned_missing = sorted(
            m for m in mentioned if m not in code_modules)
        mentioned_basenames = {m.rsplit("/", 1)[-1] for m in mentioned}
        undocumented = sorted(
            p for p in code_modules
            if p not in mentioned
            and p.rsplit("/", 1)[-1] not in mentioned_basenames
        )
        return {
            "mentioned_missing": mentioned_missing,
            "undocumented_modules": undocumented,
        }

    def identify_risks(self) -> List[Dict[str, str]]:
        """Structural risks: no tests? no CI? huge files? binary blobs?"""
        if self._domain_map is None:
            raise NotMappedError("map_repository() must run first")
        risks: List[Dict[str, str]] = []

        test_dirs = self._domain_map.get("test_dirs", [])
        test_files = [
            f["path"] for f in self._domain_map.get("files", [])
            if f.get("language") == "python"
            and (os.path.basename(f["path"]).startswith("test_")
                 or os.path.basename(f["path"]).endswith("_test.py"))
        ]
        if not test_dirs and not test_files:
            risks.append({
                "id": "no_tests",
                "severity": "high",
                "detail": "no test directories and no test files detected",
            })

        if not any(os.path.exists(os.path.join(self.project_dir, marker))
                   for marker in CI_MARKERS):
            risks.append({
                "id": "no_ci",
                "severity": "medium",
                "detail": "no CI configuration found "
                          "(.github/workflows, .gitlab-ci.yml, ...)",
            })

        for info in self._domain_map.get("files", []):
            path = info.get("path", "")
            full = os.path.join(self.project_dir, path)
            try:
                with open(full, "rb") as handle:
                    lines = sum(1 for _ in handle)
            except OSError:
                continue
            if lines > HUGE_FILE_LINES:
                risks.append({
                    "id": "huge_file",
                    "severity": "medium",
                    "detail": f"{path} has {lines} lines "
                              f"(>{HUGE_FILE_LINES}); hard to review safely",
                })

        blobs: List[str] = []
        for dirpath, dirnames, filenames in os.walk(self.project_dir):
            dirnames[:] = [d for d in dirnames
                           if d not in cartographer_mod.SKIP_DIRS
                           and d != ".mythis"]
            for filename in filenames:
                if os.path.splitext(filename)[1].lower() \
                        in cartographer_mod.BINARY_EXTENSIONS:
                    rel = os.path.relpath(os.path.join(dirpath, filename),
                                          self.project_dir)
                    blobs.append(rel.replace(os.sep, "/"))
        for blob in sorted(blobs):
            risks.append({
                "id": "binary_blob",
                "severity": "low",
                "detail": f"binary blob in repo: {blob}",
            })

        self._risks = risks
        return risks

    # -- planning -------------------------------------------------------

    def _roadmap_specs(self, goal_text: str) -> List[Dict[str, Any]]:
        """Task specs derived from the goal plus the discovered risks."""
        specs: List[Dict[str, Any]] = [{
            "title": f"Pursue goal: {goal_text[:80]}",
            "goal": goal_text,
            "acceptance": ["goal's acceptance criteria are defined and met"],
        }]
        mitigations = {
            "no_tests": ("Add a test harness",
                         "Introduce a tests/ directory with a first passing "
                         "test so future changes are verifiable."),
            "no_ci": ("Add CI configuration",
                      "Add a CI workflow that runs the test suite on every "
                      "push so regressions are caught early."),
            "huge_file": ("Break down huge files",
                          "Split files over 5000 lines into smaller, "
                          "focused modules behind stable interfaces."),
            "binary_blob": ("Audit binary blobs",
                            "Confirm each binary blob is intentional and "
                            "documented; remove or externalize the rest."),
        }
        for risk in self._risks:
            title, goal = mitigations.get(risk["id"], (None, None))
            if title is None:
                continue
            specs.append({
                "title": f"Mitigate risk: {title} [{risk['detail'][:60]}]",
                "goal": goal,
                "acceptance": [f"risk '{risk['id']}' is resolved or tracked"],
            })
        return specs

    def build_roadmap(self, goal_text: str) -> List[ForgeTask]:
        """Turn goal + risks into real ForgeTasks and write ROADMAP.md.

        Raises:
            NotMappedError: when the domain map does not exist yet —
                worker-facing tasks must never be created pre-map.
        """
        if not self.ready_for_worker():
            raise NotMappedError(
                "map before modifying: run map_repository() first")
        specs = self._roadmap_specs(goal_text)
        tasks = [
            ForgeTask(
                task_id=f"E-{i + 1:03d}",
                title=spec["title"],
                domain="existing-repo",
                status="ready",
                depends_on=[f"E-{i:03d}"] if i > 0 else [],
                goal=spec["goal"],
                acceptance=list(spec.get("acceptance", [])),
                verification=["change verified against acceptance criteria"],
            )
            for i, spec in enumerate(specs)
        ]
        lines = ["# Change Roadmap", "",
                 f"Goal: {goal_text.strip()}", "",
                 "## Tasks", ""]
        for task in tasks:
            deps = (f" (after {', '.join(task.depends_on)})"
                    if task.depends_on else "")
            lines.append(f"- **{task.task_id}** {task.title}{deps}")
            lines.append(f"  - acceptance: {'; '.join(task.acceptance)}")
        os.makedirs(os.path.dirname(self.roadmap_path), exist_ok=True)
        with open(self.roadmap_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        return tasks

    # -- public ---------------------------------------------------------

    def run(self, goal_text: str) -> Dict[str, Any]:
        """Run the existing-repo discovery sequence (§22).

        Returns ``{"domain_map", "risks", "doc_gaps", "roadmap_path",
        "ready"}``. Never raises: failures surface as ``ready=False``.
        """
        goal_text = (goal_text or "").strip() or "Understand this repository."
        try:
            # The Skald hears the goal first, so the roadmap serves intent.
            skald_mod.interpret(goal_text)

            domain_map = self.map_repository()          # SCAN -> MAP
            self.detect_tests()                          # test detection
            self.inventory_docs()                        # doc inventory
            doc_gaps = self.compare_docs_to_code()       # docs-vs-code diff
            risks = self.identify_risks()                # structural risks
            self.build_roadmap(goal_text)                # change roadmap

            return {
                "domain_map": domain_map,
                "risks": risks,
                "doc_gaps": doc_gaps,
                "roadmap_path": self.roadmap_path,
                "ready": True,
            }
        except Exception as exc:  # the Forge must not fall mid-swing
            _LOG.warning("existing-repo mode failed: %s", exc)
            return {
                "domain_map": self._domain_map or {},
                "risks": self._risks,
                "doc_gaps": {"mentioned_missing": [],
                             "undocumented_modules": []},
                "roadmap_path": self.roadmap_path,
                "ready": False,
            }
