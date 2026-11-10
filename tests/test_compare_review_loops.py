import unittest
from tools.compare_review_loops import ModelCallBudget


class ModelCallBudgetTests(unittest.TestCase):
    def test_budget_stops_before_an_unbounded_external_call(self):
        budget = ModelCallBudget(2)
        budget.claim()
        budget.claim()
        self.assertEqual(budget.used, 2)
        with self.assertRaisesRegex(RuntimeError, "budget exhausted"):
            budget.claim()


if __name__ == "__main__":
    unittest.main()
