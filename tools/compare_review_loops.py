"""Compare full-document review/rewrite against ChunkLoop until both pass."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path

from chunkloop.adapters import ChatClient, ModelFinalReviewer, ModelPlanner, ModelReviewer, ModelWriter
from chunkloop.datasets.writingbench import WritingBenchCase, WritingBenchFinalReviewer, load_cases
from chunkloop.engine import Engine
from chunkloop.models import Policy
from tools.run_writingbench import read_env_values


class ModelCallBudget:
    def __init__(self, limit: int):
        if limit < 1:
            raise ValueError("model call budget must be positive")
        self.limit = limit
        self.used = 0
        self.lock = threading.Lock()

    def claim(self) -> None:
        with self.lock:
            if self.used >= self.limit:
                raise RuntimeError(f"model call budget exhausted at {self.limit} calls")
            self.used += 1


class BudgetedChatClient(ChatClient):
    def __init__(self, base: str, key: str, model: str,
                 budget: ModelCallBudget, *, enable_thinking: bool | None = None):
        super().__init__(base, key, model, enable_thinking=enable_thinking)
        self.budget = budget

    def _request(self, system: str, user: str) -> dict:
        self.budget.claim()
        return super()._request(system, user)


def save(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


async def reviewed_whole_document(case: WritingBenchCase, base: str, key: str,
                                   writer_model: str, reviewer_model: str,
                                   budget: ModelCallBudget,
                                   report: dict, path: Path) -> None:
    writer = BudgetedChatClient(base, key, writer_model, budget)
    reviewer = BudgetedChatClient(base, key, reviewer_model, budget,
                                  enable_thinking=False)
    final_review = WritingBenchFinalReviewer(case, ModelFinalReviewer(reviewer))
    context, evidence = case.generation_input()
    section = {"rounds": [], "status": "running"}
    report["whole_document"] = section
    started = time.perf_counter()
    previous = ""
    feedback: list[dict] = []
    while True:
        number = len(section["rounds"]) + 1
        print(f"whole_document round={number} generating", file=sys.stderr, flush=True)
        try:
            response = await writer.json(
                "Complete the full writing task in response_language unless the task "
                "explicitly asks for another language. Follow all explicit constraints. "
                "When a prior draft and review findings are supplied, revise the entire "
                "draft to resolve them. Return JSON with a single string field response.",
                {"task": context, "previous_response": previous,
                 "review_feedback": feedback},
            )
            output = response.get("response")
            if not isinstance(output, str) or not output.strip():
                raise ValueError("full-document writer returned empty response")
            decision = await final_review(output, context, evidence)
        except Exception as exc:
            section.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
            break
        section["rounds"].append({"round": number, "response": output,
                                  "word_count": case.measured_word_count(output),
                                  "han_count": case.measured_han_count(output),
                                  "review": asdict(decision)})
        section["wall_seconds"] = round(time.perf_counter() - started, 3)
        save(path, report)
        print(f"whole_document round={number} passed={decision.passed}",
              file=sys.stderr, flush=True)
        if decision.passed:
            section["status"] = "completed"
            break
        previous = output
        feedback = [asdict(issue) for issue in decision.issues]
    section["wall_seconds"] = round(time.perf_counter() - started, 3)
    section["usage"] = {"writer": writer.stats(), "reviewer": reviewer.stats()}
    save(path, report)


async def reviewed_chunkloop(case: WritingBenchCase, base: str, key: str,
                             models: dict[str, str], budget: ModelCallBudget,
                             report: dict, path: Path) -> None:
    planner = BudgetedChatClient(base, key, models["planner"], budget,
                                 enable_thinking=False)
    writer = BudgetedChatClient(base, key, models["writer"], budget)
    reviewer = BudgetedChatClient(base, key, models["reviewer"], budget,
                                  enable_thinking=False)
    final_review = WritingBenchFinalReviewer(case, ModelFinalReviewer(reviewer))
    context, evidence = case.generation_input()
    section = {"rounds": [], "status": "running"}
    report["chunkloop"] = section
    started = time.perf_counter()
    try:
        plan = case.with_length_budgets(await ModelPlanner(planner).plan(context, evidence))
        section["plan"] = asdict(plan)
        save(path, report)
        feedback: list[dict] = []
        while True:
            number = len(section["rounds"]) + 1
            print(f"chunkloop round={number} blocks={len(plan.blocks)}", file=sys.stderr, flush=True)
            task_context = {**context, **({"whole_document_feedback": feedback} if feedback else {})}
            result = await Engine(
                ModelWriter(writer), ModelReviewer(reviewer),
                policy=Policy(max_rewrites=None, review_gate="advisory"),
                final_reviewer=final_review,
                assembler=lambda parts: "\n\n".join(parts),
            ).run(plan, task_context, evidence)
            section["rounds"].append({"round": number, "status": result.status,
                                      "response": result.output,
                                      "word_count": case.measured_word_count(result.output or ""),
                                      "han_count": case.measured_han_count(result.output or ""),
                                      "metrics": result.metrics,
                                      "block_statuses": {name: state.status
                                                         for name, state in result.state.blocks.items()},
                                      "events": result.state.events,
                                      "block_findings": {name: [asdict(issue) for issue in state.issues]
                                                         for name, state in result.state.blocks.items()
                                                         if state.issues},
                                      "final_review": asdict(result.final_review)
                                      if result.final_review else None,
                                      "error": result.error})
            section["wall_seconds"] = round(time.perf_counter() - started, 3)
            save(path, report)
            print(f"chunkloop round={number} status={result.status}",
                  file=sys.stderr, flush=True)
            if result.status == "completed":
                section["status"] = "completed"
                break
            if result.status == "blocked":
                section.update({"status": "error", "error": result.error})
                break
            feedback = ([asdict(issue) for issue in result.final_review.issues]
                        if result.final_review else [])
            if not feedback:
                raise RuntimeError("needs_review without whole-document findings")
    except Exception as exc:
        section.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
    section["wall_seconds"] = round(time.perf_counter() - started, 3)
    section["usage"] = {"planner": planner.stats(), "writer": writer.stats(),
                        "reviewer": reviewer.stats()}
    save(path, report)


async def execute(args: argparse.Namespace) -> dict:
    case = load_cases(args.dataset)[args.index]
    values = read_env_values(args.env_file) if args.env_file.exists() else {}
    base = os.environ.get("CHUNKLOOP_BASE_URL") or values.get("LLM_BASE_URL", "")
    key = os.environ.get("CHUNKLOOP_API_KEY") or values.get("LLM_API_KEY", "")
    configured = values.get("LLM_MODEL", "")
    models = {"planner": args.planner or configured,
              "writer": args.writer or configured,
              "reviewer": args.reviewer or configured}
    if not base or not key or not all(models.values()):
        raise ValueError("endpoint, key and all model names are required")
    whole_budget = ModelCallBudget(args.max_model_calls_per_path)
    chunk_budget = ModelCallBudget(args.max_model_calls_per_path)
    report = {"index": case.index, "domain": case.domain1,
              "models": models, "dataset": str(args.dataset),
              "comparison": "both paths reviewed to pass or equal per-path call budget",
              "model_call_budget_per_path": args.max_model_calls_per_path}
    save(args.report, report)
    await reviewed_whole_document(case, base, key, models["writer"],
                                  models["reviewer"], whole_budget, report, args.report)
    if report["whole_document"]["status"] == "completed":
        await reviewed_chunkloop(case, base, key, models, chunk_budget, report, args.report)
    report["model_calls_used"] = {"whole_document": whole_budget.used,
                                  "chunkloop": chunk_budget.used}
    save(args.report, report)
    summary = {"index": case.index,
               "whole_document": {key: report["whole_document"].get(key)
                                  for key in ("status", "wall_seconds")},
               "chunkloop": {key: report.get("chunkloop", {}).get(key)
                             for key in ("status", "wall_seconds")}}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path,
                        default=Path("data/writingbench/benchmark_all.jsonl"))
    parser.add_argument("--index", required=True, type=int)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--planner", default="qwen3.8-flash")
    parser.add_argument("--writer", default="deepseek-v4.1-flash")
    parser.add_argument("--reviewer", default="glm-5.2")
    parser.add_argument("--report", type=Path,
                        default=Path("data/writingbench/review-loop-comparison.json"))
    parser.add_argument("--max-model-calls-per-path", type=int, default=30,
                        help="equal external call budget for each path in this experiment")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    asyncio.run(execute(args))


if __name__ == "__main__":
    main()
