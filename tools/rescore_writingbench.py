"""Rescore saved paired WritingBench drafts with a stricter, source-aligned prompt."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from chunkloop.adapters import ChatClient
from chunkloop.datasets.writingbench import load_cases
from tools.run_writingbench import read_env_values


async def score(client: ChatClient, case, response: str) -> dict:
    async def score_one(criterion):
        result = await client.json(
            "You are a strict writing-benchmark evaluator. Evaluate only the supplied "
            "response against the query and this one criterion, using its detailed "
            "score bands. Inspect actual task completion, factual grounding, coherence, "
            "and substance. Do not award a high score merely for length or polished "
            "format. Use the supplied exact response counts for any length judgment; "
            "do not estimate length from the displayed text. "
            "Penalize unsupported invented details and an introductory overview "
            "that does not complete the task. Give concrete strengths and defects with "
            "specific passages from the response. Return JSON with integer score 1-10 "
            "and string reason.",
            {"query": case.query, "criterion": criterion, "response": response,
             "exact_response_word_count": case.measured_word_count(response),
             "exact_response_han_count": case.measured_han_count(response)},
        )
        value, reason = result.get("score"), result.get("reason")
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10:
            raise ValueError(f"invalid score for case {case.index}: {value!r}")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"missing reason for case {case.index}")
        return {"name": criterion["name"], "score": value, "reason": reason}
    criteria = await asyncio.gather(*(score_one(criterion) for criterion in case.checklist))
    return {"criteria": criteria,
            "mean_10": round(sum(item["score"] for item in criteria) / len(criteria), 2)}


async def execute(args: argparse.Namespace) -> list[dict]:
    values = read_env_values(args.env_file) if args.env_file.exists() else {}
    base = os.environ.get("CHUNKLOOP_BASE_URL") or values.get("LLM_BASE_URL", "")
    key = os.environ.get("CHUNKLOOP_API_KEY") or values.get("LLM_API_KEY", "")
    if not base or not key:
        raise ValueError("model endpoint and key required")
    cases = load_cases(args.dataset)
    saved = json.loads(args.input.read_text(encoding="utf-8"))
    by_index = {row["index"]: row for row in saved}
    client = ChatClient(base, key, args.judge, enable_thinking=False)
    report = []
    for index in args.indices:
        source = by_index[index]
        row = {"index": index, "source": str(args.input), "judge": args.judge,
               "prompt_version": "strict_exact_counts_v2"}
        for key_name in args.response_keys:
            response = source[key_name]["response"]
            row[key_name] = await score(client, cases[index], response)
        report.append(row)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"index": index,
                          **{name: row[name]["mean_10"] for name in args.response_keys}},
                         ensure_ascii=False), flush=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("data/writingbench/benchmark_all.jsonl"))
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--indices", type=lambda x: [int(v) for v in x.split(",")], required=True)
    parser.add_argument("--response-keys", nargs="+", default=["direct", "chunkloop"])
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--judge", default="glm-5.2")
    parser.add_argument("--report", type=Path, required=True)
    asyncio.run(execute(parser.parse_args()))


if __name__ == "__main__":
    main()
