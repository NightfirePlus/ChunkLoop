"""Deterministic demo adapters for local use and tests."""
from __future__ import annotations

import asyncio
from typing import Any, Mapping

from .models import Block, Issue, Plan, Review


def default_spec():
    """Keep the installed CLI demo independent of the source checkout."""
    return (Plan((Block('intro', '说明块状态', evidence_ids=('F1',)),
                  Block('design', '说明队列', evidence_ids=('F2',)),
                  Block('end', '总结', depends_on=('intro', 'design')))),
            {'topic': '分块生成与审核'},
            {'F1': '每块有版本号和审核状态', 'F2': '写作与审核分别排队'})


class DemoWriter:
    def __init__(self, delay: float = 0.08):
        self.delay = delay

    async def generate(self, block: Block, context: Mapping[str, Any],
                       evidence: Mapping[str, Any], passed: Mapping[str, Any],
                       feedback: tuple[Issue, ...]) -> str:
        await asyncio.sleep(self.delay)
        facts = "；".join(str(value) for value in evidence.values())
        return f"{block.instruction}：{facts or context.get('topic', '')}".strip()


class DemoReviewer:
    def __init__(self, delay: float = 0.08):
        self.delay = delay

    async def review(self, block: Block, content: Any, context: Mapping[str, Any],
                     evidence: Mapping[str, Any]) -> Review:
        await asyncio.sleep(self.delay)
        if not isinstance(content, str) or not content.strip():
            return Review((Issue("empty", "content is empty"),))
        return Review()
