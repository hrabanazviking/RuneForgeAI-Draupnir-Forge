"""Dawn-forge Track D acceptance tests (slices 16-20): Eldra's carving.

auditor/finding-serialize, tester/evidence-atomic, report/task-stats,
archdocs/missing-keys, scribe/roadmap-status-validation.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from draupnir_forge.archdocs import render_architecture, render_interfaces
from draupnir_forge.report import FinalReport
from draupnir_forge.roles.auditor import Finding
from draupnir_forge.roles.scribe import update_roadmap_status
from draupnir_forge.roles.tester import write_evidence_log


class TestFindingFromDict(unittest.TestCase):
    """Slice 16 — Finding round-trips through to_dict()/from_dict()."""

    def test_round_trip_equality(self):
        finding = Finding(
            severity="high",
            check="hardcoded_secrets",
            file="src/x.py",
            line=42,
            message="possible api key",
        )
        restored = Finding.from_dict(finding.to_dict())
        self.assertEqual(restored.severity, finding.severity)
        self.assertEqual(restored.check, finding.check)
        self.assertEqual(restored.file, finding.file)
        self.assertEqual(restored.line, finding.line)
        self.assertEqual(restored.message, finding.message)

    def test_round_trip_none_line(self):
        finding = Finding(
            severity="low", check="c", file="f", line=None, message="m"
        )
        restored = Finding.from_dict(finding.to_dict())
        self.assertIsNone(restored.line)

    def test_missing_optional_keys_get_defaults_never_raises(self):
        restored = Finding.from_dict({})
        self.assertEqual(restored.severity, "low")
        self.assertEqual(restored.check, "")
        self.assertEqual(restored.file, "")
        self.assertIsNone(restored.line)
        self.assertEqual(restored.message, "")

    def test_invalid_severity_falls_back_never_raises(self):
        restored = Finding.from_dict({"severity": "critical", "check": "c"})
        self.assertEqual(restored.severity, "low")

    def test_non_dict_input_never_raises(self):
        restored = Finding.from_dict(None)
        self.assertIsInstance(restored, Finding)

    def test_unknown_keys_tolerated(self):
        data = Finding(
            severity="medium", check="c", file="f", line=1, message="m"
        ).to_dict()
        data["bogus_key"] = "ignored"
        restored = Finding.from_dict(data)
        self.assertEqual(restored.severity, "medium")


class TestWriteEvidenceLogAtomic(unittest.TestCase):
    """Slice 17 — evidence log writes atomically, same contract."""

    def test_write_returns_path_with_full_content(self):
        with TemporaryDirectory() as tmp:
            output = "ok\n" * 100
            result = write_evidence_log(
                tmp, ["pytest", "-q"], output, {"passed": 3}
            )
            self.assertIsInstance(result, str)
            path = Path(result)
            self.assertTrue(path.is_file())
            text = path.read_text(encoding="utf-8")
            self.assertIn("pytest -q", text)
            self.assertIn(output, text)

    def test_no_tmp_residue(self):
        with TemporaryDirectory() as tmp:
            result = write_evidence_log(
                tmp, ["pytest"], "out", {"passed": 1}
            )
            self.assertIsNotNone(result)
            evidence_dir = Path(tmp) / ".mythis" / "evidence" / "tests"
            tmp_files = list(evidence_dir.glob("*.tmp"))
            self.assertEqual(tmp_files, [])

    def test_never_raises_on_bad_project_dir(self):
        # A file where a directory must be -> None, not an exception.
        with TemporaryDirectory() as tmp:
            blocker = Path(tmp) / "blocker"
            blocker.write_text("x")
            result = write_evidence_log(
                str(blocker / "nope"), ["pytest"], "out", {}
            )
            self.assertIsNone(result)


class TestFinalReportTaskStats(unittest.TestCase):
    """Slice 18 — task-summary line in the rendered final report."""

    @staticmethod
    def _write_roadmap(root: Path, tasks) -> None:
        mythis = root / ".mythis"
        mythis.mkdir(parents=True, exist_ok=True)
        (mythis / "roadmap.json").write_text(
            json.dumps({"tasks": tasks}), encoding="utf-8"
        )

    def test_two_of_three_line(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_roadmap(root, [
                {"task_id": "T-1", "title": "one", "status": "done"},
                {"task_id": "T-2", "title": "two", "status": "completed"},
                {"task_id": "T-3", "title": "three", "status": "in_progress"},
            ])
            report = FinalReport(root)
            text = report.generate()
            self.assertIn("2 of 3 tasks complete", text)
            path = report.write()
            self.assertIn("2 of 3 tasks complete",
                          path.read_text(encoding="utf-8"))

    def test_empty_roadmap_line(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = FinalReport(root)  # no roadmap.json at all
            self.assertIn("No tasks in roadmap.", report.generate())


class TestArchdocsMissingKeys(unittest.TestCase):
    """Slice 19 — missing domains/interfaces raise ValueError."""

    SAMPLE = {
        "domains": [{"name": "roles", "owner": "architect",
                     "files": ["x.py"]}],
        "interfaces": [{"module": "draupnir_forge.roles.auditor",
                        "functions": ["audit"]}],
        "invariants": [],
        "dependency_direction": {},
    }

    def test_render_architecture_missing_domains(self):
        with self.assertRaises(ValueError) as ctx:
            render_architecture({})
        self.assertIn("domains", str(ctx.exception))

    def test_render_interfaces_missing_interfaces(self):
        with self.assertRaises(ValueError) as ctx:
            render_interfaces({})
        self.assertIn("interfaces", str(ctx.exception))

    def test_render_architecture_empty_domains_list(self):
        arch = dict(self.SAMPLE, domains=[])
        with self.assertRaises(ValueError) as ctx:
            render_architecture(arch)
        self.assertIn("domains", str(ctx.exception))

    def test_valid_arch_still_renders(self):
        arch_text = render_architecture(self.SAMPLE)
        self.assertIn("# Architecture", arch_text)
        iface_text = render_interfaces(self.SAMPLE)
        self.assertIn("# Interfaces", iface_text)


class TestUpdateRoadmapStatusValidation(unittest.TestCase):
    """Slice 20 — unknown status raises ValueError listing valid words."""

    def test_unknown_status_raises(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError) as ctx:
                update_roadmap_status(tmp, "T-001", "bogus")
            message = str(ctx.exception)
            self.assertIn("bogus", message)
            self.assertIn("done", message)
            self.assertIn("pending", message)

    def test_valid_status_flips_marker_as_before(self):
        with TemporaryDirectory() as tmp:
            path = update_roadmap_status(tmp, "T-001", "done")
            self.assertIn("- [x] T-001", path.read_text(encoding="utf-8"))

    def test_status_words_normalized(self):
        with TemporaryDirectory() as tmp:
            path = update_roadmap_status(tmp, "T-002", " In-Progress ")
            self.assertIn("[~]", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
