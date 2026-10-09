"""Slice 33 tests: DiscoveryReport."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from draupnir_forge.discovery import DiscoveryReport


def _build_project(tmp: str) -> Path:
    """A small fixture project with tests, CI, deps, docs, entry points."""
    root = Path(tmp)
    (root / "src" / "app").mkdir(parents=True)
    (root / "src" / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "src" / "app" / "main.py").write_text(
        '"""Entry point."""\n\n\ndef main():\n    print("hi")\n',
        encoding="utf-8",
    )
    (root / "src" / "lib").mkdir(parents=True)
    (root / "src" / "lib" / "util.py").write_text("X = 1\n", encoding="utf-8")
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_main.py").write_text(
        "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_x(self):\n"
        '        self.assertTrue(True)\n',
        encoding="utf-8",
    )
    (root / "requirements.txt").write_text("requests==2.31.0\n", encoding="utf-8")
    (root / "README.md").write_text("# Demo\n", encoding="utf-8")
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
    (root / "Makefile").write_text("test:\n\tpytest\n", encoding="utf-8")
    ci = root / ".github" / "workflows"
    ci.mkdir(parents=True)
    (ci / "ci.yml").write_text("name: ci\n", encoding="utf-8")
    # A binary blob over 1 MiB.
    (root / "assets").mkdir(parents=True)
    (root / "assets" / "blob.bin").write_bytes(b"\x00\xff" * 600_000)
    # A text file over 5000 lines.
    (root / "src" / "lib" / "huge.py").write_text(
        "".join(f"x{i} = {i}\n" for i in range(6000)), encoding="utf-8"
    )
    return root


class TestDiscoveryReport(unittest.TestCase):
    def test_generate_covers_all_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            _build_project(tmp)
            report = DiscoveryReport(tmp, {})
            text = report.generate()
        for section in (
            "File inventory by language",
            "Entry points",
            "Test directories",
            "Dependency files",
            "Domains",
            "Documentation inventory",
            "Runtime / build state",
            "Risks",
        ):
            self.assertIn(section, text)
        # §2 bullets: language table, entry points, test command, domains.
        self.assertIn("| python |", text)
        self.assertIn("src/app/main.py", text)
        self.assertIn("Test command:", text)
        self.assertIn("| src |", text)
        self.assertIn("README.md", text)
        self.assertIn("Makefile", text)
        self.assertIn(".github/workflows/ci.yml", text)

    def test_uses_cartographer_shaped_domain_map(self):
        domain_map = {
            "files": [
                {"path": "src/app/main.py", "language": "python", "size": 40},
            ],
            "entry_points": ["src/app/main.py"],
            "test_dirs": ["tests"],
            "dep_files": ["requirements.txt"],
            "domains": {
                "src": {
                    "owner": "architect",
                    "files": ["src/app/main.py"],
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            report = DiscoveryReport(tmp, domain_map)
            self.assertEqual(report.entry_points(), ["src/app/main.py"])
            self.assertEqual(report.test_dirs(), ["tests"])
            self.assertEqual(report.dep_files(), ["requirements.txt"])
            domains = report.domains()
            self.assertEqual(domains["src"]["owner"], "architect")

    def test_risks(self):
        with tempfile.TemporaryDirectory() as tmp:
            _build_project(tmp)
            report = DiscoveryReport(tmp, {})
            risks = report.risks()
        joined = "\n".join(risks)
        self.assertIn("5000 lines", joined)
        self.assertIn("huge.py", joined)
        self.assertIn("1 MiB", joined)
        self.assertIn("blob.bin", joined)
        # Pinned requirements + CI + README + tests -> those risks absent.
        self.assertNotIn("No tests detected", joined)
        self.assertNotIn("No CI config", joined)
        self.assertNotIn("No dependency pinning", joined)
        self.assertNotIn("No README", joined)

    def test_risks_on_empty_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "lonely.py").write_text("x = 1\n", encoding="utf-8")
            report = DiscoveryReport(tmp, {})
            risks = report.risks()
        joined = "\n".join(risks)
        self.assertIn("No tests detected", joined)
        self.assertIn("No CI config", joined)
        self.assertIn("No dependency pinning", joined)
        self.assertIn("No README", joined)

    def test_write_returns_path_and_risks(self):
        with tempfile.TemporaryDirectory() as tmp:
            _build_project(tmp)
            report = DiscoveryReport(tmp, {})
            result = report.write()
            path = Path(result["report_path"])
            self.assertEqual(path.name, "DISCOVERY_REPORT.md")
            self.assertEqual(path.parent.name, ".mythis")
            text = path.read_text(encoding="utf-8")
        self.assertIn("report_path", result)
        self.assertIn("risks", result)
        self.assertIn("# Discovery Report", text)
        self.assertTrue(any("5000 lines" in r for r in result["risks"]))

    def test_generate_never_raises_on_missing_dir(self):
        report = DiscoveryReport("/nonexistent/project/dir", {})
        text = report.generate()
        self.assertIn("# Discovery Report", text)


if __name__ == "__main__":
    unittest.main()
