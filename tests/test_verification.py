"""Slice 25 tests: VerificationEngine — the eight gates, live evidence."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from draupnir_forge.roles.tester import TestResult
from draupnir_forge.roles.verifier import Verdict
from draupnir_forge.tasks import ForgeTask
from draupnir_forge.verification import VerificationEngine


def _task() -> ForgeTask:
    return ForgeTask(task_id="T-001", title="Build the gate",
                     domain="test",
                     acceptance=["the gate stands", "the gate sings"])


def _green_impl(project_dir: str) -> dict:
    """An implementation record that should pass every gate."""
    (Path(project_dir) / "alpha.py").write_text("x = 1\n", encoding="utf-8")
    return {
        "project_dir": project_dir,
        "changed_files": ["alpha.py"],
        "build_ok": True,
        "public_api": {"alpha.main": "() -> None"},
        "runtime_evidence": "ran alpha.main; it sang",
        "evidence_text": "the gate stands and the gate sings",
        "docs_updated": True,
    }


def _green_result() -> TestResult:
    return TestResult(passed=5, failed=0, errors=0, skipped=0)


class TestAllGreen(unittest.TestCase):
    def test_all_gates_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = VerificationEngine(tmp)
            verdict = engine.run_gates(_task(), _green_impl(tmp),
                                       _green_result())
            self.assertIsInstance(verdict, Verdict)
            self.assertTrue(verdict.passed, verdict.summary)
            self.assertEqual(len(verdict.gates), 8)

    def test_snapshot_created_on_first_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = VerificationEngine(tmp)
            engine.run_gates(_task(), _green_impl(tmp), _green_result())
            snapshot = Path(tmp) / ".mythis" / "api_snapshot.json"
            self.assertTrue(snapshot.exists())
            self.assertEqual(json.loads(snapshot.read_text(encoding="utf-8")),
                             {"alpha.main": "() -> None"})

    def test_unchanged_api_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = VerificationEngine(tmp)
            impl = _green_impl(tmp)
            engine.run_gates(_task(), impl, _green_result())
            verdict = engine.run_gates(_task(), impl, _green_result())
            gate = verdict.by_name("interface")
            self.assertTrue(gate.passed)
            self.assertIn("matches snapshot", gate.evidence)

    def test_wraps_verifier_evaluate(self):
        """The verdict comes from the Verifier role's evaluate()."""
        with tempfile.TemporaryDirectory() as tmp:
            from draupnir_forge.roles import verifier as verifier_mod
            engine = VerificationEngine(tmp)
            seen = {}

            original = verifier_mod.evaluate

            def spy(task, impl, test_result):
                seen["impl"] = dict(impl)
                return original(task, impl, test_result)

            verifier_mod.evaluate = spy
            try:
                engine.run_gates(_task(), _green_impl(tmp), _green_result())
            finally:
                verifier_mod.evaluate = original
            # The engine enriches the record before delegating.
            self.assertIn("api_unchanged", seen["impl"])
            self.assertIn("invariant_violations", seen["impl"])
            self.assertTrue(seen["impl"]["api_unchanged"])
            self.assertEqual(seen["impl"]["invariant_violations"], [])


class TestFailingGatesBlock(unittest.TestCase):
    def _verdict(self, tmp, **overrides):
        impl = _green_impl(tmp)
        impl.update(overrides)
        return VerificationEngine(tmp).run_gates(_task(), impl,
                                                 _green_result())

    def test_missing_changed_file_fails_code_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            verdict = self._verdict(tmp, changed_files=["ghost.py"])
            self.assertFalse(verdict.passed)
            self.assertFalse(verdict.by_name("code").passed)

    def test_build_false_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            verdict = self._verdict(tmp, build_ok=False)
            self.assertFalse(verdict.passed)
            self.assertFalse(verdict.by_name("build").passed)

    def test_failed_tests_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = VerificationEngine(tmp)
            bad = TestResult(passed=4, failed=1)
            verdict = engine.run_gates(_task(), _green_impl(tmp), bad)
            self.assertFalse(verdict.passed)
            self.assertFalse(verdict.by_name("test").passed)

    def test_changed_api_fails_interface_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = VerificationEngine(tmp)
            engine.run_gates(_task(), _green_impl(tmp), _green_result())
            impl = _green_impl(tmp)
            impl["public_api"] = {"alpha.main": "() -> int"}  # changed!
            verdict = engine.run_gates(_task(), impl, _green_result())
            self.assertFalse(verdict.passed)
            gate = verdict.by_name("interface")
            self.assertFalse(gate.passed)
            self.assertIn("differs from snapshot", gate.evidence)

    def test_print_in_src_fails_invariant_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            src.mkdir()
            (src / "noisy.py").write_text("print('loud')\n", encoding="utf-8")
            engine = VerificationEngine(tmp)
            verdict = engine.run_gates(_task(), _green_impl(tmp),
                                       _green_result())
            self.assertFalse(verdict.passed)
            gate = verdict.by_name("invariant")
            self.assertFalse(gate.passed)
            self.assertIn("print()", gate.evidence)

    def test_print_in_cli_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            src.mkdir()
            (src / "cli.py").write_text("print('herald speaks')\n",
                                        encoding="utf-8")
            engine = VerificationEngine(tmp)
            verdict = engine.run_gates(_task(), _green_impl(tmp),
                                       _green_result())
            self.assertTrue(verdict.passed, verdict.summary)

    def test_corrupt_state_file_fails_invariant_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            mythis = Path(tmp) / ".mythis"
            mythis.mkdir()
            (mythis / "PROJECT_STATE.json").write_text("{broken",
                                                      encoding="utf-8")
            engine = VerificationEngine(tmp)
            verdict = engine.run_gates(_task(), _green_impl(tmp),
                                       _green_result())
            self.assertFalse(verdict.passed)
            self.assertFalse(verdict.by_name("invariant").passed)

    def test_empty_runtime_evidence_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            verdict = self._verdict(tmp, runtime_evidence="  ")
            self.assertFalse(verdict.passed)
            self.assertFalse(verdict.by_name("runtime").passed)

    def test_unevidenced_criterion_fails_goal_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            verdict = self._verdict(tmp, evidence_text="the gate stands")
            self.assertFalse(verdict.passed)
            self.assertFalse(verdict.by_name("goal").passed)

    def test_docs_not_updated_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            verdict = self._verdict(tmp, docs_updated=False)
            self.assertFalse(verdict.passed)
            self.assertFalse(verdict.by_name("documentation").passed)

    def test_no_test_result_skips_test_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = VerificationEngine(tmp)
            verdict = engine.run_gates(_task(), _green_impl(tmp), None)
            gate = verdict.by_name("test")
            self.assertTrue(gate.skipped)
            self.assertTrue(verdict.passed, verdict.summary)

    def test_no_public_api_skips_interface_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = VerificationEngine(tmp)
            impl = _green_impl(tmp)
            del impl["public_api"]
            verdict = engine.run_gates(_task(), impl, _green_result())
            self.assertTrue(verdict.by_name("interface").skipped)
            self.assertTrue(verdict.passed, verdict.summary)


class TestUpdateApiSnapshot(unittest.TestCase):
    def test_rebaseline_accepts_new_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = VerificationEngine(tmp)
            engine.update_api_snapshot({"v2": "() -> None"})
            unchanged, note = engine.check_interface({"v2": "() -> None"})
            self.assertTrue(unchanged)


class TestGateTimeout(unittest.TestCase):
    """Slice 16: a hung engine-owned check fails its gate, never the forge."""

    def _hung_check(self, release):
        """Stand in for time.sleep(120): blocks far past any gate timeout.

        (A raw sleep(120) in an abandoned pool worker would pin the test
        process at interpreter exit — CPython joins pool workers in its
        atexit handler — so the hang is released via the event instead.
        From the engine's point of view the semantics are identical: the
        check never returns before the timeout fires.)
        """
        def hung(*args, **kwargs):
            release.wait(timeout=120)
            return None
        return hung

    def test_hung_invariant_check_times_out_but_verdict_returned(self):
        import threading
        from draupnir_forge.roles import verifier as verifier_mod
        release = threading.Event()
        with tempfile.TemporaryDirectory() as tmp:
            engine = VerificationEngine(tmp, gate_timeout_s=0.2)
            engine.check_invariants = self._hung_check(release)
            seen = {}
            original = verifier_mod.evaluate

            def spy(task, impl, test_result):
                seen["impl"] = dict(impl)
                return original(task, impl, test_result)

            verifier_mod.evaluate = spy
            try:
                verdict = engine.run_gates(_task(), _green_impl(tmp),
                                           _green_result())
            finally:
                verifier_mod.evaluate = original
                release.set()
            self.assertIsInstance(verdict, Verdict)
            violations = seen["impl"]["invariant_violations"]
            self.assertEqual(len(violations), 1)
            self.assertIn("invariant checks timed out after 0.2s",
                          violations[0])
            gate = verdict.by_name("invariant")
            self.assertFalse(gate.passed)
            self.assertIn("invariant checks timed out", gate.evidence)

    def test_hung_interface_check_times_out_and_fails_gate(self):
        import threading
        release = threading.Event()
        with tempfile.TemporaryDirectory() as tmp:
            engine = VerificationEngine(tmp, gate_timeout_s=0.2)
            engine.check_interface = self._hung_check(release)
            try:
                verdict = engine.run_gates(_task(), _green_impl(tmp),
                                           _green_result())
            finally:
                release.set()
            self.assertIsInstance(verdict, Verdict)
            gate = verdict.by_name("interface")
            self.assertFalse(gate.passed)
            self.assertIn("interface check timed out after 0.2s",
                          gate.evidence)

    def test_default_gate_timeout_is_sixty_seconds(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = VerificationEngine(tmp)
            self.assertEqual(engine.gate_timeout_s, 60.0)


if __name__ == "__main__":
    unittest.main()
