"""Repeatable simulated latency comparison; no model or network required."""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time

from chunkloop import Block, Engine, Plan, Review
from chunkloop.demo import DemoReviewer, DemoWriter


async def serial(blocks: int, write_delay: float, review_delay: float) -> float:
    writer, reviewer = DemoWriter(write_delay), DemoReviewer(review_delay)
    start = time.perf_counter()
    for index in range(blocks):
        block = Block(str(index), "generate")
        content = await writer.generate(block, {}, {}, {}, ())
        result = await reviewer.review(block, content, {}, {})
        assert result == Review()
    return time.perf_counter() - start


async def overlap(blocks: int, write_delay: float, review_delay: float) -> float:
    plan = Plan(tuple(Block(str(index), "generate") for index in range(blocks)))
    result = await Engine(DemoWriter(write_delay), DemoReviewer(review_delay)).run(plan, {}, {})
    assert result.status == "completed"
    return result.metrics["total_seconds"]


async def compare(blocks: int, write_delay: float, review_delay: float, repeats: int) -> dict:
    serial_samples, overlap_samples = [], []
    for _ in range(repeats):
        serial_samples.append(await serial(blocks, write_delay, review_delay))
        overlap_samples.append(await overlap(blocks, write_delay, review_delay))
    baseline = statistics.median(serial_samples)
    pipeline = statistics.median(overlap_samples)
    return {"scenario": "simulated latency; equal adapters and inputs",
            "blocks": blocks, "repeats": repeats,
            "serial_median_seconds": round(baseline, 4),
            "chunkloop_median_seconds": round(pipeline, 4),
            "latency_reduction_percent": round((baseline - pipeline) / baseline * 100, 2)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blocks", type=int, default=6)
    parser.add_argument("--write-delay", type=float, default=0.08)
    parser.add_argument("--review-delay", type=float, default=0.08)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.blocks < 1 or args.repeats < 1 or min(args.write_delay, args.review_delay) < 0:
        parser.error("blocks and repeats must be positive; delays cannot be negative")
    result = asyncio.run(compare(args.blocks, args.write_delay,
                                 args.review_delay, args.repeats))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
