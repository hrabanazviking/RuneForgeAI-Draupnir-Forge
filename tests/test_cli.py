"""Slice 4 acceptance tests: the full CLI skeleton behaves for real.

Every subcommand is exercised through main() with in-memory stdout/stderr
capture; filesystem commands run inside temporary directories so the real
repository is never touched.
"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from draupnir_forge.cli import SUBCOMMANDS, build_parser, main


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    """Run main(argv); return (exit_code, stdout, stderr).

    argparse errors raise SystemExit; real handlers return int codes.
    """
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
    return code, out.getvalue(), err.getvalue()


def write_project_state(tmp: Path, **fields: object) -> Path:
    """Create a .mythis/PROJECT_STATE.json inside tmp; return its path."""
    mythis = tmp / ".mythis"
    mythis.mkdir(parents=True, exist_ok=True)
    state = {"phase": "INTAKE", "goal": "", "updated": "2026-10-09T00:00:00+00:00"}
    state.update(fields)
    path = mythis / "PROJECT_STATE.json"
    path.write_text(json.dumps(state), encoding="utf-8")
    return path


class TestParserStructure(unittest.TestCase):
    def test_subcommands_match_roadmap(self):
        self.assertEqual(
            SUBCOMMANDS,
            ("init", "forge", "status", "roadmap", "events", "checkpoint", "metrics"),
        )

    def test_global_flags_exist(self):
        parser = build_parser()
        for flag in ("--verbose", "--quiet", "--project-dir", "--version"):
            self.assertIn(flag, parser.format_help())

    def test_help_per_subcommand_works(self):
        for name in SUBCOMMANDS:
            code, out, _err = run_cli([name, "--help"])
            self.assertEqual(code, 0, f"{name} --help should exit 0")
            self.assertIn(name, out)

    def test_unknown_subcommand_exits_2(self):
        code, _out, err = run_cli(["frobnicate"])
        self.assertEqual(code, 2)
        self.assertIn("invalid choice", err)

    def test_no_command_prints_help_exits_0(self):
        code, out, _err = run_cli([])
        self.assertEqual(code, 0)
        self.assertIn("draupnir", out.lower())

    def test_version_flag(self):
        code, out, _err = run_cli(["--version"])
        self.assertEqual(code, 0)
        self.assertIn("0.1.0", out)

    def test_verbose_flag_does_not_crash(self):
        code, _out, _err = run_cli(["--verbose", "--project-dir", ".", "status"])
        self.assertIn(code, (0, 2))


class TestInit(unittest.TestCase):
    def test_init_creates_skeleton(self):
        with TemporaryDirectory() as tmp:
            code, out, _err = run_cli(["init", tmp])
            self.assertEqual(code, 0)
            mythis = Path(tmp) / ".mythis"
            for subdir in ("logs", "evidence", "sessions"):
                self.assertTrue((mythis / subdir).is_dir(), subdir)
            state_path = mythis / "PROJECT_STATE.json"
            self.assertTrue(state_path.is_file())
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["phase"], "INTAKE")
            self.assertIn(tmp, out)  # created path is printed

    def test_init_defaults_to_project_dir(self):
        with TemporaryDirectory() as tmp:
            code, _out, _err = run_cli(["--project-dir", tmp, "init"])
            self.assertEqual(code, 0)
            self.assertTrue((Path(tmp) / ".mythis" / "PROJECT_STATE.json").is_file())

    def test_init_is_idempotent(self):
        with TemporaryDirectory() as tmp:
            write_project_state(Path(tmp), goal="keep me")
            code, _out, _err = run_cli(["init", tmp])
            self.assertEqual(code, 0)
            state_path = (
                Path(tmp) / ".mythis" / "PROJECT_STATE.json"
            ).read_text(encoding="utf-8")
            state = json.loads(state_path)
            self.assertEqual(state["goal"], "keep me")  # existing state preserved

    def test_init_unwritable_path_exits_2(self):
        code, _out, err = run_cli(["init", "/proc/definitely-not-here-xyz"])
        self.assertEqual(code, 2)
        self.assertIn("cannot create", err)


class TestForgeCheckpointMetrics(unittest.TestCase):
    def test_forge_honest_exit_2(self):
        code, out, _err = run_cli(["forge"])
        self.assertEqual(code, 2)
        self.assertIn("slice 20", out)

    def test_checkpoint_honest_exit_2(self):
        code, out, _err = run_cli(["checkpoint"])
        self.assertEqual(code, 2)
        self.assertIn("slice 28", out)

    def test_metrics_honest_exit_2(self):
        code, out, _err = run_cli(["metrics"])
        self.assertEqual(code, 2)
        self.assertIn("slice 41", out)


class TestStatus(unittest.TestCase):
    def test_status_no_project(self):
        with TemporaryDirectory() as tmp:
            code, out, _err = run_cli(["--project-dir", tmp, "status"])
            self.assertEqual(code, 0)
            self.assertIn("no project", out)

    def test_status_reads_state(self):
        with TemporaryDirectory() as tmp:
            write_project_state(Path(tmp), phase="FORGE", goal="build the thing")
            code, out, _err = run_cli(["--project-dir", tmp, "status"])
            self.assertEqual(code, 0)
            self.assertIn("FORGE", out)
            self.assertIn("build the thing", out)
            self.assertIn("2026-10-09", out)

    def test_status_after_init(self):
        with TemporaryDirectory() as tmp:
            run_cli(["init", tmp])
            code, out, _err = run_cli(["--project-dir", tmp, "status"])
            self.assertEqual(code, 0)
            self.assertIn("INTAKE", out)

    def test_status_corrupt_json_exits_2(self):
        with TemporaryDirectory() as tmp:
            mythis = Path(tmp) / ".mythis"
            mythis.mkdir(parents=True)
            (mythis / "PROJECT_STATE.json").write_text("{not json", encoding="utf-8")
            code, _out, err = run_cli(["--project-dir", tmp, "status"])
            self.assertEqual(code, 2)
            self.assertIn("cannot read", err)


class TestRoadmap(unittest.TestCase):
    def _write_roadmap(self, tmp: Path, data: object) -> None:
        mythis = tmp / ".mythis"
        mythis.mkdir(parents=True, exist_ok=True)
        (mythis / "roadmap.json").write_text(json.dumps(data), encoding="utf-8")

    def test_roadmap_missing(self):
        with TemporaryDirectory() as tmp:
            code, out, _err = run_cli(["--project-dir", tmp, "roadmap"])
            self.assertEqual(code, 0)
            self.assertIn("no roadmap yet", out)

    def test_roadmap_prints_task_table(self):
        with TemporaryDirectory() as tmp:
            self._write_roadmap(
                Path(tmp),
                {"tasks": [
                    {"id": "s4", "title": "CLI skeleton", "status": "done"},
                    {"id": "s5", "title": "Event model", "status": "todo"},
                ]},
            )
            code, out, _err = run_cli(["--project-dir", tmp, "roadmap"])
            self.assertEqual(code, 0)
            self.assertIn("CLI skeleton", out)
            self.assertIn("Event model", out)
            self.assertIn("done", out)

    def test_roadmap_accepts_bare_list(self):
        with TemporaryDirectory() as tmp:
            self._write_roadmap(Path(tmp), [{"id": "s1", "title": "scaffold"}])
            code, out, _err = run_cli(["--project-dir", tmp, "roadmap"])
            self.assertEqual(code, 0)
            self.assertIn("scaffold", out)

    def test_roadmap_corrupt_exits_2(self):
        with TemporaryDirectory() as tmp:
            mythis = Path(tmp) / ".mythis"
            mythis.mkdir(parents=True)
            (mythis / "roadmap.json").write_text("[oops", encoding="utf-8")
            code, _out, err = run_cli(["--project-dir", tmp, "roadmap"])
            self.assertEqual(code, 2)
            self.assertIn("cannot read", err)


class TestEvents(unittest.TestCase):
    def _write_events(self, tmp: Path, count: int) -> None:
        mythis = tmp / ".mythis"
        mythis.mkdir(parents=True, exist_ok=True)
        lines = [
            json.dumps({
                "seq": i,
                "ts": f"2026-10-09T00:{i:02d}:00+00:00",
                "type": "EVEN" if i % 2 == 0 else "ODD",
                "actor_role": "tester",
            })
            for i in range(count)
        ]
        (mythis / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_events_missing(self):
        with TemporaryDirectory() as tmp:
            code, out, _err = run_cli(["--project-dir", tmp, "events"])
            self.assertEqual(code, 0)
            self.assertIn("no events", out)

    def test_events_tails_last_20_by_default(self):
        with TemporaryDirectory() as tmp:
            self._write_events(Path(tmp), 30)
            code, out, _err = run_cli(["--project-dir", tmp, "events"])
            self.assertEqual(code, 0)
            lines = [line for line in out.splitlines() if line.strip()]
            self.assertEqual(len(lines), 20)
            self.assertIn("29", lines[-1])  # newest event is last
            self.assertNotIn(" 0  ", f" {lines[0]} ")  # seq 0 fell off the tail

    def test_events_limit_flag(self):
        with TemporaryDirectory() as tmp:
            self._write_events(Path(tmp), 30)
            code, out, _err = run_cli(["--project-dir", tmp, "events", "--limit", "5"])
            self.assertEqual(code, 0)
            shown = [line for line in out.splitlines() if line.strip()]
            self.assertEqual(len(shown), 5)

    def test_events_type_filter(self):
        with TemporaryDirectory() as tmp:
            self._write_events(Path(tmp), 10)
            code, out, _err = run_cli(
                ["--project-dir", tmp, "events", "--type", "ODD"]
            )
            self.assertEqual(code, 0)
            self.assertIn("ODD", out)
            self.assertNotIn("EVEN", out)

    def test_events_skips_corrupt_lines(self):
        with TemporaryDirectory() as tmp:
            self._write_events(Path(tmp), 3)
            path = Path(tmp) / ".mythis" / "events.jsonl"
            with path.open("a", encoding="utf-8") as handle:
                handle.write("this is not json\n")
            code, out, _err = run_cli(["--project-dir", tmp, "events"])
            self.assertEqual(code, 0)  # self-healing: still prints the good lines
            self.assertIn("tester", out)


if __name__ == "__main__":
    unittest.main()
