"""Slice 10 tests: Skald intent interpreter."""

from __future__ import annotations

import os
import tempfile
import unittest

from draupnir_forge.roles.base import RoleContext, registry
from draupnir_forge.roles.skald import (
    Vision,
    interpret,
    load_prompts,
    Skald,
)

import draupnir_forge.roles.skald as skald_module  # noqa: E402  (registers role)


SAMPLE_INTENT = (
    "Build a fast static site generator in Rust. "
    "It must render Markdown to HTML and it must support themes, "
    "because theming is critical for adoption. "
    "It should produce valid HTML5 and should finish a 1000-page site "
    "in under five seconds. "
    "Do not include a plugin system; plugins are out of scope. "
    "Should it support Windows or Linux only? "
    "Handle images and stuff somehow."
)


class TestInterpret(unittest.TestCase):
    def test_goal_is_first_substantive_sentence(self):
        vision = interpret(SAMPLE_INTENT)
        self.assertEqual(vision.goal,
                         "Build a fast static site generator in Rust.")

    def test_priorities_extracted(self):
        vision = interpret(SAMPLE_INTENT)
        self.assertEqual(len(vision.priorities), 1)
        self.assertIn("must render Markdown", vision.priorities[0])
        self.assertIn("critical", vision.priorities[0])

    def test_non_goals_extracted(self):
        vision = interpret(SAMPLE_INTENT)
        self.assertEqual(len(vision.non_goals), 1)
        self.assertIn("out of scope", vision.non_goals[0])

    def test_success_criteria_extracted(self):
        vision = interpret(SAMPLE_INTENT)
        self.assertEqual(len(vision.success_criteria), 2)

    def test_ambiguities_flagged(self):
        vision = interpret(SAMPLE_INTENT)
        # the question + the vague "stuff somehow" sentence
        self.assertEqual(len(vision.ambiguities), 2)
        self.assertTrue(any("?" in a for a in vision.ambiguities))
        self.assertTrue(any("stuff" in a for a in vision.ambiguities))

    def test_empty_text_yields_empty_vision(self):
        for text in ("", "   ", "\n"):
            vision = interpret(text)
            self.assertIsInstance(vision, Vision)
            self.assertEqual(vision.goal, "")
            self.assertEqual(vision.priorities, [])

    def test_single_sentence(self):
        vision = interpret("Write a saga.")
        self.assertEqual(vision.goal, "Write a saga.")

    def test_never_raises(self):
        interpret(None)  # type: ignore[arg-type]


class TestLoadPrompts(unittest.TestCase):
    def test_prompts_yaml_loads(self):
        prompts = load_prompts()
        self.assertIn("vision_extraction", prompts)
        user = prompts["vision_extraction"]["user"]
        self.assertIn("{goal_text}", user)

    def test_all_templates_have_placeholder(self):
        prompts = load_prompts()
        for name, template in prompts.items():
            self.assertIn("{goal_text}", template.get("user", ""),
                          f"template {name} lacks {{goal_text}}")


class _FakeTask:
    def __init__(self, goal: str):
        self.goal = goal


class TestSkaldRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_run_writes_vision_md(self):
        ctx = RoleContext(project_dir=self.tmp.name,
                          task=_FakeTask(SAMPLE_INTENT))
        result = Skald().run(ctx)
        self.assertTrue(result.ok, result.summary)
        path = os.path.join(self.tmp.name, ".mythis", "SYSTEM_VISION.md")
        self.assertEqual(result.artifacts["vision_path"], path)
        self.assertTrue(os.path.isfile(path))
        with open(path, encoding="utf-8") as handle:
            content = handle.read()
        self.assertIn("# System Vision", content)
        self.assertIn("## Goal", content)
        self.assertIn("## Open ambiguities", content)
        self.assertIn("static site generator", content)
        vision = result.artifacts["vision"]
        self.assertIsInstance(vision, Vision)
        self.assertIn("static site generator", vision.goal)

    def test_run_reads_goal_from_artifacts(self):
        ctx = RoleContext(project_dir=self.tmp.name,
                          artifacts={"goal_text": "Forge a hammer."})
        result = Skald().run(ctx)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(result.artifacts["vision"].goal, "Forge a hammer.")

    def test_run_no_goal_is_not_ok(self):
        ctx = RoleContext(project_dir=self.tmp.name)
        result = Skald().run(ctx)
        self.assertFalse(result.ok)
        self.assertIsNotNone(result.escalation)

    def test_registered(self):
        self.assertIn("skald", registry.names())
        self.assertEqual(registry.create("skald").purpose,
                         "interprets human intent into explicit vision")


if __name__ == "__main__":
    unittest.main()
