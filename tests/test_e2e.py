"""Slice 47 acceptance test: end-to-end forge loop on a fixture repo.

Spins a tiny fixture repository, runs the REAL Orchestrator with the
REAL roles through new-project mode to PROJECT_COMPLETE, and asserts
the loop's real artifacts: checkpoints, events, docs, and green tests.

The hand-written patch stands in for model output (seeded into the
orchestrator's artifacts, exactly where a model-written patch would
land — the same technique as the slice-46 dogfood). Everything
downstream of the seed — patch apply, audit, test, verify, document,
checkpoint, final report — runs for real.

Acceptance: green, well under 120s.
"""

from __future__ import annotations

import difflib
import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from draupnir_forge.orchestrator import Orchestrator


FIXTURE_HELLO = '''\
def greet():
    return "hello"
'''

FIXTURE_TEST = '''\
"""Fixture test: self-contained, inserts its own src dir on sys.path."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from hello import greet


class TestHello(unittest.TestCase):
    def test_greet(self):
        self.assertEqual(greet(), "hello")


if __name__ == "__main__":
    unittest.main()
'''

# The "model output" stand-in: a unified diff adding farewell().
# Generated with difflib so the hunk headers are always correct.
def _make_patch() -> str:
    new_hello = (
        'def greet():\n'
        '    return "hello"\n'
        '\n'
        '\n'
        'def farewell():\n'
        '    return "farewell"\n'
    )
    new_test = FIXTURE_TEST.replace(
        "from hello import greet",
        "from hello import farewell, greet",
    ).replace(
        '        self.assertEqual(greet(), "hello")\n',
        '        self.assertEqual(greet(), "hello")\n'
        '\n'
        '    def test_farewell(self):\n'
        '        self.assertEqual(farewell(), "farewell")\n',
    )
    parts = []
    for old, new, path in (
        (FIXTURE_HELLO, new_hello, "src/hello.py"),
        (FIXTURE_TEST, new_test, "tests/test_hello.py"),
    ):
        diff = difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
        parts.append("".join(diff))
    return "".join(parts)


FIXTURE_PATCH = _make_patch()

TASK_SPECS = [
    {
        "title": "Add farewell() to the fixture greeter",
        "domain": "src",
        "depends_on": [],
        "goal": (
            "Add a farewell() function returning 'farewell' to "
            "src/hello.py and cover it with a unit test."
        ),
        "acceptance": [
            "src/hello.py defines farewell() returning 'farewell'.",
            "tests/test_hello.py covers farewell().",
            "The fixture suite stays green.",
        ],
        "verification": ["python -m unittest discover -s tests -q passes."],
    }
]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=str(repo), check=True,
        capture_output=True, text=True, timeout=60,
    )


class TestEndToEnd(unittest.TestCase):
    def _build_fixture(self, root: Path) -> None:
        src = root / "src"
        tests = root / "tests"
        src.mkdir(parents=True)
        tests.mkdir(parents=True)
        (src / "hello.py").write_text(FIXTURE_HELLO, encoding="utf-8")
        (tests / "test_hello.py").write_text(FIXTURE_TEST, encoding="utf-8")
        _git(root, "init", "-q")
        _git(root, "config", "user.email", "e2e@nine.worlds")
        _git(root, "config", "user.name", "e2e")
        _git(root, "add", "-A")
        _git(root, "commit", "-qm", "fixture baseline")

    def test_forge_loop_to_project_complete(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_fixture(root)

            orch = Orchestrator(project_dir=str(root))
            # Seeds stand in for model output (the slice-46 dogfood
            # technique). _reset_run_state() clears artifacts at the
            # start of every run(), so they are installed right after
            # the reset. Everything downstream runs for real.
            seeds = {
                "task_specs": TASK_SPECS,
                "patch": FIXTURE_PATCH,
                "commands": [
                    "python3 -m unittest discover -s tests -q"
                ],
            }
            _orig_reset = orch._reset_run_state

            def _reset_with_seeds():
                _orig_reset()
                orch._artifacts.update(seeds)

            orch._reset_run_state = _reset_with_seeds  # type: ignore

            result = orch.run("Add farewell() to the fixture greeter")

            # -- the loop completed -----------------------------------
            self.assertEqual(result["status"], "complete")
            self.assertEqual(result["state"], "PROJECT_COMPLETE")
            self.assertEqual(result["tasks_done"], 1)

            mythis = root / ".mythis"

            # -- events: the loop left its trail ----------------------
            events_path = mythis / "events.jsonl"
            self.assertTrue(events_path.is_file(), "events.jsonl missing")
            event_types = set()
            for line in events_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    event_types.add(json.loads(line).get("type"))
                except json.JSONDecodeError:
                    pass
            self.assertIn("TASK_COMPLETED", event_types)
            self.assertIn("PROJECT_COMPLETED", event_types)
            self.assertIn("TEST_PASSED", event_types)

            # -- checkpoints: git commits with Forge-Task trailers -----
            log = subprocess.run(
                ["git", "log", "--format=%B"],
                cwd=str(root), capture_output=True, text=True, timeout=60,
            ).stdout
            self.assertIn("Forge-Task: T-001", log)
            self.assertIn("Forge-Task: PROJECT_COMPLETE", log)

            # -- docs: the scribe and the report ----------------------
            self.assertTrue((mythis / "roadmap.json").is_file())
            self.assertTrue((mythis / "ARCHITECTURE.md").is_file())
            self.assertTrue(
                (mythis / "FINAL_REPORT.md").is_file(),
                "FINAL_REPORT.md missing at PROJECT_COMPLETE",
            )

            # -- tests: the feature landed and the suite is green -----
            hello_src = (root / "src" / "hello.py").read_text(
                encoding="utf-8")
            self.assertIn("def farewell()", hello_src)
            suite = subprocess.run(
                [sys.executable, "-m", "unittest", "discover",
                 "-s", "tests", "-q"],
                cwd=str(root), capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(suite.returncode, 0, suite.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
