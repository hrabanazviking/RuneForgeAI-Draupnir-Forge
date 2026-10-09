"""Slice 8 acceptance tests: token/cost budget tracker."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from draupnir_forge.budget import Budget, BudgetExhausted, load_model_prices


class BudgetTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = Path(self._tmp.name) / "proj"

    def make_budget(self, max_tokens=1_000_000, max_cost_usd=10.0) -> Budget:
        return Budget(self.project, max_tokens, max_cost_usd)


class TestPriceTable(BudgetTestBase):
    def test_price_table_loads_from_yaml(self):
        prices = load_model_prices()
        self.assertIn("gpt-4o-mini", prices)
        self.assertAlmostEqual(prices["gpt-4o-mini"]["input"], 0.00015)
        self.assertAlmostEqual(prices["gpt-4o-mini"]["output"], 0.0006)
        self.assertAlmostEqual(prices["gpt-4o"]["input"], 0.0025)
        self.assertAlmostEqual(prices["gpt-4o"]["output"], 0.01)

    def test_local_models_cost_zero(self):
        prices = load_model_prices()
        for model in ("ollama", "local"):
            self.assertEqual(prices[model]["input"], 0.0)
            self.assertEqual(prices[model]["output"], 0.0)

    def test_unknown_model_raises_value_error(self):
        budget = self.make_budget()
        with self.assertRaises(ValueError) as ctx:
            budget.charge("definitely-not-a-model", 10, 10)
        self.assertIn("definitely-not-a-model", str(ctx.exception))


class TestChargeMath(BudgetTestBase):
    def test_charge_math(self):
        budget = self.make_budget()
        # gpt-4o-mini: 0.00015/1K in, 0.0006/1K out
        cost = budget.charge("gpt-4o-mini", 1000, 2000)
        self.assertAlmostEqual(cost, 0.00015 + 2 * 0.0006, places=9)
        self.assertEqual(budget.tokens_used, 3000)
        self.assertAlmostEqual(budget.cost_usd, 0.00135, places=9)

    def test_charges_accumulate(self):
        budget = self.make_budget()
        budget.charge("gpt-4o-mini", 1000, 0)
        budget.charge("gpt-4o-mini", 0, 1000)
        self.assertEqual(budget.tokens_used, 2000)
        self.assertAlmostEqual(budget.cost_usd, 0.00015 + 0.0006, places=9)

    def test_zero_token_charge(self):
        budget = self.make_budget()
        cost = budget.charge("ollama", 5000, 5000)
        self.assertEqual(cost, 0.0)
        self.assertEqual(budget.tokens_used, 10000)
        self.assertEqual(budget.cost_usd, 0.0)

    def test_negative_tokens_rejected(self):
        budget = self.make_budget()
        with self.assertRaises(ValueError):
            budget.charge("gpt-4o-mini", -1, 0)
        with self.assertRaises(ValueError):
            budget.charge("gpt-4o-mini", 0, -5)

    def test_remaining(self):
        budget = self.make_budget(max_tokens=1000, max_cost_usd=1.0)
        budget.charge("gpt-4o-mini", 400, 0)
        self.assertEqual(budget.remaining_tokens, 600)
        self.assertAlmostEqual(budget.remaining_usd, 1.0 - 400 / 1000 * 0.00015)


class TestExhaustion(BudgetTestBase):
    def test_token_cap_exhaustion_is_atomic(self):
        budget = self.make_budget(max_tokens=1000, max_cost_usd=100.0)
        budget.charge("gpt-4o-mini", 900, 0)
        with self.assertRaises(BudgetExhausted):
            budget.charge("gpt-4o-mini", 200, 0)  # would make 1100 > 1000
        # atomic: the failed charge left no trace
        self.assertEqual(budget.tokens_used, 900)
        self.assertFalse(budget.exceeded())

    def test_cost_cap_exhaustion_is_atomic(self):
        budget = self.make_budget(max_tokens=10_000_000, max_cost_usd=0.001)
        budget.charge("gpt-4o-mini", 1000, 0)  # $0.00015
        before_tokens = budget.tokens_used
        before_cost = budget.cost_usd
        with self.assertRaises(BudgetExhausted):
            budget.charge("gpt-4o", 100_000, 100_000)  # far over $0.001
        self.assertEqual(budget.tokens_used, before_tokens)
        self.assertAlmostEqual(budget.cost_usd, before_cost)

    def test_exceeded_true_at_cap(self):
        budget = self.make_budget(max_tokens=100, max_cost_usd=100.0)
        self.assertFalse(budget.exceeded())
        budget.charge("ollama", 100, 0)
        self.assertTrue(budget.exceeded())

    def test_budget_exhausted_is_exception(self):
        self.assertTrue(issubclass(BudgetExhausted, Exception))


class TestPersistence(BudgetTestBase):
    def test_round_trip(self):
        budget = self.make_budget()
        budget.charge("gpt-4o-mini", 1200, 3400)
        budget.record_task_complete()
        budget.record_task_complete()

        reloaded = Budget(self.project, 1_000_000, 10.0)
        self.assertEqual(reloaded.tokens_used, 4600)
        self.assertAlmostEqual(reloaded.cost_usd, budget.cost_usd)
        self.assertEqual(reloaded.tasks_completed, 2)

    def test_ledger_file_location(self):
        budget = self.make_budget()
        budget.charge("gpt-4o-mini", 10, 10)
        ledger = self.project / ".mythis" / "budget.json"
        self.assertTrue(ledger.is_file())
        data = json.loads(ledger.read_text(encoding="utf-8"))
        self.assertEqual(data["tokens_used"], 20)

    def test_corrupt_ledger_starts_fresh(self):
        ledger = self.project / ".mythis" / "budget.json"
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text("{ this is not json", encoding="utf-8")
        budget = self.make_budget()
        self.assertEqual(budget.tokens_used, 0)
        self.assertEqual(budget.cost_usd, 0.0)
        self.assertEqual(budget.tasks_completed, 0)

    def test_missing_ledger_starts_fresh(self):
        budget = self.make_budget()
        self.assertEqual(budget.tokens_used, 0)

    def test_record_task_complete_counts(self):
        budget = self.make_budget()
        self.assertEqual(budget.tasks_completed, 0)
        self.assertEqual(budget.record_task_complete(), 1)
        self.assertEqual(budget.record_task_complete(), 2)
        self.assertEqual(budget.tasks_completed, 2)


class TestReport(BudgetTestBase):
    def test_report_format(self):
        budget = self.make_budget(max_tokens=2_000_000, max_cost_usd=25.0)
        budget.charge("gpt-4o-mini", 10_000, 4_000)
        for _ in range(3):
            budget.record_task_complete()
        report = budget.report()
        lines = report.splitlines()
        self.assertGreaterEqual(len(lines), 2)  # multi-line
        self.assertIn("14,000/2,000,000", lines[0])
        self.assertIn("tokens", lines[0])
        self.assertIn("$", report)
        self.assertIn("3 tasks completed", report)
        self.assertIn("within budget", report)

    def test_report_shows_exhausted(self):
        budget = self.make_budget(max_tokens=100, max_cost_usd=100.0)
        budget.charge("ollama", 100, 0)
        self.assertIn("EXHAUSTED", budget.report())


class TestFallbackPrices(BudgetTestBase):
    def test_corrupt_yaml_falls_back_without_crashing(self):
        bad_yaml = Path(self._tmp.name) / "bad_prices.yaml"
        bad_yaml.write_text(":\n\t- broken {[", encoding="utf-8")
        old = os.environ.get("DRAUPNIR_PRICE_FILE")
        os.environ["DRAUPNIR_PRICE_FILE"] = str(bad_yaml)
        try:
            prices = load_model_prices()
        finally:
            if old is None:
                os.environ.pop("DRAUPNIR_PRICE_FILE", None)
            else:
                os.environ["DRAUPNIR_PRICE_FILE"] = old
        # fallback estimates still let the Forge budget instead of crashing
        self.assertIn("gpt-4o-mini", prices)
        budget = Budget(self.project, 1_000_000, 10.0)
        cost = budget.charge("gpt-4o-mini", 1000, 1000)
        self.assertAlmostEqual(cost, 0.00015 + 0.0006, places=9)


if __name__ == "__main__":
    unittest.main()
