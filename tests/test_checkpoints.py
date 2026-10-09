"""Slice 28 tests: Checkpointer — git checkpoints and pause/resume."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from draupnir_forge.checkpoints import Checkpointer


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True,
        check=True).stdout.strip()


def _make_repo() -> Path:
    """A throwaway git repo with identity configured and one commit."""
    tmp = Path(tempfile.mkdtemp())
    _git(tmp, "init", "-q")
    _git(tmp, "config", "user.email", "forge@test.local")
    _git(tmp, "config", "user.name", "Draupnir Test")
    (tmp / "saga.txt").write_text("in the beginning\n", encoding="utf-8")
    _git(tmp, "add", "-A")
    _git(tmp, "commit", "-q", "-m", "first stone")
    return tmp


class TestCheckpoint(unittest.TestCase):
    def test_checkpoint_creates_commit_with_trailer(self):
        repo = _make_repo()
        (repo / "saga.txt").write_text("in the beginning\nmore saga\n",
                                       encoding="utf-8")
        checkpointer = Checkpointer(repo)
        commit_hash = checkpointer.checkpoint("T-003", "Wove the third thread")
        self.assertIsNotNone(commit_hash)
        self.assertEqual(len(commit_hash), 40)
        message = _git(repo, "log", "-1", "--format=%B")
        self.assertIn("Wove the third thread", message)
        self.assertIn("Forge-Task: T-003", message)

    def test_checkpoint_nothing_to_commit_returns_none(self):
        repo = _make_repo()
        checkpointer = Checkpointer(repo)
        self.assertIsNone(checkpointer.checkpoint("T-001", "quiet"))
        # Still one commit: no empty commit was made.
        self.assertEqual(_git(repo, "rev-list", "--count", "HEAD"), "1")

    def test_checkpoint_not_a_repo_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkpointer = Checkpointer(tmp)
            self.assertIsNone(checkpointer.checkpoint("T-001", "quiet"))


class TestRollback(unittest.TestCase):
    def test_rollback_needs_confirm(self):
        repo = _make_repo()
        checkpointer = Checkpointer(repo)
        with self.assertRaises(PermissionError):
            checkpointer.rollback()

    def test_rollback_resets_hard(self):
        repo = _make_repo()
        (repo / "saga.txt").write_text("changed\n", encoding="utf-8")
        checkpointer = Checkpointer(repo)
        new_hash = checkpointer.checkpoint("T-002", "second stone")
        self.assertEqual(_git(repo, "rev-list", "--count", "HEAD"), "2")
        rolled = checkpointer.rollback(confirm=True)
        self.assertEqual(_git(repo, "rev-list", "--count", "HEAD"), "1")
        self.assertNotEqual(rolled, new_hash)
        self.assertEqual((repo / "saga.txt").read_text(encoding="utf-8"),
                         "in the beginning\n")

    def test_rollback_bad_n_rejected(self):
        repo = _make_repo()
        with self.assertRaises(ValueError):
            Checkpointer(repo).rollback(n=0, confirm=True)

    def test_rollback_not_a_repo_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                Checkpointer(tmp).rollback(confirm=True)


class TestPauseResume(unittest.TestCase):
    def test_pause_and_resume_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkpointer = Checkpointer(tmp)
            self.assertFalse(checkpointer.is_paused())
            marker = checkpointer.pause("the night grows late")
            self.assertTrue(checkpointer.is_paused())
            self.assertEqual(marker.name, "PAUSED")
            raw = json.loads(marker.read_text(encoding="utf-8"))
            self.assertEqual(raw["reason"], "the night grows late")
            self.assertIn("paused_at", raw)
            self.assertIn("snapshot", raw)
            record = checkpointer.resume()
            self.assertEqual(record["reason"], "the night grows late")
            self.assertFalse(checkpointer.is_paused())
            self.assertFalse(marker.exists())

    def test_resume_when_not_paused_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                Checkpointer(tmp).resume()

    def test_pause_snapshot_holds_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            from draupnir_forge.memory import ProjectMemory
            memory = ProjectMemory(tmp)
            memory.ensure_skeleton()
            memory.write_doc("evidence/clue.md", "a clue")
            checkpointer = Checkpointer(tmp)
            checkpointer.pause("rest")
            record = checkpointer.resume()
            docs = record["snapshot"]["docs"]
            self.assertIn("SYSTEM_VISION.md", docs)
            self.assertEqual(
                record["snapshot"]["evidence"]["evidence/clue.md"], "a clue")


if __name__ == "__main__":
    unittest.main()
