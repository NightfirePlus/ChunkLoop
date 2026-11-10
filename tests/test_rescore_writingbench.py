import unittest

from chunkloop.datasets.writingbench import WritingBenchCase
from tools.rescore_writingbench import score


class StrictRescoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_scores_each_criterion_and_requires_reasons(self):
        class FakeClient:
            def __init__(self):
                self.calls = []

            async def json(self, system, payload):
                self.calls.append((system, payload))
                return {"score": 8, "reason": "Covers the task with concrete detail."}

        criterion = {"name": "coverage", "criteria_description": "Cover the task"}
        case = WritingBenchCase(1, "A", "B", "en", "Write a report.",
                                (criterion,) * 5)
        client = FakeClient()
        result = await score(client, case, "A report")
        self.assertEqual(result["mean_10"], 8)
        self.assertEqual(len(client.calls), 5)
        self.assertIn("unsupported invented details", client.calls[0][0])
        self.assertEqual(client.calls[0][1]["exact_response_word_count"], 2)
        self.assertEqual(client.calls[0][1]["exact_response_han_count"], 0)
