"""Slice 27 tests: human escalation engine (§13 predicates, resume flow)."""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from draupnir_forge.escalation import (
    AWAITING_FILE,
    ASK_CONDITIONS,
    Escalation,
    EscalationPolicy,
    answer_escalation,
    discard_stale_escalation,
    has_pending,
    is_routine,
    is_stale,
    load_negative_list,
    pending_age_seconds,
    request_escalation,
)
from draupnir_forge.failures import FailureClass, FailureRecord
from draupnir_forge.roles.planner import ForgeTask


def _task(title: str, goal: str = "") -> ForgeTask:
    return ForgeTask(task_id="t-1", title=title, domain="core", goal=goal)


def _failure(cls: FailureClass) -> FailureRecord:
    return FailureRecord(failure_class=cls, detail="boom", task_id="t-1")


class TestNegativeList(unittest.TestCase):
    def test_data_loads(self):
        entries = load_negative_list()
        self.assertGreaterEqual(len(entries), 5)
        names = {e["name"] for e in entries}
        self.assertIn("continue_next", names)
        self.assertIn("fix_own_test_failure", names)

    def test_continue_next_is_routine(self):
        policy = EscalationPolicy()
        need, reason = policy.needs_human(
            _task("Continue to next roadmap item"), [], {})
        self.assertFalse(need)
        self.assertTrue(reason.startswith("routine:"))

    def test_fix_own_test_failure_is_routine(self):
        name = is_routine(_task("Fix the test failure from my last patch"))
        self.assertEqual(name, "fix_own_test_failure")

    def test_update_docs_is_routine(self):
        policy = EscalationPolicy()
        need, reason = policy.needs_human(
            _task("Update documentation after the architecture change"),
            [], {"destructive": True})
        # Negative list wins even when an ask-condition would also fire.
        self.assertFalse(need)
        self.assertTrue(reason.startswith("routine:"))

    def test_inspect_files_is_routine(self):
        self.assertEqual(is_routine(_task("Inspect files to finish the task")),
                         "inspect_files")

    def test_verification_is_routine(self):
        self.assertIsNotNone(
            is_routine(_task("Run verification for the workflow")))

    def test_plain_task_not_routine(self):
        self.assertIsNone(is_routine(_task("Implement the payment gateway")))


class TestAskConditions(unittest.TestCase):
    def setUp(self):
        self.policy = EscalationPolicy()
        self.task = _task("Implement the payment gateway",
                         "charge cards through the new provider")

    def test_all_eight_conditions_defined(self):
        self.assertEqual(len(ASK_CONDITIONS), 8)

    def test_no_condition_no_escalation(self):
        need, reason = self.policy.needs_human(self.task, [], {})
        self.assertFalse(need)
        self.assertEqual(reason, "no escalation condition met")

    def test_1_ambiguous_directions(self):
        ctx = {"ambiguous_directions": 2}
        need, reason = self.policy.needs_human(self.task, [], ctx)
        self.assertTrue(need)
        self.assertIn("directions", reason)

    def test_1_single_direction_ok(self):
        need, _ = self.policy.needs_human(self.task, [],
                                          {"ambiguous_directions": 1})
        self.assertFalse(need)

    def test_2_conflicts_requirements(self):
        ctx = {"requirements_conflict": ["must use PostgreSQL"]}
        need, reason = self.policy.needs_human(self.task, [], ctx)
        self.assertTrue(need)
        self.assertIn("PostgreSQL", reason)

    def test_3_destructive_unauthorized(self):
        need, reason = self.policy.needs_human(
            self.task, [], {"destructive": True})
        self.assertTrue(need)
        self.assertIn("destructive", reason)

    def test_3_destructive_authorized_ok(self):
        need, _ = self.policy.needs_human(
            self.task, [],
            {"destructive": True, "destructive_authorized": True})
        self.assertFalse(need)

    def test_4_external_consequences(self):
        need, reason = self.policy.needs_human(
            self.task, [], {"external_consequences": True})
        self.assertTrue(need)
        self.assertIn("authorization", reason)

    def test_5_missing_credentials(self):
        need, reason = self.policy.needs_human(
            self.task, [], {"missing_credentials": ["STRIPE_KEY"]})
        self.assertTrue(need)
        self.assertIn("STRIPE_KEY", reason)

    def test_6_goal_ambiguous(self):
        need, reason = self.policy.needs_human(
            self.task, [], {"goal_ambiguous": True})
        self.assertTrue(need)
        self.assertIn("ambiguous", reason)

    def test_7_breaks_user_decision(self):
        need, reason = self.policy.needs_human(
            self.task, [], {"breaks_user_decision": True})
        self.assertTrue(need)
        self.assertIn("user decision", reason)

    def test_8_repeated_same_class_failures(self):
        hist = [_failure(FailureClass.TEST_FAILURE)] * 4
        need, reason = self.policy.needs_human(self.task, hist, {})
        self.assertTrue(need)
        self.assertIn("4", reason)

    def test_8_three_failures_ok(self):
        hist = [_failure(FailureClass.TEST_FAILURE)] * 3
        need, _ = self.policy.needs_human(self.task, hist, {})
        self.assertFalse(need)

    def test_8_mixed_classes_no_escalation(self):
        hist = ([_failure(FailureClass.TEST_FAILURE)] * 3
                + [_failure(FailureClass.IMPLEMENTATION_ERROR)] * 3)
        need, _ = self.policy.needs_human(self.task, hist, {})
        self.assertFalse(need)

    def test_8_dict_history_supported(self):
        hist = [{"failure_class": "TEST_FAILURE"}] * 4
        need, _ = self.policy.needs_human(self.task, hist, {})
        self.assertTrue(need)

    def test_task_as_plain_string(self):
        need, _ = self.policy.needs_human(
            "run the tests for the new module", [], {})
        self.assertFalse(need)

    def test_task_as_dict(self):
        need, _ = self.policy.needs_human(
            {"title": "Update docs", "goal": ""}, [], {"destructive": True})
        self.assertFalse(need)  # routine still wins

    def test_first_condition_wins(self):
        need, reason = self.policy.needs_human(
            self.task, [],
            {"ambiguous_directions": 3, "missing_credentials": ["X"]})
        self.assertTrue(need)
        self.assertIn("directions", reason)


class TestEscalationRecord(unittest.TestCase):
    def test_round_trip(self):
        esc = Escalation(question="q?", context={"a": 1},
                         asked_at="2026-10-09T00:00:00+00:00")
        clone = Escalation.from_dict(esc.to_dict())
        self.assertEqual(clone.question, "q?")
        self.assertEqual(clone.context, {"a": 1})


class TestPauseResumeFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = self.tmp.name

    def test_no_pending_initially(self):
        self.assertFalse(has_pending(self.project))

    def test_request_writes_question_file(self):
        path = request_escalation(self.project, "Which database?",
                                  {"options": ["a", "b"]})
        self.assertTrue(has_pending(self.project))
        self.assertTrue(path.endswith(AWAITING_FILE))
        with open(path, encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("Which database?", body)
        self.assertIn("Asked (UTC):", body)
        # Escalation ledger entry persisted.
        log = os.path.join(self.project, ".mythis", "escalations.jsonl")
        self.assertTrue(os.path.isfile(log))
        with open(log, encoding="utf-8") as fh:
            record = json.loads(fh.readline())
        self.assertEqual(record["question"], "Which database?")
        self.assertIsNone(record["answer"])

    def test_answer_consumes_and_returns(self):
        request_escalation(self.project, "Which database?", {})
        result = answer_escalation(self.project, "PostgreSQL")
        self.assertEqual(
            result, {"answer": "PostgreSQL", "question": "Which database?"})
        self.assertFalse(has_pending(self.project))
        # Ledger stamped with the answer.
        log = os.path.join(self.project, ".mythis", "escalations.jsonl")
        with open(log, encoding="utf-8") as fh:
            record = json.loads(fh.readline())
        self.assertEqual(record["answer"], "PostgreSQL")
        self.assertIsNotNone(record["answered_at"])

    def test_answer_without_pending_raises(self):
        with self.assertRaises(FileNotFoundError):
            answer_escalation(self.project, "nope")


class TestPendingAge(unittest.TestCase):
    """Slice 3: pending-age and staleness of an awaiting file."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = self.tmp.name

    def _awaiting_path(self):
        return os.path.join(self.project, ".mythis", AWAITING_FILE)

    def _backdate(self, hours):
        old = (datetime.now(timezone.utc)
               - timedelta(hours=hours)).isoformat()
        text = open(self._awaiting_path(), encoding="utf-8").read()
        text = re.sub(r"^Asked \(UTC\): .*$", f"Asked (UTC): {old}",
                      text, flags=re.M)
        with open(self._awaiting_path(), "w", encoding="utf-8") as handle:
            handle.write(text)

    def test_fresh_escalation_is_not_stale(self):
        request_escalation(self.project, "Which database?", {})
        self.assertFalse(is_stale(self.project, 3600))
        age = pending_age_seconds(self.project)
        self.assertIsNotNone(age)
        self.assertGreaterEqual(age, 0)
        self.assertLess(age, 60)

    def test_backdated_escalation_age_and_stale(self):
        request_escalation(self.project, "Which database?", {})
        self._backdate(2)
        age = pending_age_seconds(self.project)
        self.assertIsNotNone(age)
        self.assertAlmostEqual(age, 7200, delta=120)
        self.assertTrue(is_stale(self.project, 3600))
        self.assertFalse(is_stale(self.project, 99999))

    def test_missing_file_is_neither(self):
        self.assertIsNone(pending_age_seconds(self.project))
        self.assertFalse(is_stale(self.project, 3600))

    def test_unparsable_timestamp_is_neither(self):
        os.makedirs(os.path.join(self.project, ".mythis"), exist_ok=True)
        with open(self._awaiting_path(), "w", encoding="utf-8") as handle:
            handle.write("Asked (UTC): not-a-timestamp\n")
        self.assertIsNone(pending_age_seconds(self.project))
        self.assertFalse(is_stale(self.project, 3600))


class TestDiscardStale(unittest.TestCase):
    """Slice 4: discarding a stale pending escalation."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = self.tmp.name

    def _awaiting_path(self):
        return os.path.join(self.project, ".mythis", AWAITING_FILE)

    def _log_records(self):
        log = os.path.join(self.project, ".mythis", "escalations.jsonl")
        if not os.path.isfile(log):
            return []
        with open(log, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def test_stale_is_discarded_and_logged(self):
        request_escalation(self.project, "Which database?", {})
        old = (datetime.now(timezone.utc)
               - timedelta(hours=2)).isoformat()
        text = open(self._awaiting_path(), encoding="utf-8").read()
        text = re.sub(r"^Asked \(UTC\): .*$", f"Asked (UTC): {old}",
                      text, flags=re.M)
        with open(self._awaiting_path(), "w", encoding="utf-8") as handle:
            handle.write(text)

        self.assertTrue(discard_stale_escalation(self.project, 3600))
        self.assertFalse(os.path.isfile(self._awaiting_path()))
        self.assertFalse(has_pending(self.project))

        records = self._log_records()
        discarded = [r for r in records if r.get("event") == "discarded"]
        self.assertEqual(len(discarded), 1)
        self.assertTrue(discarded[0]["asked_at"])
        self.assertTrue(discarded[0]["discarded_at"])

    def test_fresh_is_kept_untouched(self):
        request_escalation(self.project, "Which database?", {})
        before = open(self._awaiting_path(), encoding="utf-8").read()

        self.assertFalse(discard_stale_escalation(self.project, 3600))
        self.assertTrue(os.path.isfile(self._awaiting_path()))
        after = open(self._awaiting_path(), encoding="utf-8").read()
        self.assertEqual(before, after)

        records = self._log_records()
        self.assertFalse(any(r.get("event") == "discarded"
                             for r in records))

    def test_nothing_pending_returns_false(self):
        self.assertFalse(discard_stale_escalation(self.project, 3600))


if __name__ == "__main__":
    unittest.main()
