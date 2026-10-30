"""Compare real-model serial processing with ChunkLoop on one fixed plan."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from chunkloop import Engine
from chunkloop.adapters import ChatClient, ModelReviewer, ModelWriter
from chunkloop.cli import load_spec
from tools.validate_live import read_env


def overlap_seconds(left: list[tuple[float, float]],
                    right: list[tuple[float, float]]) -> float:
    return sum(max(0.0, min(a_end, b_end) - max(a_start, b_start))
               for a_start, a_end in left for b_start, b_end in right)


async def measure(mode: str, plan, context, evidence, base: str, key: str,
                  writer_model: str, reviewer_model: str) -> dict:
    writer_client = ChatClient(base, key, writer_model)
    reviewer_client = ChatClient(base, key, reviewer_model, enable_thinking=False)
    writer, reviewer = ModelWriter(writer_client), ModelReviewer(reviewer_client)
    started = time.perf_counter()
    if mode == "pipeline":
        result = await Engine(writer, reviewer).run(plan, context, evidence)
        status, metrics = result.status, result.metrics
        outputs = [result.state.blocks[block.id].content for block in plan.blocks]
    else:
        outputs, passed, written, reviewed, rewrites = [], {}, 0, 0, 0
        status = "completed"
        for block in plan.blocks:
            feedback = ()
            for attempt in range(3):
                scoped = {key: evidence[key] for key in block.evidence_ids}
                content = await writer.generate(
                    block, context, scoped,
                    {dep: passed[dep] for dep in block.depends_on}, feedback)
                written += 1
                review = await reviewer.review(block, content, context, scoped)
                reviewed += 1
                if review.passed:
                    outputs.append(content)
                    passed[block.id] = content
                    break
                if attempt == 2:
                    status = "needs_review"
                    outputs.append(content)
                    passed[block.id] = content
                    break
                feedback = review.issues
                rewrites += 1
        metrics = {"written": written, "reviewed": reviewed, "rewrites": rewrites}
    elapsed = time.perf_counter() - started
    return {"status": status, "wall_seconds": round(elapsed, 3), "metrics": metrics,
            "writer": writer_client.stats(), "reviewer": reviewer_client.stats(),
            "writer_http_seconds": round(sum(end - begin for begin, end in writer_client.timings), 3),
            "reviewer_http_seconds": round(sum(end - begin for begin, end in reviewer_client.timings), 3),
            "http_overlap_seconds": round(overlap_seconds(writer_client.timings,
                                                           reviewer_client.timings), 3),
            "outputs": outputs}


async def run(args) -> dict:
    values = read_env(args.env_file)
    base = os.environ.get("CHUNKLOOP_BASE_URL") or values.get("LLM_BASE_URL", "")
    key = os.environ.get("CHUNKLOOP_API_KEY") or values.get("LLM_API_KEY", "")
    if not base or not key:
        raise ValueError("base URL and API key are required")
    plan, context, evidence = load_spec(args.case)
    if plan is None:
        raise ValueError("comparison case requires a fixed plan")
    plan.validate()
    print("running pipeline", file=sys.stderr, flush=True)
    pipeline = await measure("pipeline", plan, context, evidence, base, key,
                             args.writer, args.reviewer)
    print("running serial baseline", file=sys.stderr, flush=True)
    serial = await measure("serial", plan, context, evidence, base, key,
                           args.writer, args.reviewer)
    return {"case": args.case.name, "models": {"writer": args.writer,
            "reviewer": args.reviewer}, "pipeline": pipeline, "serial": serial,
            "latency_reduction_percent": round((serial["wall_seconds"] - pipeline["wall_seconds"])
                                               / serial["wall_seconds"] * 100, 2)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--case", type=Path, default=Path("examples/live-architecture.json"))
    parser.add_argument("--report", type=Path, default=Path(".tmp/live-architecture-compare.json"))
    parser.add_argument("--writer", default="deepseek-v4.1-flash")
    parser.add_argument("--reviewer", default="glm-5.2")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    report = asyncio.run(run(args))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"case": report["case"], "models": report["models"],
                      "pipeline": {key: value for key, value in report["pipeline"].items()
                                   if key != "outputs"},
                      "serial": {key: value for key, value in report["serial"].items()
                                 if key != "outputs"},
                      "latency_reduction_percent": report["latency_reduction_percent"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
