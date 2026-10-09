"""Tests for the clean-room research mode (slice 40)."""

import json
import os
import shutil
import tempfile
import unittest

from draupnir_forge.cleanroom import (
    DERIVED_STAMP,
    OVERLAP_WINDOW,
    CleanRoom,
)


class CleanRoomTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="forge-cleanroom-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.room = CleanRoom(self.root)

    def _provenance_text(self):
        path = os.path.join(self.root, ".mythis", "provenance.md")
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()

    def test_study_records_provenance(self):
        entry = self.room.study(
            source_id="oauth-rfc",
            url="https://example.test/oauth",
            license="MIT",
            notes="Token endpoint exchanges codes for bearer tokens.",
        )
        self.assertEqual(entry["source_id"], "oauth-rfc")
        self.assertEqual(entry["license"], "MIT")
        self.assertTrue(entry["date"])  # ISO date stamped
        text = self._provenance_text()
        self.assertIn("oauth-rfc", text)
        self.assertIn("https://example.test/oauth", text)
        self.assertIn("MIT", text)
        self.assertIn("Token endpoint exchanges codes", text)

    def test_study_rejects_empty_notes(self):
        with self.assertRaises(ValueError):
            self.room.study("x", "https://example.test", "MIT", "   ")

    def test_study_rejects_empty_source_id(self):
        with self.assertRaises(ValueError):
            self.room.study("  ", "https://example.test", "MIT", "notes")

    def test_sources_survive_reload(self):
        self.room.study(
            "s1", "https://example.test/1", "Apache-2.0", "Notes about s1."
        )
        fresh = CleanRoom(self.root)
        self.assertIn("s1", fresh.sources)
        self.assertEqual(fresh.sources["s1"]["license"], "Apache-2.0")

    def test_full_text_is_never_stored(self):
        full_text = "THIS IS THE ENTIRE SOURCE TEXT " * 20
        self.room.study("s2", "https://example.test/2", "MIT",
                        "My own short summary.")
        registry_path = os.path.join(
            self.root, ".mythis", "cleanroom_sources.json"
        )
        with open(registry_path, "r", encoding="utf-8") as handle:
            registry = json.load(handle)
        dumped = json.dumps(registry)
        self.assertNotIn("ENTIRE SOURCE TEXT", dumped)
        self.assertNotIn(full_text[:50], dumped)
        for source in registry["sources"]:
            self.assertNotIn("full_text", source)
            self.assertNotIn("content", source)

    def test_derive_shape_and_stamp(self):
        self.room.study(
            "auth-notes", "https://example.test/a", "MIT",
            "Session tokens should expire after inactivity.",
        )
        derived = self.room.derive(["expire idle sessions", "rotate keys"])
        self.assertEqual(
            set(derived.keys()),
            {"requirements", "compat_notes", "architecture_sketch"},
        )
        self.assertEqual(len(derived["requirements"]), 2)
        for section in derived.values():
            for line in section:
                self.assertIn(DERIVED_STAMP, line)
        self.assertIn("expire idle sessions", derived["requirements"][0])
        self.assertIn("auth-notes", derived["requirements"][0])

    def test_derive_requires_requirements(self):
        with self.assertRaises(ValueError):
            self.room.derive([])
        with self.assertRaises(ValueError):
            self.room.derive(["   "])

    def test_derive_compat_notes_license_aware(self):
        self.room.study("p1", "https://example.test/p1", "MIT",
                        "Permissive notes.")
        self.room.study("c1", "https://example.test/c1", "GPL-3.0",
                        "Copyleft notes.")
        derived = self.room.derive(["do the thing"])
        notes = derived["compat_notes"]
        joined = " ".join(notes)
        self.assertIn("MIT", joined)
        self.assertIn("GPL-3.0", joined)
        self.assertIn("Copyleft", joined)
        for line in notes:
            self.assertIn(DERIVED_STAMP, line)

    def test_derive_architecture_sketch_has_sections(self):
        self.room.study("s", "https://example.test/s", "BSD-3-Clause",
                        "Some design notes.")
        sketch = self.room.derive(["build a widget"])["architecture_sketch"]
        joined = " ".join(sketch).lower()
        for section in ("components", "data flow", "interfaces",
                        "error handling", "security"):
            self.assertIn(section, joined)

    def test_overlap_window_is_25(self):
        self.assertEqual(OVERLAP_WINDOW, 25)

    def test_check_overlap_fires_on_verbatim_snippet(self):
        marker = "the quick brown fox jumps over the lazy dog today"
        self.room.study("s", "https://example.test/s", "MIT",
                        f"Notes say: {marker} and more.")
        candidate = f"My design: {marker} is implemented here."
        hits = self.room.check_overlap(candidate)
        self.assertTrue(hits, "expected the verbatim 25-char alarm to fire")
        self.assertTrue(
            any(marker[:25] in hit or hit in marker for hit in hits)
        )
        for hit in hits:
            self.assertEqual(len(hit), OVERLAP_WINDOW)

    def test_check_overlap_case_insensitive(self):
        self.room.study("s", "https://example.test/s", "MIT",
                        "All session tokens must expire quickly.")
        hits = self.room.check_overlap(
            "design: ALL SESSION TOKENS MUST EXPIRE QUICKLY now"
        )
        self.assertTrue(hits)

    def test_check_overlap_silent_on_original_text(self):
        self.room.study("s", "https://example.test/s", "MIT",
                        "Tokens expire after thirty minutes of idleness.")
        hits = self.room.check_overlap(
            "I invented a wholly different mechanism with rotating nonces "
            "and short-lived grants bound to device fingerprints."
        )
        self.assertEqual(hits, [])

    def test_check_overlap_short_candidate_is_clean(self):
        self.room.study("s", "https://example.test/s", "MIT",
                        "Some reasonably long study notes here.")
        self.assertEqual(self.room.check_overlap("tiny"), [])

    def test_overlap_survives_reload(self):
        marker = "persistent window check for reload behavior ok"
        self.room.study("s", "https://example.test/s", "MIT",
                        f"Study notes: {marker}.")
        fresh = CleanRoom(self.root)
        hits = fresh.check_overlap(f"candidate text {marker} end")
        self.assertTrue(hits)

    def test_report_summarizes_provenance(self):
        self.room.study("r1", "https://example.test/r1", "MIT",
                        "First notes.")
        self.room.study("r2", "https://example.test/r2", "GPL-3.0",
                        "Second notes.")
        report = self.room.report()
        self.assertIn("# Clean-Room Provenance Report", report)
        self.assertIn("r1", report)
        self.assertIn("r2", report)
        self.assertIn("MIT", report)
        self.assertIn(DERIVED_STAMP, report)
        self.assertIn("never kept", report)

    def test_report_empty_room(self):
        report = self.room.report()
        self.assertIn("No sources studied yet", report)


if __name__ == "__main__":
    unittest.main()
