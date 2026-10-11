"""Dawn-forge 2026-10-11, Track B (slices 6-10): failures serialize, ui
sanitize, slug max length, empty goal, detect-tests non-null."""

import json
import os
import tempfile
import unittest

from draupnir_forge import failures
from draupnir_forge.failures import FailureClass, FailureRecord
from draupnir_forge.modes_existing import ExistingRepoMode
from draupnir_forge.modes_new import slugify_package_name
from draupnir_forge.roles import tester as tester_mod
from draupnir_forge.ui import ProgressView


class TestFailureRecordSerialize(unittest.TestCase):
    """Slice 6 — FailureRecord.to_dict()/from_dict() round-trip."""

    def test_round_trip_preserves_all_fields(self):
        record = FailureRecord(
            failure_class=FailureClass.TEST_FAILURE,
            task_id="D-007",
            ts=1728612345.5,
            detail="boom in the forge",
            attempt=3,
        )
        rebuilt = FailureRecord.from_dict(record.to_dict())
        self.assertEqual(rebuilt.failure_class, record.failure_class)
        self.assertEqual(rebuilt.task_id, record.task_id)
        self.assertEqual(rebuilt.ts, record.ts)
        self.assertEqual(rebuilt.detail, record.detail)
        self.assertEqual(rebuilt.attempt, record.attempt)

    def test_to_dict_is_json_safe(self):
        record = FailureRecord(failure_class=FailureClass.UNKNOWN)
        payload = json.dumps(record.to_dict())
        rebuilt = FailureRecord.from_dict(json.loads(payload))
        self.assertEqual(rebuilt.failure_class, FailureClass.UNKNOWN)

    def test_unknown_class_string_maps_to_unknown(self):
        rebuilt = FailureRecord.from_dict({
            "failure_class": "not_a_real_class",
            "task_id": "x",
            "ts": 1.0,
            "detail": "",
            "attempt": 1,
        })
        self.assertIs(rebuilt.failure_class, FailureClass.UNKNOWN)

    def test_known_class_string_resolves(self):
        rebuilt = FailureRecord.from_dict({
            "failure_class": "resource_limit",
            "task_id": "x",
            "ts": 1.0,
            "detail": "",
            "attempt": 2,
        })
        self.assertIs(rebuilt.failure_class, FailureClass.RESOURCE_LIMIT)

    def test_enum_instance_accepted(self):
        rebuilt = FailureRecord.from_dict({
            "failure_class": FailureClass.SECURITY_BLOCK,
            "task_id": "y",
            "ts": 2.0,
            "detail": "denied",
            "attempt": 1,
        })
        self.assertIs(rebuilt.failure_class, FailureClass.SECURITY_BLOCK)


class TestControlCharSanitize(unittest.TestCase):
    """Slice 7 — render()/render_compact() strip ANSI + C0/C1 controls."""

    def test_render_strips_ansi_from_title(self):
        tasks = [{"title": "\x1b[31mRED\x1b[0m", "status": "done"}]
        out = ProgressView.render({"phase": "X", "goal": "g"}, tasks, "ok")
        self.assertNotIn("\x1b", out)
        self.assertIn("RED", out)

    def test_render_strips_csi_cursor_sequences(self):
        tasks = [{"title": "\x1b[2J\x1b[Hclean", "status": "in_progress"}]
        out = ProgressView.render({"phase": "X", "goal": "g"}, tasks, "ok")
        self.assertNotIn("\x1b", out)
        self.assertIn("clean", out)

    def test_render_strips_control_chars_from_goal_and_health(self):
        state = {"phase": "X", "goal": "goal\x00with\x07bells\x1b[1m"}
        out = ProgressView.render(state, [], "hea\x08lth\x1b[0m")
        self.assertNotIn("\x1b", out)
        for ch in ("\x00", "\x07", "\x08"):
            self.assertNotIn(ch, out)
        self.assertIn("goalwithbells", out)
        self.assertIn("health", out)

    def test_render_compact_strips_ansi(self):
        tasks = [{"title": "\x1b[33mYELLOW\x1b[0m", "status": "in_progress"}]
        out = ProgressView.render_compact({"phase": "X"}, tasks)
        self.assertNotIn("\x1b", out)
        self.assertIn("YELLOW", out)

    def test_status_still_buckets_after_sanitize(self):
        tasks = [
            {"title": "a", "status": "\x1b[0mdone"},
            {"title": "b", "status": "in_progress\x1b[32m"},
        ]
        out = ProgressView.render_compact({"phase": "X"}, tasks)
        self.assertIn("1 done, 1 active, 0 queued", out)

    def test_no_control_chars_anywhere(self):
        state = {"phase": "P\x9bX", "goal": "g"}
        tasks = [{"title": "t\x85", "status": "queued\x0b"}]
        # The full panel is multi-line by design, so \n is exempt there;
        # every other C0/C1 control must be gone from both renderers.
        exempt = {0x0A}
        codes = [c for c in list(range(0x00, 0x20)) + list(range(0x7F, 0xA0))
                 if c not in exempt]
        out = ProgressView.render(state, tasks, "h\x7f")
        for code in codes:
            self.assertNotIn(chr(code), out, f"U+{code:04X} leaked")
        compact = ProgressView.render_compact(state, tasks)
        for code in list(range(0x00, 0x20)) + list(range(0x7F, 0xA0)):
            self.assertNotIn(chr(code), compact, f"U+{code:04X} leaked")


class TestSlugMaxLength(unittest.TestCase):
    """Slice 8 — slugify_package_name caps at 64 chars, stays an identifier."""

    def test_long_goal_capped_and_valid_identifier(self):
        goal = " ".join(f"supercalifragilisticexpialidocious{i}" for i in range(20))
        name = slugify_package_name(goal)
        self.assertLessEqual(len(name), 64)
        self.assertTrue(name.isidentifier(), name)
        self.assertFalse(name.endswith("_"))

    def test_short_names_unchanged(self):
        self.assertEqual(slugify_package_name("Build a todo list manager"),
                         "todo_list_manager")
        self.assertEqual(slugify_package_name("Create a new app for notes"),
                         "notes")

    def test_fallback_unchanged(self):
        self.assertEqual(slugify_package_name("the and of"), "forge_app")
        self.assertEqual(slugify_package_name(""), "forge_app")

    def test_digit_prefix_kept(self):
        # The slug regex drops leading digits ("3d" -> "d"), so the pkg_
        # branch is unreachable via public input; assert the pre-slice
        # behavior is preserved byte-for-byte.
        name = slugify_package_name("3d model viewer")
        self.assertEqual(name, "d_model_viewer")
        self.assertTrue(name[0].isalpha() or name.startswith("pkg_"))
        self.assertTrue(name.isidentifier())

    def test_truncation_never_leaves_trailing_underscore(self):
        # 3 words joined by underscores; truncation must rstrip any "_".
        goal = "a" * 60 + " b" * 40
        name = slugify_package_name(goal)
        self.assertLessEqual(len(name), 64)
        self.assertTrue(name.isidentifier())
        self.assertFalse(name.endswith("_"))


class TestEmptyGoal(unittest.TestCase):
    """Slice 9 — build_roadmap/run reject empty/blank goals with ValueError."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.mode = ExistingRepoMode(os.path.join(self.tmp.name, "repo"))

    def test_build_roadmap_empty_raises(self):
        with self.assertRaisesRegex(ValueError, "goal"):
            self.mode.build_roadmap("")

    def test_build_roadmap_whitespace_raises(self):
        with self.assertRaisesRegex(ValueError, "goal"):
            self.mode.build_roadmap("   \t\n  ")

    def test_run_empty_raises(self):
        with self.assertRaisesRegex(ValueError, "goal"):
            self.mode.run("")

    def test_run_whitespace_raises(self):
        with self.assertRaisesRegex(ValueError, "goal"):
            self.mode.run("  ")

    def test_normal_goal_still_works(self):
        repo = os.path.join(self.tmp.name, "repo")
        os.makedirs(os.path.join(repo, "src"), exist_ok=True)
        with open(os.path.join(repo, "src", "m.py"), "w") as fh:
            fh.write("X = 1\n")
        mode = ExistingRepoMode(repo)
        mode.map_repository()
        mode.identify_risks()
        tasks = mode.build_roadmap("Add caching to the demo engine.")
        self.assertTrue(tasks)


class TestDetectTestsNonNull(unittest.TestCase):
    """Slice 10 — detect_tests() returns a list, never None."""

    def test_none_maps_to_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            mode = ExistingRepoMode(tmp)
            original = tester_mod.detect_command
            try:
                tester_mod.detect_command = lambda _d: None
                result = mode.detect_tests()
            finally:
                tester_mod.detect_command = original
            self.assertIsNotNone(result)
            self.assertIsInstance(result, list)
            self.assertEqual(result, [])

    def test_command_returned_as_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            mode = ExistingRepoMode(tmp)
            original = tester_mod.detect_command
            try:
                tester_mod.detect_command = lambda _d: ["pytest", "-q"]
                result = mode.detect_tests()
            finally:
                tester_mod.detect_command = original
            self.assertEqual(result, ["pytest", "-q"])


if __name__ == "__main__":
    unittest.main()
