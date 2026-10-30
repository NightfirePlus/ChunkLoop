import unittest

from chunkloop import Engine
from chunkloop.adapters import ModelFinalReviewer, ModelPlanner, ModelReviewer, ModelWriter


class FakeChatClient:
    def __init__(self):
        self.calls = []

    async def json(self, system, payload):
        self.calls.append((system, payload))
        if "Split the task" in system:
            return {"blocks": [
                {"id": "opening", "instruction": "state the fact", "evidence_ids": ["F1"]},
                {"id": "closing", "instruction": "summarize", "depends_on": ["opening"]},
            ]}
        if "Write only" in system:
            return {"content": "supported result"}
        return {"issues": []}


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_planner_writer_reviewer_and_final_review(self):
        client = FakeChatClient()
        context = {"task": "short explanation"}
        evidence = {"F1": "verified statement", "F2": "not assigned"}
        plan = await ModelPlanner(client).plan(context, evidence)
        result = await Engine(ModelWriter(client), ModelReviewer(client),
                              final_reviewer=ModelFinalReviewer(client)).run(
            plan, context, evidence)
        self.assertEqual(result.status, "completed")
        writer_calls = [payload for system, payload in client.calls if "Write only" in system]
        self.assertEqual(writer_calls[0]["allowed_evidence"], {"F1": "verified statement"})
        self.assertEqual(writer_calls[1]["allowed_evidence"], {})
        self.assertEqual(writer_calls[1]["dependency_drafts"],
                         {"opening": "supported result"})
        self.assertEqual(result.metrics["reviewed"], 2)
