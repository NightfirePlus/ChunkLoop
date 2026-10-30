"""Deterministic demo adapters for local use and tests."""
from __future__ import annotations

import asyncio
from typing import Any, Mapping

from .models import Block, Issue, Review


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
