"""tests/test_machine.py — slice 19: Forge state machine."""

import shutil
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime

from draupnir_forge.events import EventLog, EventType
from draupnir_forge.machine import (
    INITIAL_STATE,
    STATE_EVENT_MAP,
    STATE_ROLE_MAP,
    STATES,
    TERMINAL_STATES,
    TRANSITIONS,
    ForgeMachine,
    IllegalTransition,
)
from draupnir_forge.state import ALLOWED_TRANSITIONS


@contextmanager
def temp_project():
    """A fresh, cleaned-up project directory for one test."""
    tmp = tempfile.mkdtemp(prefix="forge-machine-")
    try:
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# The seventeen states, exactly as the task specifies, in lifecycle order.
EXPECTED_STATES = [
    "INTAKE", "DISCOVERY", "DEFINITION", "ARCHITECTURE", "ROADMAP",
    "TASK_READY", "IMPLEMENTING", "REVIEWING", "TESTING", "VERIFYING",
    "COMPLETE_TASK", "REPAIR", "REPLAN", "HUMAN_DECISION", "DOCUMENTING",
    "REGROUNDING", "PROJECT_COMPLETE",
]

HAPPY_PATH = [
    "DISCOVERY", "DEFINITION", "ARCHITECTURE", "ROADMAP", "TASK_READY",
    "IMPLEMENTING", "REVIEWING", "TESTING", "VERIFYING", "COMPLETE_TASK",
    "DOCUMENTING", "REGROUNDING", "TASK_READY", "PROJECT_COMPLETE",
]


class TestStatesAndTransitions(unittest.TestCase):
    def test_states_exactly_seventeen(self):
        self.assertEqual(STATES, EXPECTED_STATES)

    def test_transitions_come_from_state_py(self):
        # Module DATA, imported — never duplicated.
        self.assertEqual(set(TRANSITIONS), set(ALLOWED_TRANSITIONS))
        for state, targets in ALLOWED_TRANSITIONS.items():
            self.assertIsInstance(TRANSITIONS[state], list)
            self.assertEqual(set(TRANSITIONS[state]), set(targets))

    def test_initial_and_terminal(self):
        self.assertEqual(INITIAL_STATE, "INTAKE")
        self.assertEqual(TERMINAL_STATES, frozenset({"PROJECT_COMPLETE"}))

    def test_state_role_map_covers_all_states(self):
        self.assertEqual(set(STATE_ROLE_MAP), set(STATES))
        expected = {
            "DISCOVERY": "cartographer",
            "DEFINITION": "skald",
            "ARCHITECTURE": "architect",
            "ROADMAP": "planner",
            "IMPLEMENTING": "forge_worker",
            "REVIEWING": "auditor",
            "TESTING": "tester",
            "VERIFYING": "verifier",
            "DOCUMENTING": "scribe",
        }
        for state, role in expected.items():
            self.assertEqual(STATE_ROLE_MAP[state], role, state)
        for state in STATES:
            if state not in expected:
                self.assertIsNone(STATE_ROLE_MAP[state], state)


class TestForgeMachine(unittest.TestCase):
    def test_starts_at_intake(self):
        with temp_project() as project:
            machine = ForgeMachine(project)
            self.assertEqual(machine.current(), "INTAKE")

    def test_happy_path_walk(self):
        with temp_project() as project:
            machine = ForgeMachine(project)
            for target in HAPPY_PATH:
                machine.go(target)
                self.assertEqual(machine.current(), target)
            self.assertTrue(machine.is_terminal())

    def test_illegal_transition_rejected(self):
        with temp_project() as project:
            machine = ForgeMachine(project)
            with self.assertRaises(IllegalTransition):
                machine.go("IMPLEMENTING")  # INTAKE -> IMPLEMENTING illegal
            self.assertEqual(machine.current(), "INTAKE")  # unchanged

    def test_unknown_state_rejected(self):
        with temp_project() as project:
            machine = ForgeMachine(project)
            with self.assertRaises(IllegalTransition):
                machine.go("VALHALLA")

    def test_same_state_is_noop(self):
        with temp_project() as project:
            machine = ForgeMachine(project)
            machine.go("INTAKE")  # no-op, not an error
            self.assertEqual(machine.current(), "INTAKE")

    def test_complete_from_any_state(self):
        with temp_project() as project:
            machine = ForgeMachine(project)
            machine.go("PROJECT_COMPLETE")  # explicit completion bypasses map
            self.assertTrue(machine.is_terminal())

    def test_human_decision_interrupt_from_any_state(self):
        with temp_project() as project:
            machine = ForgeMachine(project)
            machine.go("DISCOVERY")
            machine.go("DEFINITION")
            machine.go("ARCHITECTURE")
            machine.go("ROADMAP")
            machine.go("TASK_READY")
            machine.go("IMPLEMENTING")
            machine.go("HUMAN_DECISION")  # interrupt bypasses the map
            self.assertEqual(machine.current(), "HUMAN_DECISION")
            self.assertFalse(machine.is_terminal())
            machine.go("TASK_READY")  # resume is a legal map transition
            self.assertEqual(machine.current(), "TASK_READY")

    def test_persists_across_instances(self):
        with temp_project() as project:
            machine = ForgeMachine(project)
            machine.go("DISCOVERY")
            machine.go("DEFINITION")
            resumed = ForgeMachine(project)
            self.assertEqual(resumed.current(), "DEFINITION")

    def test_allowed_from(self):
        with temp_project() as project:
            machine = ForgeMachine(project)
            self.assertEqual(machine.allowed_from("INTAKE"), ["DISCOVERY"])
            self.assertEqual(machine.allowed_from("PROJECT_COMPLETE"), [])
            self.assertEqual(machine.allowed_from("NOPE"), [])

    def test_state_change_emits_event(self):
        with temp_project() as project:
            log = EventLog(project)
            machine = ForgeMachine(project, event_log=log)
            machine.go("DISCOVERY")
            events = log.query(type=EventType.DOMAIN_DISCOVERED)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].payload["to"], "DISCOVERY")
            self.assertEqual(events[0].payload["from"], "INTAKE")

    def test_quiet_states_emit_nothing(self):
        with temp_project() as project:
            log = EventLog(project)
            machine = ForgeMachine(project, event_log=log)
            machine.go("DISCOVERY")
            machine.go("DEFINITION")
            machine.go("ARCHITECTURE")
            machine.go("ROADMAP")
            machine.go("TASK_READY")  # no natural event for TASK_READY
            self.assertEqual(len(log.query()), 4)

    def test_event_map_covers_key_states(self):
        self.assertEqual(STATE_EVENT_MAP["IMPLEMENTING"],
                         EventType.TASK_STARTED)
        self.assertEqual(STATE_EVENT_MAP["COMPLETE_TASK"],
                         EventType.TASK_COMPLETED)
        self.assertEqual(STATE_EVENT_MAP["REPAIR"], EventType.TASK_FAILED)
        self.assertEqual(STATE_EVENT_MAP["HUMAN_DECISION"],
                         EventType.HUMAN_DECISION_REQUESTED)
        self.assertEqual(STATE_EVENT_MAP["PROJECT_COMPLETE"],
                         EventType.PROJECT_COMPLETED)


class TestTransitionAudit(unittest.TestCase):
    """Slice 17: ForgeMachine.recent_transitions() audit trail."""

    def test_successful_transitions_recorded_in_order(self):
        with temp_project() as project:
            machine = ForgeMachine(project)
            machine.go("DISCOVERY")
            machine.go("DEFINITION")
            machine.go("ARCHITECTURE")

            trail = machine.recent_transitions()
            self.assertEqual(
                [(entry["from"], entry["to"]) for entry in trail],
                [("INTAKE", "DISCOVERY"),
                 ("DISCOVERY", "DEFINITION"),
                 ("DEFINITION", "ARCHITECTURE")])
            for entry in trail:
                self.assertEqual(set(entry.keys()),
                                 {"from", "to", "ts"})
                # Must be a parseable UTC ISO timestamp.
                ts = datetime.fromisoformat(entry["ts"])
                self.assertIsNotNone(ts.tzinfo)

    def test_illegal_transition_not_recorded(self):
        with temp_project() as project:
            machine = ForgeMachine(project)
            machine.go("DISCOVERY")
            with self.assertRaises(IllegalTransition):
                machine.go("IMPLEMENTING")  # DISCOVERY -> IMPLEMENTING illegal
            trail = machine.recent_transitions()
            self.assertEqual(len(trail), 1)
            self.assertEqual((trail[0]["from"], trail[0]["to"]),
                             ("INTAKE", "DISCOVERY"))

    def test_same_state_noop_not_recorded(self):
        with temp_project() as project:
            machine = ForgeMachine(project)
            machine.go("INTAKE")  # no-op, not a real move
            self.assertEqual(machine.recent_transitions(), [])

    def test_trail_capped_at_thirty_two(self):
        with temp_project() as project:
            machine = ForgeMachine(project)
            machine.go("DISCOVERY")
            machine.go("DEFINITION")
            # HUMAN_DECISION is a legal out-of-band move from any state,
            # and HUMAN_DECISION -> DEFINITION is in the table, giving a
            # two-node cycle to hammer the trail with.
            for _ in range(20):
                machine.go("HUMAN_DECISION")
                machine.go("DEFINITION")
            trail = machine.recent_transitions()
            self.assertEqual(len(trail), 32)
            self.assertEqual((trail[-1]["from"], trail[-1]["to"]),
                             ("HUMAN_DECISION", "DEFINITION"))
            self.assertEqual((trail[-2]["from"], trail[-2]["to"]),
                             ("DEFINITION", "HUMAN_DECISION"))


if __name__ == "__main__":
    unittest.main()
