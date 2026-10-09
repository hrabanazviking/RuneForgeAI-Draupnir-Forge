"""Slice 32 acceptance tests: glass-box inspection commands.

``roadmap`` (task table + dependencies), ``events --since`` (sequence
filter on top of the slice-4 filters), ``architecture`` and
``decisions`` (canonical-doc printers), and ``forge`` (real
Orchestrator construction with an honest dry-run / not-yet-loop).

Every command is exercised through main() with in-memory
stdout/stderr; fixture projects live in temporary directories so the
real repository is never touched. All commands here are read-only.
"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from draupnir_forge.cli import main


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    """Run main(argv); return (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
    return code, out.getvalue(), err.getvalue()


def _mythis(tmp: Path) -> Path:
    mythis = tmp / ".mythis"
    mythis.mkdir(parents=True, exist_ok=True)
    return mythis


class TestRoadmapGlassbox(unittest.TestCase):
    def _write_roadmap(self, tmp: Path, data: object) -> None:
        (_mythis(tmp) / "roadmap.json").write_text(
            json.dumps(data), encoding="utf-8"
        )

    def test_dependency_info_shown(self):
        with TemporaryDirectory() as tmp:
            self._write_roadmap(
                Path(tmp),
                {"tasks": [
                    {"task_id": "T-001", "title": "Scaffold", "status": "done",
                     "depends_on": []},
                    {"task_id": "T-002", "title": "Build engine",
                     "status": "ready", "depends_on": ["T-001"]},
                ]},
            )
            code, out, _err = run_cli(["--project-dir", tmp, "roadmap"])
            self.assertEqual(code, 0)
            self.assertIn("ID", out)
            self.assertIn("STATUS", out)
            self.assertIn("TITLE", out)
            self.assertIn("T-001", out)
            self.assertIn("T-002", out)
            self.assertIn("[depends on: T-001]", out)

    def test_missing_roadmap_message(self):
        with TemporaryDirectory() as tmp:
            code, out, _err = run_cli(["--project-dir", tmp, "roadmap"])
            self.assertEqual(code, 0)
            self.assertIn("no roadmap yet", out)

    def test_corrupt_roadmap_exits_2(self):
        with TemporaryDirectory() as tmp:
            (_mythis(Path(tmp)) / "roadmap.json").write_text(
                "{oops", encoding="utf-8"
            )
            code, _out, err = run_cli(["--project-dir", tmp, "roadmap"])
            self.assertEqual(code, 2)
            self.assertIn("cannot read", err)


class TestEventsSince(unittest.TestCase):
    def _write_events(self, tmp: Path, count: int) -> None:
        lines = [
            json.dumps({
                "seq": i,
                "ts": f"2026-10-09T00:00:{i:02d}+00:00",
                "type": "PROJECT_CREATED" if i == 1 else "TASK_COMPLETED",
                "actor_role": "scribe",
                "project": "fixture",
                "payload": {},
                "caused_by": None,
            })
            for i in range(1, count + 1)
        ]
        (_mythis(tmp) / "events.jsonl").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    def test_since_filters_older_sequences(self):
        with TemporaryDirectory() as tmp:
            self._write_events(Path(tmp), 5)
            code, out, _err = run_cli(
                ["--project-dir", tmp, "events", "--since", "3"]
            )
            self.assertEqual(code, 0)
            for seq in ("4", "5"):
                self.assertIn(seq, out)
            self.assertNotIn("PROJECT_CREATED", out)

    def test_since_zero_shows_everything(self):
        with TemporaryDirectory() as tmp:
            self._write_events(Path(tmp), 3)
            code, out, _err = run_cli(
                ["--project-dir", tmp, "events", "--since", "0",
                 "--limit", "50"]
            )
            self.assertEqual(code, 0)
            self.assertIn("PROJECT_CREATED", out)

    def test_existing_limit_and_type_still_work(self):
        with TemporaryDirectory() as tmp:
            self._write_events(Path(tmp), 5)
            code, out, _err = run_cli(
                ["--project-dir", tmp, "events",
                 "--type", "PROJECT_CREATED", "--limit", "2"]
            )
            self.assertEqual(code, 0)
            self.assertIn("PROJECT_CREATED", out)
            self.assertNotIn("TASK_COMPLETED", out)


class TestArchitectureAndDecisions(unittest.TestCase):
    def test_architecture_missing(self):
        with TemporaryDirectory() as tmp:
            code, out, _err = run_cli(["--project-dir", tmp, "architecture"])
            self.assertEqual(code, 0)
            self.assertIn("not yet generated", out)

    def test_architecture_prints_doc(self):
        with TemporaryDirectory() as tmp:
            (_mythis(Path(tmp)) / "ARCHITECTURE.md").write_text(
                "# Forge Architecture\n\nThe loom of the forge.\n",
                encoding="utf-8",
            )
            code, out, _err = run_cli(["--project-dir", tmp, "architecture"])
            self.assertEqual(code, 0)
            self.assertIn("# Forge Architecture", out)
            self.assertIn("loom of the forge", out)

    def test_decisions_missing(self):
        with TemporaryDirectory() as tmp:
            code, out, _err = run_cli(["--project-dir", tmp, "decisions"])
            self.assertEqual(code, 0)
            self.assertIn("no decisions recorded", out)

    def test_decisions_prints_ledger(self):
        with TemporaryDirectory() as tmp:
            (_mythis(Path(tmp)) / "DECISIONS.md").write_text(
                "## 2026-10-09 — Chose stdlib urllib\n\nReason: no deps.\n",
                encoding="utf-8",
            )
            code, out, _err = run_cli(["--project-dir", tmp, "decisions"])
            self.assertEqual(code, 0)
            self.assertIn("Chose stdlib urllib", out)

    def test_empty_doc_counts_as_missing(self):
        with TemporaryDirectory() as tmp:
            (_mythis(Path(tmp)) / "ARCHITECTURE.md").write_text(
                "\n", encoding="utf-8"
            )
            code, out, _err = run_cli(["--project-dir", tmp, "architecture"])
            self.assertEqual(code, 0)
            self.assertIn("not yet generated", out)


class TestForgeWiring(unittest.TestCase):
    def test_forge_dry_run_prints_plan_and_exits_0(self):
        with TemporaryDirectory() as tmp:
            code, out, _err = run_cli(
                ["--project-dir", tmp, "forge", "--dry-run"]
            )
            self.assertEqual(code, 0)
            self.assertIn("starting forge loop...", out)
            self.assertIn("dry run", out)
            self.assertIn("INTAKE", out)
            self.assertIn("PROJECT_COMPLETE", out)

    def test_forge_without_dry_run_is_honest(self):
        with TemporaryDirectory() as tmp:
            code, out, _err = run_cli(["--project-dir", tmp, "forge"])
            self.assertEqual(code, 2)
            self.assertIn("starting forge loop...", out)
            self.assertIn("autonomous loop enabled in slice 46", out)


if __name__ == "__main__":
    unittest.main()
