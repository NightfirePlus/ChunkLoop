import asyncio
import tempfile
import unittest
from pathlib import Path

from chunkloop import Block, Engine, Issue, JsonCheckpoint, Plan, Policy, Review, State
from chunkloop.demo import DemoReviewer, DemoWriter
from chunkloop.adapters import parse_plan, parse_review


class RecordingWriter:
    def __init__(self):
        self.calls = []

    async def generate(self, block, context, evidence, passed, feedback):
        self.calls.append((block.id, set(evidence), dict(passed), len(feedback)))
        await asyncio.sleep(0.03)
        return f"{block.id}-v{len([c for c in self.calls if c[0] == block.id])}"


class FlakyReviewer:
    def __init__(self):
        self.seen = {}

    async def review(self, block, content, context, evidence):
        self.seen[block.id] = self.seen.get(block.id, 0) + 1
        await asyncio.sleep(0.08)
        if block.id == "a" and self.seen[block.id] == 1:
            return Review((Issue("unsupported", "needs correction", "line 1", "F1"),))
        return Review()


class EngineTests(unittest.IsolatedAsyncioTestCase):
    def test_calls_have_no_default_timeout(self):
        self.assertIsNone(Policy().call_timeout_seconds)

    async def test_unlimited_rewrites_can_reach_pass(self):
        class PassOnFourthReview:
            def __init__(self):
                self.calls = 0

            async def review(self, *args):
                self.calls += 1
                return Review() if self.calls == 4 else Review((Issue("revise", "try again"),))

        result = await Engine(DemoWriter(0), PassOnFourthReview(),
                              policy=Policy(max_rewrites=None)).run(
            Plan((Block("a", "write"),)), {}, {})
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.metrics["rewrites"], 3)

    async def test_overlap_rewrite_dependency_and_evidence_scope(self):
        plan = Plan((Block("a", "first", evidence_ids=("F1",)),
                     Block("b", "independent", evidence_ids=("F2",)),
                     Block("c", "last", depends_on=("a", "b"), evidence_ids=("F1",))))
        writer = RecordingWriter()
        result = await Engine(writer, FlakyReviewer()).run(
            plan, {}, {"F1": "one", "F2": "two"})
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.metrics["rewrites"], 1)
        self.assertEqual(result.metrics["written"], 4)
        self.assertEqual(result.metrics["reviewed"], 4)
        self.assertEqual(result.state.blocks["a"].version, 2)
        self.assertEqual(result.state.blocks["c"].status, "passed")
        self.assertEqual(writer.calls[0][1], {"F1"})
        self.assertEqual(writer.calls[1][1], {"F2"})
        self.assertEqual(writer.calls[-1][2], {"a": "a-v2", "b": "b-v1"})
        self.assertEqual(writer.calls[2][3], 1)
        events = [(e["kind"], e["block_id"]) for e in result.state.events]
        self.assertLess(events.index(("queued_review", "b")),
                        events.index(("rewrite_requested", "a")))
        rewrite_at = events.index(("rewrite_requested", "a"))
        self.assertEqual(events[rewrite_at + 1], ("queued_write", "a"))
        self.assertIn(("queued_review", "a"), events[rewrite_at + 2:])

    async def test_review_limit_blocks_and_never_assembles(self):
        class Reject:
            async def review(self, *args):
                return Review((Issue("bad", "reject"),))

        result = await Engine(DemoWriter(0), Reject(),
                              policy=Policy(max_rewrites=1, review_gate="strict")).run(
            Plan((Block("a", "x"),)), {}, {})
        self.assertEqual(result.status, "blocked")
        self.assertIsNone(result.output)
        self.assertEqual(result.state.blocks["a"].version, 2)

    async def test_checkpoint_resume_preserves_passed_and_reviews_inflight(self):
        plan = Plan((Block("a", "first"), Block("b", "second")))
        state = State.fresh(plan)
        state.blocks["a"].status = "passed"
        state.blocks["a"].version = 1
        state.blocks["a"].content = "already approved"
        state.blocks["b"].status = "reviewing"
        state.blocks["b"].version = 1
        state.blocks["b"].content = "ready for review"
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = JsonCheckpoint(Path(directory) / "state.json")
            checkpoint.save(plan, {}, {}, state)
            writer = RecordingWriter()
            result = await Engine(writer, DemoReviewer(0)).run(
                plan, {}, {}, checkpoint=checkpoint, resume=True)
            self.assertEqual(result.status, "completed")
            self.assertEqual(writer.calls, [])
            self.assertEqual(result.output, ["already approved", "ready for review"])
            with self.assertRaisesRegex(ValueError, "inputs changed"):
                checkpoint.load(plan, {"changed": True}, {})

    async def test_final_review_blocks(self):
        async def reject(*args):
            return Review((Issue("contradiction", "conflicting claims"),))

        result = await Engine(DemoWriter(0), DemoReviewer(0), final_reviewer=reject,
                              policy=Policy(review_gate="strict")).run(
            Plan((Block("a", "x"),)), {}, {})
        self.assertEqual(result.status, "blocked")
        self.assertEqual(result.error, "final review failed")

    async def test_default_review_is_advisory_and_retains_output(self):
        class Reject:
            async def review(self, *args):
                return Review((Issue("suggestion", "consider rewriting"),))

        async def final_findings(*args):
            return Review((Issue("repetition", "similar wording"),))

        plan = Plan((Block("a", "first"), Block("b", "depends on a", ("a",))))
        result = await Engine(DemoWriter(0), Reject(),
                              policy=Policy(max_rewrites=0),
                              final_reviewer=final_findings).run(plan, {}, {})
        self.assertEqual(result.status, "needs_review")
        self.assertEqual([state.status for state in result.state.blocks.values()],
                         ["needs_review", "needs_review"])
        self.assertEqual(len(result.output), 2)
        self.assertEqual(result.final_review.issues[0].code, "repetition")
        self.assertFalse(result.error)

    async def test_invalidation_rebuilds_only_changed_blocks_and_descendants(self):
        plan = Plan((Block("a", "first"), Block("b", "unrelated"),
                     Block("c", "dependent", depends_on=("a",))))
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = JsonCheckpoint(Path(directory) / "state.json")
            first = await Engine(DemoWriter(0), DemoReviewer(0)).run(
                plan, {}, {}, checkpoint=checkpoint)
            self.assertEqual(first.status, "completed")
            writer = RecordingWriter()
            second = await Engine(writer, DemoReviewer(0)).run(
                plan, {}, {}, checkpoint=checkpoint, resume=True, invalidate={"a"})
            self.assertEqual(second.status, "completed")
            self.assertEqual([call[0] for call in writer.calls], ["a", "c"])
            self.assertEqual(second.state.blocks["b"].version, 1)
            self.assertEqual(second.state.blocks["a"].version, 2)

    async def test_rebase_changed_evidence_keeps_unrelated_approval(self):
        plan = Plan((Block("a", "first", evidence_ids=("F1",)),
                     Block("b", "unrelated", evidence_ids=("F2",)),
                     Block("c", "dependent", depends_on=("a",))))
        original = {"F1": "old", "F2": "stable"}
        changed = {"F1": "new", "F2": "stable"}
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = JsonCheckpoint(Path(directory) / "state.json")
            await Engine(DemoWriter(0), DemoReviewer(0)).run(
                plan, {}, original, checkpoint=checkpoint)
            writer = RecordingWriter()
            result = await Engine(writer, DemoReviewer(0)).run(
                plan, {}, changed, checkpoint=checkpoint, resume=True, rebase=True)
            self.assertEqual(result.status, "completed")
            self.assertEqual([call[0] for call in writer.calls], ["a", "c"])
            self.assertEqual(result.state.blocks["b"].version, 1)
            self.assertEqual(checkpoint.load(plan, {}, changed).blocks["a"].version, 2)

    async def test_timeout_blocks_and_reports_cause(self):
        class SlowWriter:
            async def generate(self, *args):
                await asyncio.sleep(1)
                return "too late"

        result = await Engine(SlowWriter(), DemoReviewer(0),
                              policy=Policy(call_timeout_seconds=0.01)).run(
            Plan((Block("a", "write"),)), {}, {})
        self.assertEqual(result.status, "blocked")
        self.assertIn("TimeoutError", result.error)

    def test_plan_rejects_cycles_and_unknown_evidence(self):
        with self.assertRaisesRegex(ValueError, "cycle"):
            Plan((Block("a", "x", ("b",)), Block("b", "y", ("a",)))).validate()
        with self.assertRaisesRegex(ValueError, "unique"):
            Plan((Block("a", "x"), Block("a", "y"))).validate()

    def test_planner_output_is_checked_before_dispatch(self):
        data = {"blocks": [{"id": "a", "instruction": "explain",
                            "evidence_ids": ["missing"]}]}
        with self.assertRaisesRegex(ValueError, "unknown evidence"):
            parse_plan(data, {"F1": "fact"})
        data["blocks"][0]["evidence_ids"] = ["F1"]
        self.assertEqual(parse_plan(data, {"F1": "fact"}).blocks[0].evidence_ids, ("F1",))
        with self.assertRaisesRegex(ValueError, "issues array"):
            parse_review({"approved": True})
