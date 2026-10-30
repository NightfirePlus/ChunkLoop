"""Run a credential-safe live validation using a local env file."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.request
from dataclasses import asdict
from pathlib import Path

from chunkloop import Block, Engine, Policy
from chunkloop.adapters import ChatClient, ModelFinalReviewer, ModelPlanner, ModelReviewer, ModelWriter


def read_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"\'')
    return values


def model_client(base: str, key: str, model: str, *, timeout: float | None,
                 enable_thinking: bool | None = None) -> ChatClient:
    return ChatClient(base, key, model, timeout=timeout, enable_thinking=enable_thinking)


def discover(base: str, key: str) -> dict:
    request = urllib.request.Request(base.rstrip("/") + "/models",
                                     headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response)
    ids = sorted(str(item["id"]) for item in data.get("data", []) if "id" in item)
    return {"count": len(ids), "selected_ids": [name for name in ids
            if any(word in name.lower() for word in ("qwen", "deepseek", "glm"))][:80]}


async def run_case(args, base: str, key: str, configured: str) -> dict:
    planner_model = args.planner or configured
    writer_model = args.writer or configured
    reviewer_model = args.reviewer or configured
    planner_client = model_client(base, key, planner_model, timeout=args.call_timeout,
                                  enable_thinking=False)
    writer_client = model_client(base, key, writer_model, timeout=args.call_timeout)
    reviewer_client = model_client(base, key, reviewer_model, timeout=args.call_timeout,
                                   enable_thinking=False)
    data = json.loads(args.case.read_text(encoding="utf-8"))
    context, evidence = data["context"], data["evidence"]
    report = {"case": args.case.name, "models": {
        "planner": planner_model, "writer": writer_model, "reviewer": reviewer_model},
        "call_timeout_seconds": args.call_timeout,
        "result": "not_started"}
    started = time.perf_counter()
    try:
        plan = await ModelPlanner(planner_client, max_blocks=6).plan(context, evidence)
        report["plan"] = asdict(plan)
        print(f"plan_ready blocks={len(plan.blocks)}", file=sys.stderr, flush=True)
        def progress(event: dict) -> None:
            if event["kind"] in {"passed", "blocked", "rewrite_requested", "completed"}:
                print(f"{event['kind']} block={event['block_id']}", file=sys.stderr, flush=True)
        engine = Engine(ModelWriter(writer_client), ModelReviewer(reviewer_client),
                        policy=Policy(call_timeout_seconds=args.call_timeout),
                        final_reviewer=ModelFinalReviewer(reviewer_client),
                        assembler=lambda parts: "\n\n".join(parts), on_event=progress)
        result = await engine.run(plan, context, evidence)
        report["result"] = result.status
        report["output"] = result.output
        report["error"] = result.error
        report["metrics"] = result.metrics
        report["final_review"] = asdict(result.final_review) if result.final_review else None
        report["events"] = result.state.events
        report["block_statuses"] = {block_id: state.status
                                    for block_id, state in result.state.blocks.items()}
        if "F7" in evidence:
            probe = await ModelReviewer(reviewer_client).review(
                Block("probe", "仅根据F1核验这句话是否有事实依据", evidence_ids=("F1",)),
                "ChunkLoop已经自动验证了全部证据来源的真实性，审核绝不会漏检。",
                context, {"F1": evidence["F7"]},
            )
            report["negative_probe"] = {"detected": not probe.passed,
                                        "issues": [asdict(issue) for issue in probe.issues]}
    except Exception as exc:
        report["result"] = "error"
        report["error"] = f"{type(exc).__name__}: {exc}"
    report["wall_seconds"] = round(time.perf_counter() - started, 3)
    report["usage"] = {"planner": planner_client.stats(), "writer": writer_client.stats(),
                       "reviewer": reviewer_client.stats()}
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--case", type=Path, default=Path("examples/live-technical-brief.json"))
    parser.add_argument("--report", type=Path, default=Path(".tmp/live-validation.json"))
    parser.add_argument("--discover-models", action="store_true")
    parser.add_argument("--planner")
    parser.add_argument("--writer")
    parser.add_argument("--reviewer")
    parser.add_argument("--call-timeout", type=float)
    args = parser.parse_args()
    values = read_env(args.env_file)
    base = os.environ.get("CHUNKLOOP_BASE_URL") or values.get("LLM_BASE_URL", "")
    key = os.environ.get("CHUNKLOOP_API_KEY") or values.get("LLM_API_KEY", "")
    configured = values.get("LLM_MODEL", "")
    if not base or not key or not configured:
        parser.error("base URL, API key and configured model are required")
    if args.call_timeout is not None and args.call_timeout <= 0:
        parser.error("call timeout must be positive")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.discover_models:
        print(json.dumps(discover(base, key), ensure_ascii=False, indent=2))
        return
    report = asyncio.run(run_case(args, base, key, configured))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report.get(key) for key in
                      ("case", "models", "result", "error", "metrics", "negative_probe",
                       "wall_seconds", "usage")}, ensure_ascii=False, indent=2))
    probe = report.get("negative_probe")
    if report["result"] not in {"completed", "needs_review"} or (probe and not probe["detected"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
