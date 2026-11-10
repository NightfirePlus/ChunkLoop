"""Matched scheduling study; simulated fixtures or bounded real-model cases."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import statistics
from dataclasses import asdict
from pathlib import Path

from chunkloop import Block, Engine, Issue, Plan, Policy, Review
from chunkloop.adapters import ChatClient, ModelWriter, ModelReviewer
from tools.run_writingbench import read_env_values


FACTS = {'a': '维护时间为22:00至22:30', 'b': '维护期间文件上传不可用',
         'c': '维护结束后自动恢复'}


def plan_for(chain):
    return Plan(tuple(Block(bid, f'用一句中文说明以下事实，必须原样包含：{fact}。',
                            (chr(ord(bid) - 1),) if chain and bid != 'a' else (), (bid,))
                      for bid, fact in FACTS.items()))


class FixtureWriter:
    def __init__(self, scenario, delay):
        self.scenario, self.delay = scenario, delay

    async def generate(self, block, context, evidence, passed, feedback):
        await asyncio.sleep(self.delay)
        if block.id == 'a' and (self.scenario == 'unrepairable' or
                               self.scenario == 'global_gap' and not feedback):
            return '服务即将维护。'
        return '；'.join(evidence.values()) + '。'


class FixtureReviewer:
    def __init__(self, delay):
        self.delay = delay

    async def review(self, block, content, context, evidence):
        await asyncio.sleep(self.delay)
        # Deliberately weak block review tests whether the final check catches a miss.
        return Review() if content else Review((Issue('empty', 'empty block'),))


async def final_check(output, context, evidence):
    return Review(tuple(Issue('missing_fact', f'必须包含原始事实：{fact}', block_ids=(bid,))
                        for bid, fact in evidence.items() if fact not in output))


class BudgetClient(ChatClient):
    remaining = 48

    async def json(self, system, payload):
        if BudgetClient.remaining <= 0:
            raise RuntimeError('study model-call budget exhausted')
        BudgetClient.remaining -= 1
        return await super().json(system, payload)


async def study(args):
    values = read_env_values(Path('.env')) if args.live else {}
    base = os.environ.get('CHUNKLOOP_BASE_URL') or values.get('LLM_BASE_URL', '')
    key = os.environ.get('CHUNKLOOP_API_KEY') or values.get('LLM_API_KEY', '')
    scenarios = ('independent', 'chain') if args.live else (
        'independent', 'chain', 'global_gap', 'unrepairable')
    report = {'kind': 'live' if args.live else 'controlled_fixture',
              'facts': FACTS, 'repeats': args.repeats, 'delay_seconds': args.delay,
              'writer_model': args.writer if args.live else None,
              'reviewer_model': args.reviewer if args.live else None,
              'call_budget': 48 if args.live else None, 'runs': []}
    for scenario in scenarios:
        plan = plan_for(scenario == 'chain')
        for repeat in range(args.repeats):
            # Alternate order to reduce systematic service/order bias.
            arms = ['serial', 'overlap'] if repeat % 2 == 0 else ['overlap', 'serial']
            if not args.live:
                arms += ['overlap_no_repair']
            for arm in arms:
                clients = []
                if args.live:
                    clients = [BudgetClient(base, key, model, timeout=60, enable_thinking=False)
                               for model in (args.writer, args.reviewer)]
                    writer, reviewer = ModelWriter(clients[0]), ModelReviewer(clients[1])
                else:
                    writer = FixtureWriter(scenario, args.delay)
                    reviewer = FixtureReviewer(args.delay)
                policy = Policy(max_rewrites=1, max_final_revisions=0 if arm == 'overlap_no_repair' else 1,
                                scheduling='serial' if arm == 'serial' else 'overlap',
                                call_timeout_seconds=65 if args.live else None)
                started = time.perf_counter()
                result = await Engine(writer, reviewer, policy=policy, final_reviewer=final_check,
                                      assembler=lambda parts: '\n'.join(parts)).run(
                    plan, {'task': '根据给定事实写一则维护通知。不要增加事实。'}, FACTS)
                report['runs'].append({
                    'scenario': scenario, 'repeat': repeat, 'arm': arm, 'plan': asdict(plan),
                    'policy': asdict(policy), 'status': result.status, 'error': result.error,
                    'output': result.output,
                    'required_facts_present': all(f in (result.output or '') for f in FACTS.values()),
                    'wall_seconds': time.perf_counter() - started,
                    'metrics': result.metrics, 'state': result.state.to_dict(),
                    'final_review': asdict(result.final_review) if result.final_review else None,
                    'usage': [client.stats() for client in clients]})
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                print(scenario, repeat, arm, result.status, flush=True)
    return report


def summarize(path):
    report = json.loads(path.read_text(encoding='utf-8'))
    scenarios = ('independent', 'chain') if report['kind'] == 'live' else (
        'independent', 'chain', 'global_gap', 'unrepairable')
    arms = ('serial', 'overlap') if report['kind'] == 'live' else (
        'serial', 'overlap', 'overlap_no_repair')
    expected = {(s, i, a) for s in scenarios for i in range(report['repeats']) for a in arms}
    actual = [(r['scenario'], r['repeat'], r['arm']) for r in report['runs']]
    if len(set(actual)) != len(actual) or set(actual) != expected:
        raise ValueError('missing, extra or duplicated experimental run')
    groups = {}
    for row in report['runs']:
        present = all(fact in (row['output'] or '') for fact in report['facts'].values())
        if present != row['required_facts_present']:
            raise ValueError('saved compliance flag disagrees with the output')
        groups.setdefault((row['scenario'], row['arm']), []).append(row)
    return [{'scenario': scenario, 'arm': arm, 'n': len(rows),
             'facts_complete': sum(r['required_facts_present'] for r in rows),
             'completed': sum(r['status'] == 'completed' for r in rows),
             'median_seconds': round(statistics.median(r['wall_seconds'] for r in rows), 4),
             'total_writes': sum(r['metrics']['written'] for r in rows),
             'total_reviews': sum(r['metrics']['reviewed'] for r in rows),
             'total_final_revisions': sum(r['metrics']['final_revisions'] for r in rows)}
            for (scenario, arm), rows in groups.items()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--summarize', type=Path, help='recompute saved results without model calls')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--delay', type=float, default=0.03)
    parser.add_argument('--writer', default='deepseek-v4.1-flash')
    parser.add_argument('--reviewer', default='glm-5.2')
    parser.add_argument('--output', type=Path, default=Path('data/workflow-study.json'))
    args = parser.parse_args()
    if args.summarize:
        print(json.dumps(summarize(args.summarize), ensure_ascii=False, indent=2))
        return
    if args.repeats < 1 or args.delay < 0:
        parser.error('repeats must be positive; delay must be nonnegative')
    asyncio.run(study(args))


if __name__ == '__main__':
    main()
