"""Slice 1 acceptance tests: package scaffold is importable and versioned."""

from __future__ import annotations

import subprocess
import sys
import unittest


class TestScaffold(unittest.TestCase):
    def test_package_imports(self):
        import draupnir_forge
        self.assertTrue(draupnir_forge.__version__)

    def test_twelve_laws_present(self):
        from draupnir_forge import FORGE_LAWS
        self.assertEqual(len(FORGE_LAWS), 12)

    def test_version_flag(self):
        from draupnir_forge import __version__
        out = subprocess.run(
            [sys.executable, "-m", "draupnir_forge", "--version"],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(out.returncode, 0)
        self.assertIn(__version__, out.stdout)

    def test_no_command_prints_help(self):
        out = subprocess.run(
            [sys.executable, "-m", "draupnir_forge"],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(out.returncode, 0)
        self.assertIn("draupnir", out.stdout.lower())

    def test_unknown_subcommand_exits_2(self):
        out = subprocess.run(
            [sys.executable, "-m", "draupnir_forge", "frobnicate"],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(out.returncode, 2)


if __name__ == "__main__":
    unittest.main()
