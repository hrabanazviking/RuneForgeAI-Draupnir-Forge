"""modes_new.py — New-project mode (slice 29).

Green-field bootstrap: intent -> requirements -> constraints -> architecture
-> scaffold -> first thin vertical slice -> verify (§23/24).

The mode uses roles directly (per slice design): the Skald interprets the
goal into a Vision, the Planner contributes three real starter ForgeTasks
(scaffold, tests, docs), the mode itself creates the scaffold files with
documented content, and the Tester role verifies that the smoke test
passes before the mode reports success.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

from draupnir_forge.roles import base as role_base
from draupnir_forge.roles import planner as planner_mod
from draupnir_forge.roles import skald as skald_mod
from draupnir_forge.roles import tester as tester_mod
from draupnir_forge.roles.base import RoleContext
from draupnir_forge.tasks import ForgeTask

_LOG = logging.getLogger(__name__)

# Words skipped when coining a package name from the goal text.
_STOPWORDS = frozenset({
    "a", "an", "the", "to", "for", "of", "and", "or", "with", "that",
    "this", "build", "create", "make", "new", "app", "application",
    "project", "system", "tool", "please", "want", "need", "should",
    "will", "using", "use", "from", "into", "its", "our", "my",
})

_FALLBACK_PACKAGE = "forge_app"


def slugify_package_name(goal_text: str) -> str:
    """Coin a snake_case package name from free-form goal text.

    Takes up to three meaningful words (stopwords and filler dropped),
    falling back to ``forge_app`` when nothing usable remains.
    """
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9]*", (goal_text or "").lower())
    chosen = [w for w in words if w not in _STOPWORDS][:3]
    if not chosen:
        return _FALLBACK_PACKAGE
    name = "_".join(chosen)
    if name[0].isdigit():
        name = "pkg_" + name
    return name


def _render_vision_markdown(vision: Any, goal_text: str) -> str:
    """Render a Skald Vision as the project's VISION.md."""
    def _bullets(items: List[str]) -> str:
        items = [str(i) for i in items if str(i).strip()]
        return "\n".join(f"- {i}" for i in items) if items else "- (none)"

    return (
        "# Vision\n"
        "\n"
        f"> Raw goal: {goal_text.strip()}\n"
        "\n"
        "## Goal\n"
        "\n"
        f"{getattr(vision, 'goal', '') or goal_text.strip()}\n"
        "\n"
        "## Priorities\n"
        "\n"
        f"{_bullets(getattr(vision, 'priorities', []))}\n"
        "\n"
        "## Non-goals\n"
        "\n"
        f"{_bullets(getattr(vision, 'non_goals', []))}\n"
        "\n"
        "## Success criteria\n"
        "\n"
        f"{_bullets(getattr(vision, 'success_criteria', []))}\n"
        "\n"
        "## Open ambiguities\n"
        "\n"
        f"{_bullets(getattr(vision, 'ambiguities', []))}\n"
    )


def _init_py_content(package: str, goal_text: str) -> str:
    """Documented content for the scaffolded package ``__init__.py``."""
    return (
        f'"""{{package}} — scaffolded by Draupnir Forge new-project mode.\n'
        "\n"
        f"Goal: {goal_text.strip()}\n"
        '"""\n'
        "\n"
        "__version__ = \"0.1.0\"\n"
        "\n"
        "\n"
        "def greet(name: str = \"world\") -> str:\n"
        '    """Greet; the smoke test proves the package imports."""\n'
        "    return f\"Hello, {name}, from {__name__}!\"\n"
    ).replace("{package}", package)


def _smoke_test_content(package: str) -> str:
    """A real smoke test: imports the package and asserts real behavior."""
    return (
        '"""Smoke test for the scaffolded project (stdlib unittest)."""\n'
        "\n"
        "import unittest\n"
        "\n"
        f"import {package}\n"
        "\n"
        "\n"
        "class TestSmoke(unittest.TestCase):\n"
        '    """The thin vertical slice: package imports and behaves."""\n'
        "\n"
        "    def test_package_imports(self):\n"
        f"        self.assertTrue(hasattr({package}, '__version__'))\n"
        "\n"
        "    def test_version_looks_like_version(self):\n"
        f"        parts = {package}.__version__.split('.')\n"
        "        self.assertGreaterEqual(len(parts), 2)\n"
        '        self.assertTrue(all(p.isdigit() for p in parts))\n'
        "\n"
        "    def test_greet_returns_greeting(self):\n"
        f"        message = {package}.greet('forge')\n"
        "        self.assertIn('forge', message)\n"
        "        self.assertIn('Hello', message)\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    unittest.main()\n"
    )


def _readme_content(package: str, goal_text: str) -> str:
    """README quickstart for the scaffolded project."""
    return (
        f"# {package}\n"
        "\n"
        f"{goal_text.strip()}\n"
        "\n"
        "Scaffolded by Draupnir Forge (new-project mode).\n"
        "\n"
        "## Quickstart\n"
        "\n"
        "```sh\n"
        f"python -m unittest discover -s tests\n"
        "```\n"
        "\n"
        "```python\n"
        f"import {package}\n"
        "\n"
        f"print({package}.greet())\n"
        "```\n"
    )


class NewProjectMode:
    """Bootstrap a brand-new project from a goal sentence (§23/24)."""

    def __init__(self, project_dir: str,
                 registry: Optional[Any] = None) -> None:
        self.project_dir = os.path.abspath(project_dir)
        # Kept for orchestrator wiring; roles are used directly per slice.
        self.registry = registry or role_base.registry

    # -- internals ------------------------------------------------------

    def _write(self, rel_path: str, content: str) -> str:
        """Write ``content`` to ``rel_path`` under the project dir."""
        path = os.path.join(self.project_dir, rel_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return path

    def _starter_tasks(self, package: str) -> List[ForgeTask]:
        """Three real ForgeTasks from the Planner's starter-task specs."""
        planner = planner_mod.Planner()
        specs = planner.starter_tasks({"domains": [{"name": package}]})
        tasks: List[ForgeTask] = []
        index_to_id: Dict[int, str] = {}
        for i, spec in enumerate(specs):
            task_id = f"N-{i + 1:03d}"
            index_to_id[i] = task_id
        for i, spec in enumerate(specs):
            depends = [index_to_id[d] for d in spec.get("depends_on", [])
                       if d in index_to_id]
            tasks.append(ForgeTask(
                task_id=index_to_id[i],
                title=str(spec.get("title", "")),
                domain=str(spec.get("domain", package)),
                status="ready",
                depends_on=depends,
                goal=str(spec.get("goal", "")),
                constraints=list(spec.get("constraints", [])),
                acceptance=list(spec.get("acceptance", [])),
                verification=list(spec.get("verification", [])),
            ))
        return tasks

    def _run_scaffold(self, package: str, goal_text: str,
                      tasks: List[ForgeTask]) -> List[str]:
        """Create the scaffold files directly; return done task ids."""
        done: List[str] = []
        try:
            # Task 1 — scaffold: package skeleton.
            self._write(os.path.join("src", package, "__init__.py"),
                        _init_py_content(package, goal_text))
            self._write(os.path.join("tests", "__init__.py"),
                        '"""Test package for the scaffolded project."""\n')
            tasks[0].status = "done"
            done.append(tasks[0].task_id)

            # Task 2 — tests: the smoke test (thin vertical slice).
            self._write(os.path.join("tests", "test_smoke.py"),
                        _smoke_test_content(package))
            tasks[1].status = "done"
            done.append(tasks[1].task_id)

            # Task 3 — docs: README quickstart.
            self._write("README.md", _readme_content(package, goal_text))
            tasks[2].status = "done"
            done.append(tasks[2].task_id)
        except OSError as exc:
            _LOG.warning("new-project scaffold failed: %s", exc)
        return done

    # -- public ---------------------------------------------------------

    def run(self, goal_text: str) -> Dict[str, Any]:
        """Run the new-project bootstrap.

        Args:
            goal_text: The human's high-level goal, in their own words.

        Returns:
            ``{"project_dir", "tasks_done", "vision_path",
            "smoke_passed"}``. Never raises: failures surface as
            ``smoke_passed=False`` with whatever work completed.
        """
        goal_text = (goal_text or "").strip() or "An unnamed forge project."
        package = slugify_package_name(goal_text)

        try:
            # Skald: intent -> explicit Vision.
            vision = skald_mod.interpret(goal_text)
            vision_path = self._write(
                os.path.join(".mythis", "VISION.md"),
                _render_vision_markdown(vision, goal_text))

            # Planner: three real starter ForgeTasks.
            tasks = self._starter_tasks(package)

            # The mode executes the thin vertical slice directly.
            tasks_done = self._run_scaffold(package, goal_text, tasks)

            # Tester: verify the smoke test passes before claiming success.
            smoke_passed = False
            try:
                ctx = RoleContext(project_dir=self.project_dir)
                result = tester_mod.Tester().run(ctx)
                test_result = (result.artifacts or {}).get("test_result")
                smoke_passed = bool(
                    result.ok and test_result is not None
                    and test_result.passed > 0
                    and test_result.failed == 0
                    and test_result.errors == 0)
            except Exception as exc:  # verification must not crash the mode
                _LOG.warning("new-project verification failed: %s", exc)

            return {
                "project_dir": self.project_dir,
                "tasks_done": tasks_done,
                "vision_path": vision_path,
                "smoke_passed": smoke_passed,
            }
        except Exception as exc:  # the Forge must not fall mid-swing
            _LOG.warning("new-project mode failed: %s", exc)
            return {
                "project_dir": self.project_dir,
                "tasks_done": [],
                "vision_path": "",
                "smoke_passed": False,
            }
