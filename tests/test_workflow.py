import unittest
import json

from chunkloop import Block, Engine, Issue, Plan, Policy, Review
from chunkloop.demo import DemoReviewer, DemoWriter
from chunkloop.models import State


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    def test_checkpoint_roundtrip_preserves_snapshots_and_issue_targets(self):
        state = State.fresh(Plan((Block('a', 'a'),)))
        state.blocks['a'].issues = [Issue('missing', 'detail', block_ids=('a',))]
        state.blocks['a'].dependencies = {'upstream': {'version': 2, 'uncertain': True}}
        restored = State.from_dict(json.loads(json.dumps(state.to_dict())))
        self.assertEqual(restored, state)

    async def test_dependency_snapshot_reaches_both_roles_with_uncertainty(self):
        seen = []
        class Writer:
            async def generate(self, block, context, evidence, passed, feedback):
                if block.id == 'b':
                    seen.append(context['chunkloop_dependencies'])
                return block.id
        class Reviewer:
            async def review(self, block, content, context, evidence):
                if block.id == 'b':
                    seen.append(context['chunkloop_dependencies'])
                return Review((Issue('unverified', 'check source'),)) if block.id == 'a' else Review()
        result = await Engine(Writer(), Reviewer(), policy=Policy(max_rewrites=0)).run(
            Plan((Block('a', 'a'), Block('b', 'b', ('a',)))), {}, {})
        self.assertEqual(seen[0], seen[1])
        self.assertTrue(seen[0]['a']['uncertain'])
        self.assertEqual(seen[0]['a']['version'], 1)
        self.assertEqual(result.status, 'needs_review')

    async def test_targeted_final_revision_reuses_unrelated_block(self):
        calls = []
        class Writer:
            async def generate(self, block, context, evidence, passed, feedback):
                calls.append(block.id)
                return 'fixed' if feedback else str(passed) if passed else 'draft'
        async def final(output, context, evidence):
            return Review() if context['chunkloop_blocks']['a']['content'] == 'fixed' else Review(
                (Issue('missing', 'fix a', block_ids=('a',)),))
        result = await Engine(Writer(), DemoReviewer(0), final_reviewer=final).run(
            Plan((Block('a', 'a'), Block('b', 'b'), Block('c', 'c', ('a',)))), {}, {})
        self.assertEqual(result.status, 'completed')
        self.assertEqual(calls.count('a'), 2)
        self.assertEqual(calls.count('b'), 1)
        self.assertEqual(calls.count('c'), 2)
        self.assertEqual(result.metrics['written'], 5)
        self.assertEqual(result.metrics['final_revisions'], 1)
        self.assertEqual(result.state.blocks['c'].dependencies['a']['version'], 2)

    async def test_persistent_and_unlocalized_findings_stop(self):
        for targets, expected in [(('a',), 1), ((), 0), (('unknown',), 0)]:
            async def final(*args):
                return Review((Issue('missing', 'still missing', block_ids=targets),))
            result = await Engine(DemoWriter(0), DemoReviewer(0), final_reviewer=final).run(
                Plan((Block('a', 'a'),)), {}, {})
            self.assertEqual(result.status, 'needs_review')
            self.assertEqual(result.metrics['final_revisions'], expected)

    async def test_serial_and_overlap_complete_same_plan(self):
        plan = Plan((Block('a', 'a'), Block('b', 'b'), Block('c', 'c', ('a',))))
        outputs = []
        for mode in ('serial', 'overlap'):
            result = await Engine(DemoWriter(0), DemoReviewer(0),
                                  policy=Policy(scheduling=mode)).run(plan, {}, {})
            outputs.append(result.output)
        self.assertEqual(outputs[0], outputs[1])

    async def test_failed_final_repair_returns_previous_draft_and_error(self):
        class Writer:
            async def generate(self, block, context, evidence, passed, feedback):
                if feedback:
                    raise RuntimeError('service unavailable')
                return 'original draft'
        async def final(*args):
            return Review((Issue('missing', 'fix it', block_ids=('a',)),))
        result = await Engine(Writer(), DemoReviewer(0), final_reviewer=final).run(
            Plan((Block('a', 'a'),)), {}, {})
        self.assertEqual(result.status, 'blocked')
        self.assertEqual(result.output, ['original draft'])
        self.assertIn('service unavailable', result.error)
