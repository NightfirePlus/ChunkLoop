"""Run ChunkLoop and a direct writer on the same WritingBench queries."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

from chunkloop.adapters import ChatClient, ModelCoherenceReviewer, ModelPlanner, ModelReviewer, ModelWriter
from chunkloop.datasets.writingbench import (WritingBenchBlockReviewer, WritingBenchCase,
                                             WritingBenchFinalReviewer, load_cases)
from chunkloop.engine import Engine
from chunkloop.models import Policy


def read_env_values(path: Path) -> dict[str, str]:
    """Read the existing local model configuration without printing credentials."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"\'')
    return values


def make_client(base: str, key: str, model: str, *, thinking: bool | None = None) -> ChatClient:
    return ChatClient(base, key, model, enable_thinking=thinking)


async def score_response(case: WritingBenchCase, response: str,
                         judge: ChatClient) -> dict:
    """Exploratory scoring; the official leaderboard uses its own judge setup."""
    scores = []
    for criterion in case.checklist:
        raw = await judge.json(
            "You are an independent WritingBench evaluator. Score this response against "
            "the supplied single criterion on an integer scale of 1 to 10. Use the "
            "criterion's score bands. Check actual task completion and concrete content; "
            "give a specific reason. Return JSON with score and reason only.",
            {"query": case.query, "criterion": criterion, "response": response},
        )
        score, reason = raw.get("score"), raw.get("reason")
        if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 10:
            raise ValueError(f"invalid judge score for {criterion['name']}: {score!r}")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"missing judge reason for {criterion['name']}")
        scores.append({"name": criterion["name"], "score": score, "reason": reason})
    return {"criteria": scores, "mean_10": round(sum(x["score"] for x in scores) / 5, 2)}


async def run_case(case: WritingBenchCase, args: argparse.Namespace,
                   base: str, key: str, models: dict[str, str]) -> dict:
    context, evidence = case.generation_input()
    selected_mode = (case.recommended_generation_mode()
                     if args.mode == "routed" else
                     "direct" if args.mode == "adaptive" else args.mode)
    record: dict = {"index": case.index, "domain1": case.domain1,
                    "domain2": case.domain2, "lang": case.lang,
                    "selected_mode": selected_mode}
    judge = make_client(base, key, models["judge"], thinking=False) if args.score else None
    if selected_mode in {"direct", "both"}:
        direct = make_client(base, key, models["writer"])
        direct_reviewer = make_client(base, key, models["reviewer"], thinking=False)
        started = time.perf_counter()
        try:
            raw = await direct.json(
                "Complete the user's writing task. Follow its explicit constraints. "
                "Use response_language unless the task explicitly requests another language. "
                "Return a JSON object with one string field named response.",
                context,
            )
            output = raw.get("response")
            if not isinstance(output, str) or not output.strip():
                raise ValueError("direct writer returned empty response")
            coherence = await ModelCoherenceReviewer(direct_reviewer)(output, context, evidence)
            record["direct"] = {"status": "completed", "response": output.strip(),
                                "coherence_review": asdict(coherence)}
            record["direct"]["wall_seconds"] = round(time.perf_counter() - started, 3)
            if judge:
                try:
                    record["direct"]["score"] = await score_response(case, output, judge)
                except Exception as exc:
                    record["direct"]["score_error"] = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            record["direct"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        record["direct"].setdefault("wall_seconds", round(time.perf_counter() - started, 3))
        record["direct"]["usage"] = {"writer": direct.stats(),
                                     "reviewer": direct_reviewer.stats()}
        print(f"case={case.index} direct={record['direct']['status']}", file=sys.stderr, flush=True)

    if args.mode == "adaptive":
        direct_response = record.get("direct", {}).get("response")
        selected_mode = ("chunkloop" if case.adaptive_fallback_needed(direct_response)
                         else "direct")
        record["adaptive_attempted_chunk"] = selected_mode == "chunkloop"
        record["selected_mode"] = selected_mode

    if selected_mode in {"chunkloop", "both"}:
        planner = make_client(base, key, models["planner"], thinking=False)
        writer = make_client(base, key, models["writer"])
        reviewer = make_client(base, key, models["reviewer"], thinking=False)
        started = time.perf_counter()
        chunked: dict = {"status": "not_started"}
        record["chunkloop"] = chunked
        try:
            plan = case.with_length_budgets(await ModelPlanner(planner).plan(context, evidence))
            chunked["plan"] = asdict(plan)
            print(f"case={case.index} plan_blocks={len(plan.blocks)}", file=sys.stderr, flush=True)
            engine = Engine(
                ModelWriter(writer), WritingBenchBlockReviewer(case, ModelReviewer(reviewer)),
                final_reviewer=ModelCoherenceReviewer(reviewer),
                policy=Policy(max_rewrites=1, review_gate="advisory"),
                assembler=lambda parts: "\n\n".join(parts),
                on_event=lambda event: print(
                    f"case={case.index} {event['kind']} {event['block_id']}",
                    file=sys.stderr, flush=True)
                if event["kind"] in {"passed", "rewrite_requested", "needs_review", "blocked"}
                else None,
            )
            result = await engine.run(plan, context, evidence)
            chunked.update({"status": result.status, "response": result.output,
                            "error": result.error, "metrics": result.metrics,
                            "final_review": asdict(result.final_review) if result.final_review else None,
                            "block_statuses": {name: state.status
                                               for name, state in result.state.blocks.items()},
                            "block_findings": {name: [asdict(issue) for issue in state.issues]
                                               for name, state in result.state.blocks.items() if state.issues}})
            chunked["wall_seconds"] = round(time.perf_counter() - started, 3)
            if judge and isinstance(result.output, str) and result.output.strip():
                try:
                    chunked["score"] = await score_response(case, result.output, judge)
                except Exception as exc:
                    chunked["score_error"] = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            chunked.update({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
        chunked.setdefault("wall_seconds", round(time.perf_counter() - started, 3))
        chunked["usage"] = {"planner": planner.stats(), "writer": writer.stats(),
                            "reviewer": reviewer.stats()}
        print(f"case={case.index} chunkloop={chunked['status']}", file=sys.stderr, flush=True)
        if args.mode == "adaptive":
            chunk_response = chunked.get("response")
            direct_response = record.get("direct", {}).get("response")
            if not isinstance(chunk_response, str) or not chunk_response.strip():
                if isinstance(direct_response, str) and direct_response.strip():
                    record["selected_mode"] = "direct"
                    record["adaptive_selection_reason"] = "chunk_failed"
            elif (isinstance(direct_response, str) and direct_response.strip() and
                  case.whole_length_distance(direct_response) <
                  case.whole_length_distance(chunk_response)):
                record["selected_mode"] = "direct"
                record["adaptive_selection_reason"] = "direct_closer_to_length_bound"
            else:
                record["adaptive_selection_reason"] = "chunk_closer_or_compliant"
    if judge:
        record["judge_usage"] = judge.stats()
    whole_length = case.whole_length_bounds()
    explicit_length = case.explicit_length()
    for mode in ("direct", "chunkloop"):
        item = record.get(mode)
        if item and isinstance(item.get("response"), str):
            item["word_count"] = case.measured_word_count(item["response"])
            item["han_count"] = case.measured_han_count(item["response"])
            if whole_length:
                lower, upper, unit = whole_length
                item["whole_length_bounds"] = [lower, upper, unit]
                if explicit_length:
                    item["explicit_length"] = list(explicit_length)
                count = item["word_count"] if case.lang == "en" else item["han_count"]
                item["length_met"] = ((lower is None or count >= lower) and
                                      (upper is None or count <= upper))
    if args.mode == "adaptive":
        chosen = record.get(record["selected_mode"], {})
        adaptive = dict(chosen)
        adaptive["source_mode"] = record["selected_mode"]
        adaptive["wall_seconds"] = round(
            record.get("direct", {}).get("wall_seconds", 0) +
            (record.get("chunkloop", {}).get("wall_seconds", 0)
             if record.get("adaptive_attempted_chunk") else 0), 3)
        record["adaptive"] = adaptive
    return record


def write_outputs(records: list[dict], report_dir: Path) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "details.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    for mode in ("direct", "chunkloop"):
        predictions = [{"index": row["index"], "response": row[mode]["response"]}
                       for row in records if isinstance(row.get(mode, {}).get("response"), str)]
        if predictions:
            (report_dir / f"{mode}_responses.jsonl").write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in predictions),
                encoding="utf-8")
    selected = [{"index": row["index"], "response": row[row["selected_mode"]]["response"]}
                for row in records if row.get("selected_mode") in {"direct", "chunkloop"}
                and isinstance(row.get(row["selected_mode"], {}).get("response"), str)]
    if selected:
        (report_dir / "selected_responses.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in selected),
            encoding="utf-8")


async def execute(args: argparse.Namespace) -> None:
    data = args.dataset.read_bytes()
    cases = load_cases(args.dataset)
    requested = [int(value) for value in args.indices.split(",")]
    if not requested or len(set(requested)) != len(requested):
        raise ValueError("indices must be nonempty and unique")
    missing = set(requested) - cases.keys()
    if missing:
        raise ValueError(f"unknown case indices: {sorted(missing)}")
    values = read_env_values(args.env_file) if args.env_file.exists() else {}
    base = os.environ.get("CHUNKLOOP_BASE_URL") or values.get("LLM_BASE_URL", "")
    key = os.environ.get("CHUNKLOOP_API_KEY") or values.get("LLM_API_KEY", "")
    configured = values.get("LLM_MODEL", "")
    models = {"planner": args.planner or os.environ.get("CHUNKLOOP_PLANNER_MODEL") or configured,
              "writer": args.writer or os.environ.get("CHUNKLOOP_WRITER_MODEL") or configured,
              "reviewer": args.reviewer or os.environ.get("CHUNKLOOP_REVIEWER_MODEL") or configured,
              "judge": args.judge or os.environ.get("CHUNKLOOP_JUDGE_MODEL") or
              args.reviewer or os.environ.get("CHUNKLOOP_REVIEWER_MODEL") or configured}
    required = {"writer", "reviewer"} | ({"planner"} if args.mode not in {"direct"} else set())
    if args.score:
        required.add("judge")
    if not base or not key or any(not models[role] for role in required):
        raise ValueError("model endpoint, key and required model names are needed")
    records = []
    for index in requested:
        records.append(await run_case(cases[index], args, base, key, models))
        write_outputs(records, args.report_dir)
    manifest = {"dataset": str(args.dataset), "dataset_sha256": hashlib.sha256(data).hexdigest(),
                "indices": requested, "mode": args.mode, "exploratory_judge": args.score,
                "models": {role: models[role] for role in required},
                "results": [{"index": row["index"], "selected_mode": row["selected_mode"], **{mode: {
                    "status": row[mode]["status"], "wall_seconds": row[mode]["wall_seconds"],
                    "score_10": row[mode].get("score", {}).get("mean_10"),
                    "word_count": row[mode].get("word_count"),
                    "han_count": row[mode].get("han_count"),
                    "length_met": row[mode].get("length_met")}
                    for mode in ("direct", "chunkloop", "adaptive") if mode in row}}
                    for row in records]}
    (args.report_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("data/writingbench/benchmark_all.jsonl"))
    parser.add_argument("--indices", required=True, help="comma-separated WritingBench indices")
    parser.add_argument("--mode", choices=["chunkloop", "direct", "both", "routed", "adaptive"],
                        default="both")
    parser.add_argument("--score", action="store_true", help="use an independent model judge after generation")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--planner")
    parser.add_argument("--writer")
    parser.add_argument("--reviewer")
    parser.add_argument("--judge")
    parser.add_argument("--report-dir", type=Path, default=Path("data/writingbench/run"))
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        asyncio.run(execute(args))
    except (ValueError, OSError) as exc:
        parser.exit(2, f"writingbench: {exc}\n")


if __name__ == "__main__":
    main()
