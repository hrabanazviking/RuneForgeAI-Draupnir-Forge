"""Slice 49 acceptance tests: the FINAL_REPORT generator.

A fixture project is built in a temp dir (roadmap.json, ARCHITECTURE.md,
README.md, KNOWN_ISSUES.md, DISCOVERY_REPORT.md, events.jsonl); the
golden test asserts every spec §26 section is present and carries the
expected fixture content. A sparse project must degrade gracefully.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from draupnir_forge.events import EventLog, EventType
from draupnir_forge.report import FinalReport


def _build_fixture(root: Path) -> None:
    """Create the golden fixture project under root."""
    mythis = root / ".mythis"
    mythis.mkdir(parents=True, exist_ok=True)
    roadmap = {
        "tasks": [
            {"task_id": "T-1", "title": "Forge the hammer head", "status": "done"},
            {"task_id": "T-2", "title": "Carve the rune haft", "status": "done"},
            {"task_id": "T-3", "title": "Enchant the return flight",
             "status": "in_progress"},
        ]
    }
    (mythis / "roadmap.json").write_text(json.dumps(roadmap), encoding="utf-8")
    (mythis / "ARCHITECTURE.md").write_text(
        "# Architecture\n\nThe forge is built from events, roles, and a state machine.\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        "# Hammer\n\nRun it: `draupnir forge`.\n", encoding="utf-8"
    )
    (mythis / "KNOWN_ISSUES.md").write_text(
        "- The haft splinters under great strain.\n", encoding="utf-8"
    )
    (mythis / "DISCOVERY_REPORT.md").write_text(
        "# Discovery\n\n## Risks\n\n- No tests for the haft.\n\n## Other\n\n- trivia\n",
        encoding="utf-8",
    )
    log = EventLog(root)
    for _ in range(3):
        log.emit(EventType.TEST_PASSED, actor_role="Tester", payload={})
    log.emit(EventType.TEST_FAILED, actor_role="Tester", payload={})
    log.emit(EventType.TASK_COMPLETED, actor_role="Orchestrator",
             payload={"task_id": "T-1"})


class TestFinalReportGolden(unittest.TestCase):
    def test_all_spec_26_sections_present(self) -> None:
        with TemporaryDirectory() as tmp:
            _build_fixture(Path(tmp))
            text = FinalReport(Path(tmp)).generate()
            for section in (
                "## What was built",
                "## Architecture summary",
                "## How to run",
                "## Verification performed",
                "## Remaining limitations",
                "## Future roadmap ideas",
                "## Known risks",
            ):
                self.assertIn(section, text)

    def test_what_was_built_lists_completed_tasks(self) -> None:
        with TemporaryDirectory() as tmp:
            _build_fixture(Path(tmp))
            text = FinalReport(Path(tmp)).generate()
            self.assertIn("Forge the hammer head", text)
            self.assertIn("Carve the rune haft", text)
            self.assertNotIn(
                "Enchant the return flight",
                text.split("## What was built")[1].split("##")[0],
            )

    def test_architecture_excerpt_and_readme_present(self) -> None:
        with TemporaryDirectory() as tmp:
            _build_fixture(Path(tmp))
            text = FinalReport(Path(tmp)).generate()
            self.assertIn("events, roles, and a state machine", text)
            self.assertIn("`draupnir forge`", text)

    def test_verification_counts_events(self) -> None:
        with TemporaryDirectory() as tmp:
            _build_fixture(Path(tmp))
            text = FinalReport(Path(tmp)).generate()
            self.assertIn("TEST_PASSED events: 3", text)
            self.assertIn("TEST_FAILED events: 1", text)
            self.assertIn("TASK_COMPLETED events: 1", text)

    def test_limitations_and_risks_sections(self) -> None:
        with TemporaryDirectory() as tmp:
            _build_fixture(Path(tmp))
            text = FinalReport(Path(tmp)).generate()
            self.assertIn("The haft splinters under great strain", text)
            self.assertIn("No tests for the haft", text)

    def test_future_ideas_lists_incomplete_tasks(self) -> None:
        with TemporaryDirectory() as tmp:
            _build_fixture(Path(tmp))
            text = FinalReport(Path(tmp)).generate()
            self.assertIn("Enchant the return flight", text)

    def test_write_saves_final_report_md(self) -> None:
        with TemporaryDirectory() as tmp:
            _build_fixture(Path(tmp))
            report = FinalReport(Path(tmp))
            path = report.write()
            self.assertTrue(path.is_file())
            self.assertEqual(path.name, "FINAL_REPORT.md")
            self.assertEqual(path.parent.name, ".mythis")
            self.assertEqual(
                path.read_text(encoding="utf-8"), report.generate()
            )


class TestFinalReportSparse(unittest.TestCase):
    def test_missing_sources_degrade_gracefully(self) -> None:
        with TemporaryDirectory() as tmp:
            text = FinalReport(Path(tmp)).generate()
            for section in (
                "## What was built",
                "## Architecture summary",
                "## How to run",
                "## Verification performed",
                "## Remaining limitations",
                "## Future roadmap ideas",
                "## Known risks",
            ):
                self.assertIn(section, text)
            self.assertIn("TEST_PASSED events: 0", text)

    def test_write_on_empty_project(self) -> None:
        with TemporaryDirectory() as tmp:
            path = FinalReport(Path(tmp)).write()
            self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
