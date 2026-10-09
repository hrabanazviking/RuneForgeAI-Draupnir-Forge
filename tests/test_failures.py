"""Slice 7 acceptance tests: failure classification + escalation ladder."""

from __future__ import annotations

import unittest

from draupnir_forge.failures import (
    FailureClass,
    FailureRecord,
    _EXCEPTION_TYPE_MAP,
    _RULE_TABLE,
    classify_failure,
    escalation_for,
)


def _rec(task_id: str, cls: FailureClass, attempt: int = 1) -> FailureRecord:
    return FailureRecord(failure_class=cls, task_id=task_id, attempt=attempt)


class TestFailureClassEnum(unittest.TestCase):
    def test_exactly_fourteen_members(self):
        self.assertEqual(len(FailureClass), 14)

    def test_expected_names(self):
        names = {member.name for member in FailureClass}
        expected = {
            "IMPLEMENTATION_ERROR",
            "TEST_FAILURE",
            "ARCHITECTURE_MISMATCH",
            "CONTEXT_MISSING",
            "FALSE_ASSUMPTION",
            "DEPENDENCY_FAILURE",
            "ENVIRONMENT_FAILURE",
            "MODEL_LIMITATION",
            "TASK_TOO_LARGE",
            "AMBIGUOUS_REQUIREMENT",
            "USER_DECISION_REQUIRED",
            "RESOURCE_LIMIT",
            "SECURITY_BLOCK",
            "UNKNOWN",
        }
        self.assertEqual(names, expected)


class TestRuleTableIsData(unittest.TestCase):
    def test_rule_table_is_list_of_pairs(self):
        self.assertIsInstance(_RULE_TABLE, list)
        for row in _RULE_TABLE:
            self.assertEqual(len(row), 2)
            pattern, cls = row
            self.assertIsInstance(pattern, str)
            self.assertIsInstance(cls, FailureClass)

    def test_exception_map_is_data(self):
        self.assertIsInstance(_EXCEPTION_TYPE_MAP, dict)
        for name, cls in _EXCEPTION_TYPE_MAP.items():
            self.assertIsInstance(name, str)
            self.assertIsInstance(cls, FailureClass)


class TestClassifyFailure(unittest.TestCase):
    def test_empty_everything_is_unknown(self):
        self.assertEqual(classify_failure(), FailureClass.UNKNOWN)

    def test_module_not_found(self):
        out = "ModuleNotFoundError: No module named 'torch'"
        self.assertEqual(
            classify_failure(test_output=out), FailureClass.DEPENDENCY_FAILURE
        )

    def test_import_error_exception(self):
        self.assertEqual(
            classify_failure(exc=ImportError("bad import")),
            FailureClass.DEPENDENCY_FAILURE,
        )

    def test_pip_in_context(self):
        self.assertEqual(
            classify_failure(context="the build failed; check pip install and requirements"),
            FailureClass.DEPENDENCY_FAILURE,
        )

    def test_permission_denied(self):
        self.assertEqual(
            classify_failure(exc=PermissionError("permission denied")),
            FailureClass.SECURITY_BLOCK,
        )

    def test_out_of_memory(self):
        self.assertEqual(
            classify_failure(test_output="RuntimeError: CUDA out of memory"),
            FailureClass.RESOURCE_LIMIT,
        )

    def test_test_output_with_assert(self):
        out = "FAILED tests/test_x.py::test_y - assert 1 == 2"
        self.assertEqual(classify_failure(test_output=out), FailureClass.TEST_FAILURE)

    def test_traceback_without_test_output_is_implementation_error(self):
        self.assertEqual(
            classify_failure(context="got a Traceback in the logs"),
            FailureClass.IMPLEMENTATION_ERROR,
        )

    def test_assertion_error_without_test_output(self):
        self.assertEqual(
            classify_failure(exc=AssertionError("boom")),
            FailureClass.IMPLEMENTATION_ERROR,
        )

    def test_assertion_error_with_test_output(self):
        self.assertEqual(
            classify_failure(exc=AssertionError("boom"), test_output="FAILED x"),
            FailureClass.TEST_FAILURE,
        )

    def test_timeout(self):
        self.assertEqual(
            classify_failure(exc=TimeoutError("timed out after 300s")),
            FailureClass.ENVIRONMENT_FAILURE,
        )

    def test_syntax_error(self):
        self.assertEqual(
            classify_failure(exc=SyntaxError("invalid syntax")),
            FailureClass.IMPLEMENTATION_ERROR,
        )

    def test_context_missing(self):
        self.assertEqual(
            classify_failure(exc=FileNotFoundError("no such file")),
            FailureClass.CONTEXT_MISSING,
        )

    def test_architecture_mismatch(self):
        self.assertEqual(
            classify_failure(context="the patch is an architecture mismatch"),
            FailureClass.ARCHITECTURE_MISMATCH,
        )

    def test_false_assumption(self):
        self.assertEqual(
            classify_failure(context="the model made a false assumption about the API"),
            FailureClass.FALSE_ASSUMPTION,
        )

    def test_model_limitation(self):
        self.assertEqual(
            classify_failure(context="hit the token limit of the model"),
            FailureClass.MODEL_LIMITATION,
        )

    def test_task_too_large(self):
        self.assertEqual(
            classify_failure(context="task too large for one slice"),
            FailureClass.TASK_TOO_LARGE,
        )

    def test_ambiguous_requirement(self):
        self.assertEqual(
            classify_failure(context="the requirement is ambiguous"),
            FailureClass.AMBIGUOUS_REQUIREMENT,
        )

    def test_user_decision_required(self):
        self.assertEqual(
            classify_failure(context="user decision required before proceeding"),
            FailureClass.USER_DECISION_REQUIRED,
        )

    def test_unknown_exception_type_defaults_to_implementation(self):
        self.assertEqual(
            classify_failure(exc=RuntimeError("something odd")),
            FailureClass.IMPLEMENTATION_ERROR,
        )

    def test_ten_classes_reachable(self):
        reached = {
            classify_failure(test_output="ModuleNotFoundError: No module named 'x'"),
            classify_failure(exc=PermissionError("permission denied")),
            classify_failure(test_output="CUDA out of memory"),
            classify_failure(exc=SyntaxError("bad")),
            classify_failure(test_output="1 failed, assert x"),
            classify_failure(exc=TimeoutError("timed out")),
            classify_failure(context="file not found on disk"),
            classify_failure(context="architecture mismatch with the spec"),
            classify_failure(context="false assumption in the plan"),
            classify_failure(context="model limitation: context window exceeded"),
            classify_failure(context="task too large"),
            classify_failure(context="ambiguous requirement"),
            classify_failure(context="user decision required"),
            classify_failure(),
        }
        self.assertGreaterEqual(len(reached), 10)


class TestFailureRecord(unittest.TestCase):
    def test_dataclass_fields(self):
        rec = FailureRecord(
            failure_class=FailureClass.TEST_FAILURE,
            task_id="t-1",
            detail="x failed",
            attempt=2,
        )
        self.assertEqual(rec.failure_class, FailureClass.TEST_FAILURE)
        self.assertEqual(rec.task_id, "t-1")
        self.assertEqual(rec.detail, "x failed")
        self.assertEqual(rec.attempt, 2)
        self.assertGreater(rec.ts, 0)

    def test_defaults(self):
        rec = FailureRecord(failure_class=FailureClass.UNKNOWN)
        self.assertEqual(rec.task_id, "")
        self.assertEqual(rec.detail, "")
        self.assertEqual(rec.attempt, 1)


class TestEscalationFor(unittest.TestCase):
    def test_empty_history(self):
        self.assertEqual(escalation_for([]), "none")

    def test_single_failure(self):
        self.assertEqual(
            escalation_for([_rec("t-1", FailureClass.TEST_FAILURE)]), "none"
        )

    def test_two_same_class_escalates_to_auditor(self):
        history = [
            _rec("t-1", FailureClass.TEST_FAILURE, 1),
            _rec("t-1", FailureClass.TEST_FAILURE, 2),
        ]
        self.assertEqual(escalation_for(history), "auditor")

    def test_three_same_class_escalates_to_architect(self):
        history = [_rec("t-1", FailureClass.DEPENDENCY_FAILURE, i) for i in (1, 2, 3)]
        self.assertEqual(escalation_for(history), "architect")

    def test_four_same_class_escalates_to_human(self):
        history = [_rec("t-1", FailureClass.ENVIRONMENT_FAILURE, i) for i in (1, 2, 3, 4)]
        self.assertEqual(escalation_for(history), "human")

    def test_five_plus_stays_human(self):
        history = [_rec("t-1", FailureClass.ENVIRONMENT_FAILURE, i) for i in range(1, 7)]
        self.assertEqual(escalation_for(history), "human")

    def test_different_classes_do_not_stack(self):
        history = [
            _rec("t-1", FailureClass.TEST_FAILURE, 1),
            _rec("t-1", FailureClass.DEPENDENCY_FAILURE, 2),
        ]
        self.assertEqual(escalation_for(history), "none")

    def test_different_tasks_do_not_stack(self):
        history = [
            _rec("t-1", FailureClass.TEST_FAILURE, 1),
            _rec("t-2", FailureClass.TEST_FAILURE, 1),
        ]
        self.assertEqual(escalation_for(history), "none")

    def test_latest_record_decides(self):
        history = [
            _rec("t-1", FailureClass.TEST_FAILURE, 1),
            _rec("t-1", FailureClass.TEST_FAILURE, 2),
            _rec("t-1", FailureClass.DEPENDENCY_FAILURE, 3),
        ]
        self.assertEqual(escalation_for(history), "none")


if __name__ == "__main__":
    unittest.main()
