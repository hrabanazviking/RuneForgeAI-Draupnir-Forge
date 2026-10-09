"""Slice 17 tests: Verifier role — the eight gates and the verdict."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from draupnir_forge.roles import verifier
from draupnir_forge.roles.base import RoleContext, registry
from draupnir_forge.roles.tester import TestResult
from draupnir_forge.roles.verifier import GateResult, Verdict, evaluate


def _green_impl(project_dir: str) -> dict:
    """An implementation record that should pass every gate."""
    (Path(project_dir) / "alpha.py").write_text("x = 1\n", encoding="utf-8")
    return {
        "project_dir": project_dir,
        "changed_files": ["alpha.py"],
        "build_ok": True,
        "api_unchanged": True,
        "invariant_violations": [],
        "runtime_evidence": "smoke run printed expected output",
        "evidence_text": (
            "the forge now renders the rune map and the rune map is legible"
        ),
        "docs_updated": True,
    }


def _task(criteria):
    return {"task_id": "T-001", "acceptance": criteria}


class TestVerdictModel(unittest.TestCase):
    def test_passed_requires_all_non_skipped(self):
        verdict = Verdict(
            gates=[
                GateResult("a", True, "ok"),
                GateResult("b", True, "ok", skipped=True),
                GateResult("c", True, "ok"),
            ],
            summary="fine",
        )
        self.assertTrue(verdict.passed)

    def test_one_failure_blocks(self):
        verdict = Verdict(
            gates=[GateResult("a", True, "ok"), GateResult("b", False, "bad")],
            summary="bad",
        )
        self.assertFalse(verdict.passed)

    def test_all_skipped_is_not_passed(self):
        verdict = Verdict(
            gates=[GateResult("a", False, "no data", skipped=True)],
            summary="nothing judged",
        )
        self.assertFalse(verdict.passed)

    def test_by_name(self):
        verdict = Verdict(gates=[GateResult("code", True, "ok")], summary="s")
        self.assertEqual(verdict.by_name("code").passed, True)
        self.assertIsNone(verdict.by_name("nope"))


class TestEvaluateGreen(unittest.TestCase):
    def test_all_gates_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            impl = _green_impl(tmp)
            task = _task(["renders the rune map", "rune map is legible"])
            test_result = TestResult(passed=5, failed=0, errors=0, skipped=0)
            verdict = evaluate(task, impl, test_result)
        self.assertTrue(verdict.passed)
        self.assertEqual(len(verdict.gates), 8)
        self.assertFalse(any(g.skipped for g in verdict.gates))

    def test_dict_test_result_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            impl = _green_impl(tmp)
            verdict = evaluate(_task(["renders the rune map"]),
                               impl, {"passed": 3, "failed": 0, "errors": 0})
        self.assertTrue(verdict.passed)


class TestGateSemantics(unittest.TestCase):
    def _base(self, tmp):
        impl = _green_impl(tmp)
        task = _task(["renders the rune map"])
        test_result = TestResult(passed=1)
        return impl, task, test_result

    def test_missing_data_skips_not_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            impl = {"project_dir": tmp}
            verdict = evaluate(_task([]), impl, None)
        skipped = {g.gate for g in verdict.gates if g.skipped}
        # code has no changed_files, build/test/interface/invariant/runtime/
        # documentation have no flags, goal has no criteria -> all skip
        self.assertEqual(len(skipped), 8)

    def test_goal_gate_fails_without_evidence_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            impl, task, test_result = self._base(tmp)
            impl.pop("evidence_text")
            verdict = evaluate(task, impl, test_result)
        gate = verdict.by_name("goal")
        self.assertFalse(gate.skipped)
        self.assertFalse(gate.passed)
        self.assertFalse(verdict.passed)

    def test_goal_gate_fails_on_missing_criterion(self):
        with tempfile.TemporaryDirectory() as tmp:
            impl, task, test_result = self._base(tmp)
            task = _task(["renders the rune map", "flies to the moon"])
            verdict = evaluate(task, impl, test_result)
        self.assertFalse(verdict.by_name("goal").passed)
        self.assertFalse(verdict.passed)

    def test_code_gate_fails_on_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            impl, task, test_result = self._base(tmp)
            impl["changed_files"] = ["alpha.py", "ghost.py"]
            verdict = evaluate(task, impl, test_result)
        gate = verdict.by_name("code")
        self.assertFalse(gate.passed)
        self.assertIn("ghost.py", gate.evidence)
        self.assertFalse(verdict.passed)

    def test_build_gate_failure_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            impl, task, test_result = self._base(tmp)
            impl["build_ok"] = False
            verdict = evaluate(task, impl, test_result)
        self.assertFalse(verdict.by_name("build").passed)
        self.assertFalse(verdict.passed)

    def test_test_gate_failure_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            impl, task, test_result = self._base(tmp)
            verdict = evaluate(task, impl, TestResult(passed=1, failed=2))
        self.assertFalse(verdict.by_name("test").passed)
        self.assertFalse(verdict.passed)

    def test_interface_flag_honored(self):
        with tempfile.TemporaryDirectory() as tmp:
            impl, task, test_result = self._base(tmp)
            impl["api_unchanged"] = False
            verdict = evaluate(task, impl, test_result)
        self.assertFalse(verdict.by_name("interface").passed)

    def test_invariant_violations_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            impl, task, test_result = self._base(tmp)
            impl["invariant_violations"] = ["wrote outside project dir"]
            verdict = evaluate(task, impl, test_result)
        self.assertFalse(verdict.by_name("invariant").passed)

    def test_empty_runtime_evidence_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            impl, task, test_result = self._base(tmp)
            impl["runtime_evidence"] = "   "
            verdict = evaluate(task, impl, test_result)
        self.assertFalse(verdict.by_name("runtime").passed)

    def test_docs_flag_honored(self):
        with tempfile.TemporaryDirectory() as tmp:
            impl, task, test_result = self._base(tmp)
            impl["docs_updated"] = False
            verdict = evaluate(task, impl, test_result)
        self.assertFalse(verdict.by_name("documentation").passed)

    def test_summary_names_failing_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            impl, task, test_result = self._base(tmp)
            impl["build_ok"] = False
            impl["docs_updated"] = False
            verdict = evaluate(task, impl, test_result)
        self.assertIn("build", verdict.summary)
        self.assertIn("documentation", verdict.summary)


class TestVerifierRole(unittest.TestCase):
    def test_registered(self):
        self.assertIn("verifier", registry.names())
        self.assertIsInstance(registry.create("verifier"), verifier.Verifier)

    def test_run_green(self):
        with tempfile.TemporaryDirectory() as tmp:
            impl = _green_impl(tmp)
            ctx = RoleContext(
                project_dir=tmp,
                task=_task(["renders the rune map", "rune map is legible"]),
                artifacts={"implementation": impl, "test_result": TestResult(passed=5)},
            )
            result = verifier.Verifier().run(ctx)
        self.assertTrue(result.ok)
        self.assertIsInstance(result.artifacts["verdict"], Verdict)

    def test_run_red(self):
        with tempfile.TemporaryDirectory() as tmp:
            impl = _green_impl(tmp)
            impl["build_ok"] = False
            ctx = RoleContext(
                project_dir=tmp,
                task=_task(["renders the rune map"]),
                artifacts={"implementation": impl, "test_result": TestResult(passed=5)},
            )
            result = verifier.Verifier().run(ctx)
        self.assertFalse(result.ok)

    def test_run_never_raises(self):
        ctx = RoleContext(project_dir="/nonexistent-dir-xyz")
        result = verifier.Verifier().run(ctx)
        self.assertIsInstance(result.summary, str)


if __name__ == "__main__":
    unittest.main()
