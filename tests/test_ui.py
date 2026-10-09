"""Slice 31 acceptance tests: the Black-Box Progress Interface.

``ProgressView`` is a pure function of plain dicts, so these tests pin
its output with zero filesystem, zero TTY, and zero forge machinery.
The golden test below exercises every rendering rule: goal truncation,
percentage math, the in-progress objective, the 6-completed cap with
``+N more``, the 4-queued cap, the decisions list, and the health line.
"""

from __future__ import annotations

import unittest

from draupnir_forge.ui import ProgressView


def _golden_tasks() -> list[dict]:
    done_titles = [
        "Project definition",
        "Repository architecture",
        "File ingestion",
        "Markdown parser",
        "Search index",
        "Retrieval API",
        "CLI skeleton",
    ]
    tasks = [
        {"id": f"T-{i + 1:03d}", "title": title, "status": "done"}
        for i, title in enumerate(done_titles)
    ]
    tasks.append(
        {
            "id": "T-008",
            "title": "Verifying citation locations against source documents",
            "status": "in_progress",
        }
    )
    queued_titles = [
        "Answer synthesis",
        "UI",
        "Final integration tests",
        "Documentation polish",
        "Release packaging",
    ]
    tasks.extend(
        {"id": f"T-{i + 9:03d}", "title": title, "status": "ready"}
        for i, title in enumerate(queued_titles)
    )
    return tasks


class TestGoldenPanel(unittest.TestCase):
    def test_golden_output(self):
        state = {
            "phase": "VERIFYING",
            "goal": (
                "Local Research Assistant that builds "
                "citation-preserving retrieval pipelines"
            ),
            "decisions_needed": ["Approve production database migration"],
        }
        expected = "\n".join(
            [
                "DRAUPNIR FORGE",
                "Project: Local Research Assistant that builds "
                "citation-preserving re…",
                "Overall: 53%",
                "Current objective: Verifying citation locations against "
                "source documents",
                "Current phase: VERIFYING",
                "",
                "Completed:",
                "✓ Project definition",
                "✓ Repository architecture",
                "✓ File ingestion",
                "✓ Markdown parser",
                "✓ Search index",
                "✓ Retrieval API",
                "  +1 more",
                "",
                "Now:",
                "◉ Verifying citation locations against source documents",
                "",
                "Queued:",
                "○ Answer synthesis",
                "○ UI",
                "○ Final integration tests",
                "○ Documentation polish",
                "",
                "Human decisions needed:",
                "- Approve production database migration",
                "",
                "System health:",
                "Stable",
            ]
        )
        self.assertEqual(
            ProgressView.render(state, _golden_tasks(), "Stable"), expected
        )

    def test_golden_compact(self):
        state = {"phase": "VERIFYING"}
        expected = (
            "Draupnir Forge: 53% · phase VERIFYING · "
            "now: Verifying citation locations against source documents · "
            "7 done, 1 active, 5 queued"
        )
        self.assertEqual(
            ProgressView.render_compact(state, _golden_tasks()), expected
        )


class TestEdgeCases(unittest.TestCase):
    def test_empty_tasks_zero_percent(self):
        out = ProgressView.render({"phase": "INTAKE", "goal": ""}, [], "ok")
        self.assertIn("Overall: 0%", out)
        self.assertIn("Current objective: —", out)
        self.assertIn("  (none)", out)

    def test_no_current_task_shows_dash(self):
        tasks = [{"id": "T-1", "title": "A", "status": "ready"}]
        out = ProgressView.render({"phase": "INTAKE", "goal": "g"}, tasks)
        self.assertIn("Now:\n—", out)

    def test_no_decisions_shows_none(self):
        out = ProgressView.render({"phase": "INTAKE"}, [])
        self.assertIn("Human decisions needed:\nNone", out)

    def test_single_string_decision(self):
        out = ProgressView.render(
            {"phase": "INTAKE", "awaiting_human": "Pick a license"}, []
        )
        self.assertIn("- Pick a license", out)

    def test_status_vocabularies(self):
        tasks = [
            {"id": "a", "title": "A", "status": "completed"},
            {"id": "b", "title": "B", "status": "in-progress"},
            {"id": "c", "title": "C", "status": "todo"},
        ]
        out = ProgressView.render({"phase": "X"}, tasks)
        self.assertIn("✓ A", out)
        self.assertIn("◉ B", out)
        self.assertIn("○ C", out)
        self.assertIn("Overall: 33%", out)

    def test_compact_is_one_line(self):
        out = ProgressView.render_compact({"phase": "P"}, [])
        self.assertNotIn("\n", out)
        self.assertIn("0%", out)

    def test_non_dict_tasks_ignored(self):
        tasks = ["junk", None, {"id": "T-1", "title": "A", "status": "done"}]
        out = ProgressView.render({"phase": "P"}, tasks)
        self.assertIn("Overall: 100%", out)


if __name__ == "__main__":
    unittest.main()
