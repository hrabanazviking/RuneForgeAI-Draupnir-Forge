"""Tests for the Context Compiler (slice 21)."""

import json
import os
import shutil
import tempfile
import unittest

from draupnir_forge.context import (
    ContextCompiler,
    ContextPackage,
    render,
)
from draupnir_forge.tasks import ForgeTask


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


class ContextCompilerTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="forge-context-")
        self.mythis = os.path.join(self.root, ".mythis")
        _write(
            os.path.join(self.mythis, "SYSTEM_VISION.md"),
            "# Vision\n\nBuild a rune-carving machine for the fjord folk.\n",
        )
        _write(
            os.path.join(self.mythis, "INTERFACES.md"),
            "# Interfaces\n\n`carve(rune)` — carves one rune.\n",
        )
        _write(
            os.path.join(self.mythis, "INVARIANTS.md"),
            "# Invariants\n\n- Never carve after midnight.\n"
            "- Honor the iron price.\n",
        )
        _write(
            os.path.join(self.mythis, "DECISIONS.md"),
            "## 2026-10-01 — Carving engine\n\nWe chose the rune-carving "
            "engine over the stamp press for fidelity.\n\n"
            "## 2026-10-02 — Unrelated\n\nThe mead hall gets new benches.\n",
        )
        _write(
            os.path.join(self.mythis, "KNOWN_ISSUES.md"),
            "- T-001: the carving chisel slips on wet oak.\n",
        )
        # Architecture map: one domain owning two files.
        _write(os.path.join(self.root, "carving", "chisel.py"), "CHISEL = 1\n")
        _write(os.path.join(self.root, "carving", "runes.py"), "RUNES = 2\n")
        _write(
            os.path.join(self.root, "meadhall", "benches.py"), "BENCHES = 3\n"
        )
        _write(
            os.path.join(self.mythis, "architecture.json"),
            json.dumps(
                {
                    "domains": [
                        {
                            "name": "carving",
                            "owner": "worker",
                            "files": ["carving/chisel.py", "carving/runes.py"],
                        },
                        {
                            "name": "meadhall",
                            "owner": "worker",
                            "files": ["meadhall/benches.py"],
                        },
                    ],
                    "interfaces": [],
                    "invariants": [],
                    "dependency_direction": {},
                }
            ),
        )
        self.compiler = ContextCompiler(self.root)
        self.task = ForgeTask(
            task_id="T-001",
            title="Sharpen the rune-carving chisel",
            domain="carving",
            goal="Make the carving engine cut cleaner runes.",
            constraints=["no new dependencies"],
            acceptance=["chisel cuts oak cleanly"],
            verification=["run the carving tests"],
        )

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_build_collects_domain_files_only(self):
        package = self.compiler.build(self.task)
        self.assertIsInstance(package, ContextPackage)
        self.assertEqual(package.task.task_id, "T-001")
        # Only the carving domain's files; the mead hall stays out.
        self.assertEqual(
            sorted(package.domain_files),
            ["carving/chisel.py", "carving/runes.py"],
        )
        self.assertIn("CHISEL = 1", package.domain_files["carving/chisel.py"])
        self.assertNotIn("meadhall/benches.py", package.domain_files)

    def test_build_reads_canonical_docs(self):
        package = self.compiler.build(self.task)
        self.assertIn("rune-carving machine", package.vision_excerpt)
        self.assertIn("carve(rune)", package.interfaces)
        self.assertIn("Never carve after midnight.", package.invariants)
        self.assertIn("Honor the iron price.", package.invariants)

    def test_decisions_keyword_matched(self):
        package = self.compiler.build(self.task)
        joined = "\n".join(package.decisions)
        self.assertIn("rune-carving", joined)
        self.assertNotIn("mead hall gets new benches", joined)

    def test_prior_failures_from_known_issues(self):
        package = self.compiler.build(self.task)
        self.assertTrue(
            any("chisel slips on wet oak" in f for f in package.prior_failures)
        )

    def test_prior_failures_from_event_log(self):
        from draupnir_forge.events import EventLog, EventType

        log = EventLog(self.root)
        log.emit(
            EventType.TASK_FAILED,
            "tester",
            {"task_id": "T-001", "summary": "carving tests failed on oak"},
        )
        package = self.compiler.build(self.task)
        self.assertTrue(
            any(
                "carving tests failed on oak" in f
                for f in package.prior_failures
            )
        )

    def test_token_estimate_is_chars_over_four(self):
        package = self.compiler.build(self.task)
        chars = (
            len(package.task.title)
            + len(package.task.goal)
            + len(package.vision_excerpt)
            + len(package.interfaces)
            + sum(
                len(p) + len(c) for p, c in package.domain_files.items()
            )
            + sum(len(i) for i in package.invariants)
            + sum(len(d) for d in package.decisions)
            + sum(len(f) for f in package.prior_failures)
        )
        self.assertEqual(package.token_estimate, chars // 4)
        self.assertGreater(package.token_estimate, 0)

    def test_fallback_domain_files_by_name(self):
        # No architecture.json: fall back to files matching the domain name.
        os.remove(os.path.join(self.mythis, "architecture.json"))
        package = ContextCompiler(self.root).build(self.task)
        self.assertIn("carving/chisel.py", package.domain_files)
        self.assertNotIn("meadhall/benches.py", package.domain_files)

    def test_missing_mythis_never_crashes(self):
        empty_dir = tempfile.mkdtemp(prefix="forge-empty-")
        package = ContextCompiler(empty_dir).build(self.task)
        self.assertEqual(package.vision_excerpt, "")
        self.assertEqual(package.domain_files, {})
        self.assertEqual(package.invariants, [])
        self.assertEqual(package.decisions, [])
        self.assertEqual(package.prior_failures, [])

    def test_truncation_drops_in_policy_order(self):
        # Force every droppable section out.
        self.compiler.policy["max_tokens"] = 1
        package = self.compiler.build(self.task)
        self.assertEqual(package.prior_failures, [])
        self.assertEqual(package.decisions, [])
        self.assertEqual(package.domain_files, {})
        self.assertEqual(package.vision_excerpt, "")
        # The non-negotiable core survives.
        self.assertEqual(package.task.task_id, "T-001")
        self.assertIn("carve(rune)", package.interfaces)
        self.assertTrue(package.invariants)

    def test_truncation_partial_drop(self):
        # Just small enough that only the first section needs dropping.
        package = self.compiler.build(self.task)
        full = package.token_estimate
        self.compiler.policy["max_tokens"] = full - 1
        trimmed = self.compiler.build(self.task)
        self.assertEqual(trimmed.prior_failures, [])
        self.assertTrue(trimmed.decisions)  # only front of drop_order went
        self.assertTrue(trimmed.domain_files)
        self.assertTrue(trimmed.vision_excerpt)
        self.assertLessEqual(trimmed.token_estimate, full - 1)

    def test_render_has_section_headers(self):
        package = self.compiler.build(self.task)
        text = render(package)
        for header in (
            "## TASK",
            "## VISION EXCERPT",
            "## DOMAIN FILES",
            "## INTERFACES",
            "## INVARIANTS",
            "## RELEVANT DECISIONS",
            "## PRIOR FAILURES",
        ):
            self.assertIn(header, text)
        self.assertIn("T-001", text)
        self.assertIn("### carving/chisel.py", text)
        self.assertIn("chisel slips on wet oak", text)

    def test_render_empty_package(self):
        package = ContextPackage(task=self.task, token_estimate=0)
        text = render(package)
        self.assertIn("## TASK", text)
        self.assertIn("(none)", text)


if __name__ == "__main__":
    unittest.main()
