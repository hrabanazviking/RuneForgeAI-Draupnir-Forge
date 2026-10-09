"""Slice 35 tests: DecisionLedger."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from draupnir_forge.decisions import DecisionLedger


class TestDecisionLedger(unittest.TestCase):
    def test_record_writes_entry_with_revisit(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = DecisionLedger(tmp)
            path = ledger.record(
                "Use pytest for the test gate",
                reason="suite already uses it",
                alternatives=["unittest only"],
                evidence="suite green under pytest",
                consequences="tester prefers pytest; revisit if pytest is unavailable",
                role="tester",
            )
            text = Path(path).read_text(encoding="utf-8")
        self.assertEqual(Path(path).name, "DECISIONS.md")
        self.assertIn("# Decisions", text)
        self.assertIn("## ", text)
        self.assertIn("— Use pytest for the test gate", text)
        self.assertIn("- Role: tester", text)
        self.assertIn("- Reason: suite already uses it", text)
        self.assertIn("- Alternatives considered: unittest only", text)
        self.assertIn("- Evidence: suite green under pytest", text)
        self.assertIn("- Consequences: tester prefers pytest", text)
        self.assertIn("- Revisit if: tester prefers pytest", text)

    def test_record_is_append_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = DecisionLedger(tmp)
            ledger.record("decision one", "r1")
            ledger.record("decision two", "r2")
            text = (Path(tmp) / ".mythis" / "DECISIONS.md").read_text(
                encoding="utf-8")
        self.assertIn("decision one", text)
        self.assertIn("decision two", text)
        self.assertEqual(text.count("# Decisions"), 1)

    def test_find_keyword_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = DecisionLedger(tmp)
            ledger.record("Use pytest for the test gate", "fast feedback")
            ledger.record("Choose sqlite for the cache", "zero dependencies")
            hits = ledger.find("pytest")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["decision"], "Use pytest for the test gate")
        self.assertIn("fast feedback", hits[0]["reason"])

    def test_find_is_case_insensitive(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = DecisionLedger(tmp)
            ledger.record("Use Pytest for the test gate", "r")
            self.assertEqual(len(ledger.find("PYTEST")), 1)

    def test_find_on_missing_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = DecisionLedger(tmp)
            self.assertEqual(ledger.find("anything"), [])

    def test_contradiction_warning_triggers(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = DecisionLedger(tmp)
            ledger.record(
                "Use pytest for the test gate",
                reason="pytest gives fast feedback on the gateway suite",
            )
            warnings = ledger.check_contradiction(
                "Do not use pytest for the test gate; use unittest instead"
            )
        self.assertEqual(len(warnings), 1)
        warning = warnings[0]
        self.assertIn("pytest", warning["shared_keywords"])
        self.assertIn("Use pytest for the test gate", warning["decision"])
        self.assertIn("different stance", warning["reason"])

    def test_no_contradiction_when_agreeing(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = DecisionLedger(tmp)
            ledger.record(
                "Use pytest for the test gate",
                reason="pytest gives fast feedback on the gateway suite",
            )
            warnings = ledger.check_contradiction(
                "Keep pytest for the test gate; it works well"
            )
        self.assertEqual(warnings, [])

    def test_no_contradiction_on_unrelated_topics(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = DecisionLedger(tmp)
            ledger.record(
                "Use sqlite for the cache",
                reason="zero-dependency embedded storage",
            )
            warnings = ledger.check_contradiction(
                "Do not use pytest for the test gate; use unittest instead"
            )
        self.assertEqual(warnings, [])

    def test_parses_scribe_format(self):
        """Entries written by the Scribe role parse back, too."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".mythis" / "DECISIONS.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "# Decisions\n\n"
                "## 2026-10-09T10:00:00+00:00 — Use pytest\n"
                "- Role: tester\n"
                "- Reason: speed\n"
                "- Alternatives considered: unittest only\n"
                "- Evidence: green\n"
                "- Consequences: none\n"
                "\n",
                encoding="utf-8",
            )
            ledger = DecisionLedger(tmp)
            entries = ledger.entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["decision"], "Use pytest")
        self.assertEqual(entries[0]["role"], "tester")


if __name__ == "__main__":
    unittest.main()
