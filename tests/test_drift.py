"""Slice 36 tests: DriftDetector — baseline vs live repo, reporting."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from draupnir_forge.drift import (
    DriftDetector,
    DriftFinding,
    DriftKind,
    SEVERITY_HIGH,
    SEVERITY_MEDIUM,
    scan_public_api,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _architecture() -> dict:
    return {
        "domains": [
            {"name": "core", "owner": "worker",
             "files": ["src/core/a.py"]},
            {"name": "web", "owner": "worker",
             "files": ["src/web/b.py"]},
            {"name": "gone", "owner": "worker",
             "files": ["gone/x.py"]},
        ],
        "interfaces": [],
        "invariants": [],
        "dependency_direction": {"core": [], "web": ["core"], "gone": []},
    }


class DriftDetectorTest(unittest.TestCase):
    """Planted drift in a fixture repo must be found by compare()."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        _write(self.root / ".mythis" / "architecture.json",
               json.dumps(_architecture()))
        # Live code: core illegally reaches into web (not allowed by
        # dependency_direction); web's legal reach into core stays quiet.
        _write(self.root / "src" / "core" / "a.py",
               "from src.web.b import other\n\n\n"
               "def new_func():\n    return 1\n")
        _write(self.root / "src" / "web" / "b.py",
               "from src.core.a import new_func\n")
        # A stray new top-level dir, and one big enough to be a domain.
        _write(self.root / "extra" / "x.py", "VALUE = 1\n")
        for i in range(6):
            _write(self.root / "huge" / f"m{i}.py", f"V{i} = {i}\n")
        # The public API changed: old_func vanished from the snapshot.
        _write(self.root / ".mythis" / "api_snapshot.json",
               json.dumps({"src.core.a": ["old_func"],
                           "src.web.b": []}))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def kinds(self, findings):
        return {f.kind for f in findings}

    def test_compare_detects_planted_drift(self) -> None:
        findings = DriftDetector(self.root).compare()
        kinds = self.kinds(findings)
        self.assertIn(DriftKind.NEW_TOP_LEVEL_DIR, kinds)
        self.assertIn(DriftKind.NEW_DOMAIN, kinds)
        self.assertIn(DriftKind.MISSING_DOMAIN, kinds)
        self.assertIn(DriftKind.CROSS_DOMAIN_IMPORT, kinds)
        self.assertIn(DriftKind.INTERFACE_CHANGED, kinds)

    def test_cross_domain_import_detail_names_domains(self) -> None:
        findings = DriftDetector(self.root).compare()
        cross = [f for f in findings
                 if f.kind is DriftKind.CROSS_DOMAIN_IMPORT]
        self.assertEqual(len(cross), 1)
        self.assertIn("core", cross[0].detail)
        self.assertIn("web", cross[0].detail)
        self.assertEqual(cross[0].severity, SEVERITY_HIGH)

    def test_new_dir_vs_new_domain_threshold(self) -> None:
        findings = DriftDetector(self.root).compare()
        new_dirs = [f for f in findings
                    if f.kind is DriftKind.NEW_TOP_LEVEL_DIR]
        new_domains = [f for f in findings
                       if f.kind is DriftKind.NEW_DOMAIN]
        self.assertEqual([f.detail for f in new_dirs],
                         [f.detail for f in new_dirs if "'extra/'" in f.detail])
        self.assertTrue(all("'huge/'" in f.detail for f in new_domains))
        self.assertTrue(all(f.severity == SEVERITY_HIGH
                            for f in new_domains))

    def test_missing_domain_is_high(self) -> None:
        findings = DriftDetector(self.root).compare()
        missing = [f for f in findings
                   if f.kind is DriftKind.MISSING_DOMAIN]
        self.assertEqual(len(missing), 1)
        self.assertIn("'gone/'", missing[0].detail)
        self.assertEqual(missing[0].severity, SEVERITY_HIGH)

    def test_interface_removal_is_high(self) -> None:
        findings = DriftDetector(self.root).compare()
        changed = [f for f in findings
                   if f.kind is DriftKind.INTERFACE_CHANGED]
        core = [f for f in changed if "'src.core.a'" in f.detail]
        self.assertEqual(len(core), 1)
        self.assertIn("old_func", core[0].detail)
        self.assertEqual(core[0].severity, SEVERITY_HIGH)

    def test_no_baseline_returns_empty(self) -> None:
        (self.root / ".mythis" / "architecture.json").unlink()
        self.assertEqual(DriftDetector(self.root).compare(), [])

    def test_clean_repo_has_no_findings(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            root = Path(other)
            arch = _architecture()
            # Drop the "gone" domain so nothing is missing.
            arch["domains"] = [d for d in arch["domains"]
                               if d["name"] != "gone"]
            arch["dependency_direction"].pop("gone", None)
            _write(root / ".mythis" / "architecture.json",
                   json.dumps(arch))
            _write(root / "src" / "core" / "a.py",
                   "def new_func():\n    return 1\n")
            _write(root / "src" / "web" / "b.py",
                   "from src.core.a import new_func\n")
            _write(root / ".mythis" / "api_snapshot.json",
                   json.dumps(scan_public_api(root)))
            self.assertEqual(DriftDetector(root).compare(), [])

    def test_report_appends_known_issues_and_signals_high(self) -> None:
        detector = DriftDetector(self.root)
        findings = detector.compare()
        self.assertTrue(detector.report(findings))
        text = (self.root / ".mythis" / "KNOWN_ISSUES.md").read_text(
            encoding="utf-8")
        self.assertIn("cross_domain_import", text)
        self.assertIn("missing_domain", text)
        self.assertIn("[high]", text)

    def test_report_returns_false_without_high_severity(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            root = Path(other)
            arch = _architecture()
            arch["domains"] = [d for d in arch["domains"]
                               if d["name"] != "gone"]
            arch["dependency_direction"].pop("gone", None)
            _write(root / ".mythis" / "architecture.json",
                   json.dumps(arch))
            _write(root / "src" / "core" / "a.py", "X = 1\n")
            _write(root / "src" / "web" / "b.py", "Y = 2\n")
            _write(root / "extra" / "x.py", "Z = 3\n")  # medium only
            detector = DriftDetector(root)
            findings = detector.compare()
            self.assertTrue(findings)
            self.assertTrue(all(f.severity == SEVERITY_MEDIUM
                                for f in findings))
            self.assertFalse(detector.report(findings))
            text = (root / ".mythis" / "KNOWN_ISSUES.md").read_text(
                encoding="utf-8")
            self.assertIn("new_top_level_dir", text)

    def test_finding_round_trip(self) -> None:
        finding = DriftFinding(kind=DriftKind.NEW_DOMAIN, detail="d",
                               severity=SEVERITY_HIGH)
        clone = DriftFinding.from_dict(finding.to_dict())
        self.assertEqual(clone.kind, finding.kind)
        self.assertEqual(clone.detail, finding.detail)
        self.assertEqual(clone.severity, finding.severity)

    def test_scan_public_api(self) -> None:
        api = scan_public_api(self.root)
        self.assertIn("new_func", api.get("src.core.a", []))
        self.assertNotIn("other", api.get("src.core.a", []))


if __name__ == "__main__":
    unittest.main()
