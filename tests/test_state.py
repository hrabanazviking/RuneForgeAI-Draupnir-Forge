"""Slice 6 acceptance tests: transition validation, corrupt-file recovery."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime

from draupnir_forge.state import (
    ALLOWED_TRANSITIONS,
    INITIAL_PHASE,
    PHASES,
    TERMINAL_PHASE,
    IllegalTransition,
    ProjectState,
)


class TestTransitionMapIsData(unittest.TestCase):
    def test_map_covers_all_phases(self):
        self.assertEqual(set(ALLOWED_TRANSITIONS), PHASES)
        self.assertIn(INITIAL_PHASE, PHASES)
        self.assertIn(TERMINAL_PHASE, PHASES)

    def test_expected_edges_present(self):
        self.assertEqual(ALLOWED_TRANSITIONS["INTAKE"], frozenset({"DISCOVERY"}))
        self.assertEqual(
            ALLOWED_TRANSITIONS["VERIFYING"],
            frozenset({"COMPLETE_TASK", "REPAIR", "REPLAN", "HUMAN_DECISION"}),
        )
        self.assertEqual(
            ALLOWED_TRANSITIONS["REGROUNDING"],
            frozenset({"TASK_READY", "ROADMAP", "ARCHITECTURE", "DEFINITION"}),
        )
        self.assertEqual(
            ALLOWED_TRANSITIONS["TASK_READY"],
            frozenset({"IMPLEMENTING", "PROJECT_COMPLETE"}),
        )
        self.assertEqual(ALLOWED_TRANSITIONS["PROJECT_COMPLETE"], frozenset())

    def test_full_happy_path_walkable(self):
        path = [
            "INTAKE", "DISCOVERY", "DEFINITION", "ARCHITECTURE", "ROADMAP",
            "TASK_READY", "IMPLEMENTING", "REVIEWING", "TESTING", "VERIFYING",
            "COMPLETE_TASK", "DOCUMENTING", "REGROUNDING", "TASK_READY",
            "PROJECT_COMPLETE",
        ]
        for current, nxt in zip(path, path[1:]):
            self.assertIn(
                nxt, ALLOWED_TRANSITIONS[current],
                f"{current} -> {nxt} should be legal",
            )


class TestProjectState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="forge-state-")
        self.project_dir = os.path.join(self.tmp, "myproject")
        self.state = ProjectState(self.project_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _state_path(self) -> str:
        return os.path.join(self.project_dir, ".mythis", "PROJECT_STATE.json")

    def test_fresh_state_defaults(self):
        self.assertEqual(self.state.phase, "INTAKE")
        self.assertEqual(self.state.goal, "")
        self.assertIsNone(self.state.task_id)
        self.assertEqual(self.state.counters, {})
        parsed = datetime.fromisoformat(self.state.updated_ts)
        self.assertIsNotNone(parsed.tzinfo)

    def test_state_file_created(self):
        self.assertTrue(os.path.isfile(self._state_path()))

    def test_legal_transitions_walk(self):
        self.assertEqual(self.state.transition("DISCOVERY"), "DISCOVERY")
        self.assertEqual(self.state.phase, "DISCOVERY")
        self.state.transition("DEFINITION")
        self.state.transition("ARCHITECTURE")
        self.state.transition("ROADMAP")
        self.state.transition("TASK_READY")
        self.state.transition("IMPLEMENTING")
        self.assertEqual(self.state.phase, "IMPLEMENTING")
        # State file on disk reflects the walk.
        with open(self._state_path(), encoding="utf-8") as handle:
            on_disk = json.load(handle)
        self.assertEqual(on_disk["phase"], "IMPLEMENTING")

    def test_illegal_transition_raises(self):
        with self.assertRaises(IllegalTransition):
            self.state.transition("IMPLEMENTING")  # INTAKE -> IMPLEMENTING
        self.assertEqual(self.state.phase, "INTAKE")  # unchanged

    def test_unknown_phase_raises(self):
        with self.assertRaises(IllegalTransition):
            self.state.transition("VALHALLA")

    def test_verify_branches(self):
        walk = ("DISCOVERY", "DEFINITION", "ARCHITECTURE", "ROADMAP",
                "TASK_READY", "IMPLEMENTING", "REVIEWING", "TESTING",
                "VERIFYING")
        for branch in ("REPAIR", "HUMAN_DECISION", "REPLAN", "COMPLETE_TASK"):
            proj = os.path.join(self.tmp, f"branch-{branch}")
            st = ProjectState(proj)
            for phase in walk:
                st.transition(phase)
            self.assertEqual(st.transition(branch), branch)

    def test_repair_loops_back_to_implementing(self):
        for phase in ("DISCOVERY", "DEFINITION", "ARCHITECTURE", "ROADMAP",
                      "TASK_READY", "IMPLEMENTING", "REVIEWING", "TESTING",
                      "VERIFYING"):
            self.state.transition(phase)
        self.state.transition("REPAIR")
        self.state.transition("IMPLEMENTING")
        self.assertEqual(self.state.phase, "IMPLEMENTING")

    def test_complete_from_any_phase(self):
        self.state.transition("DISCOVERY")
        self.assertEqual(self.state.complete(), "PROJECT_COMPLETE")
        self.assertEqual(self.state.phase, "PROJECT_COMPLETE")

    def test_complete_from_intake(self):
        self.assertEqual(self.state.complete(), "PROJECT_COMPLETE")

    def test_transition_out_of_terminal_raises(self):
        self.state.complete()
        with self.assertRaises(IllegalTransition):
            self.state.transition("TASK_READY")

    def test_direct_jump_to_complete_only_via_complete(self):
        self.state.transition("DISCOVERY")
        with self.assertRaises(IllegalTransition):
            self.state.transition("PROJECT_COMPLETE")
        # TASK_READY is the one phase allowed to transition there directly.
        st = ProjectState(os.path.join(self.tmp, "other"))
        for phase in ("DISCOVERY", "DEFINITION", "ARCHITECTURE",
                      "ROADMAP", "TASK_READY"):
            st.transition(phase)
        self.assertEqual(st.transition("PROJECT_COMPLETE"), "PROJECT_COMPLETE")

    def test_update_goal_task_and_counters(self):
        self.state.update(goal="Build the forge", task_id="slice-6")
        self.assertEqual(self.state.goal, "Build the forge")
        self.assertEqual(self.state.task_id, "slice-6")
        self.state.update(counters={"tasks_done": 3})
        self.assertEqual(self.state.counters, {"tasks_done": 3})

    def test_update_rejects_phase(self):
        with self.assertRaises(ValueError):
            self.state.update(phase="DISCOVERY")

    def test_update_rejects_unknown_field(self):
        with self.assertRaises(KeyError):
            self.state.update(bogus_field=1)

    def test_update_rejects_bad_types(self):
        with self.assertRaises(TypeError):
            self.state.update(goal=123)
        with self.assertRaises(TypeError):
            self.state.update(counters={"x": "not-an-int"})

    def test_increment(self):
        self.assertEqual(self.state.increment("tasks_done"), 1)
        self.assertEqual(self.state.increment("tasks_done"), 2)
        self.assertEqual(self.state.increment("repairs"), 1)
        self.assertEqual(
            self.state.counters, {"tasks_done": 2, "repairs": 1}
        )

    def test_increment_rejects_empty_name(self):
        with self.assertRaises(ValueError):
            self.state.increment("")

    def test_snapshot_is_deep_copy(self):
        self.state.update(goal="g", counters={"a": 1})
        snap = self.state.snapshot()
        self.assertEqual(snap["phase"], "INTAKE")
        self.assertEqual(snap["counters"], {"a": 1})
        snap["counters"]["a"] = 999
        self.assertEqual(self.state.counters, {"a": 1})

    def test_state_survives_reopen(self):
        self.state.update(goal="persist me", task_id="t-1")
        self.state.transition("DISCOVERY")
        self.state.increment("tasks_done")
        reopened = ProjectState(self.project_dir)
        self.assertEqual(reopened.phase, "DISCOVERY")
        self.assertEqual(reopened.goal, "persist me")
        self.assertEqual(reopened.task_id, "t-1")
        self.assertEqual(reopened.counters, {"tasks_done": 1})

    def test_updated_ts_refreshes_on_mutation(self):
        before = self.state.updated_ts
        self.state.increment("x")
        after = self.state.updated_ts
        self.assertGreaterEqual(after, before)

    def test_corrupt_file_recovery(self):
        path = self._state_path()
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{ this is not valid json !!!")
        recovered = ProjectState(self.project_dir)
        # Fresh default state, never a crash.
        self.assertEqual(recovered.phase, "INTAKE")
        self.assertEqual(recovered.goal, "")
        # The corrupt original was renamed aside with a timestamp.
        siblings = os.listdir(os.path.dirname(path))
        backups = [n for n in siblings if n.startswith(
            "PROJECT_STATE.json.corrupt.")]
        self.assertEqual(len(backups), 1)
        # And the live state file is valid JSON again.
        with open(path, encoding="utf-8") as handle:
            json.load(handle)

    def test_non_dict_json_recovery(self):
        path = self._state_path()
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('["not", "a", "dict"]')
        recovered = ProjectState(self.project_dir)
        self.assertEqual(recovered.phase, "INTAKE")

    def test_unknown_phase_in_file_recovery(self):
        path = self._state_path()
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"phase": "VALHALLA", "goal": "x"}, handle)
        recovered = ProjectState(self.project_dir)
        self.assertEqual(recovered.phase, "INTAKE")

    def test_cli_style_updated_key_is_accepted_on_load(self):
        # Slice 4's CLI writes "updated" instead of "updated_ts".
        path = self._state_path()
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(
                {"phase": "ROADMAP", "goal": "g",
                 "updated": "2026-10-09T00:00:00+00:00"},
                handle,
            )
        loaded = ProjectState(self.project_dir)
        self.assertEqual(loaded.phase, "ROADMAP")
        self.assertEqual(loaded.updated_ts, "2026-10-09T00:00:00+00:00")

    def test_saved_file_mirrors_updated_for_cli(self):
        self.state.update(goal="g")
        with open(self._state_path(), encoding="utf-8") as handle:
            on_disk = json.load(handle)
        self.assertEqual(on_disk["updated"], on_disk["updated_ts"])
        self.assertEqual(on_disk["updated"], self.state.updated_ts)

    def test_update_rejects_updated_alias(self):
        with self.assertRaises(ValueError):
            self.state.update(updated="2026-10-09T00:00:00+00:00")

    def test_partial_file_heals_missing_fields(self):
        path = self._state_path()
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"phase": "DISCOVERY"}, handle)
        healed = ProjectState(self.project_dir)
        self.assertEqual(healed.phase, "DISCOVERY")
        self.assertEqual(healed.goal, "")
        self.assertEqual(healed.counters, {})


if __name__ == "__main__":
    unittest.main()
