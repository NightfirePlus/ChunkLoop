"""Command-line entry point."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .adapters import ChatClient, ModelFinalReviewer, ModelPlanner, ModelReviewer, ModelWriter
from .demo import DemoReviewer, DemoWriter
from .engine import Engine
from .models import Block, Plan
from .storage import JsonCheckpoint


def load_spec(path: Path) -> tuple[Plan | None, dict, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    blocks = tuple(Block(
        id=row["id"], instruction=row["instruction"],
        depends_on=tuple(row.get("depends_on", [])),
        evidence_ids=tuple(row.get("evidence_ids", [])),
    ) for row in data.get("blocks", []))
    return (Plan(blocks) if blocks else None), data.get("context", {}), data.get("evidence", {})


async def execute(args: argparse.Namespace) -> dict:
    if args.command == "demo":
        spec = args.spec or Path(__file__).resolve().parent.parent / "examples" / "basic.json"
        writer, reviewer, final = DemoWriter(), DemoReviewer(), None
    else:
        spec = args.spec
        writer = ModelWriter(ChatClient.from_env("writer"))
        reviewer = ModelReviewer(ChatClient.from_env("reviewer"))
        final = ModelFinalReviewer(ChatClient.from_env("reviewer"))
    plan, context, evidence = load_spec(spec)
    checkpoint = JsonCheckpoint(args.checkpoint) if args.checkpoint else None
    if plan is None:
        if args.command == "demo":
            raise ValueError("demo specification needs blocks")
        if args.resume and checkpoint:
            plan = checkpoint.load_plan()
        else:
            plan = await ModelPlanner(ChatClient.from_env("planner")).plan(context, evidence)
    def event_sink(event: dict) -> None:
        print(json.dumps(event, ensure_ascii=False), file=sys.stderr, flush=True)

    engine = Engine(writer, reviewer, final_reviewer=final,
                    assembler=lambda parts: "\n\n".join(parts),
                    on_event=event_sink if args.events else None)
    result = await engine.run(plan, context, evidence,
                              checkpoint=checkpoint, resume=args.resume,
                              invalidate=set(args.invalidate.split(",")) if args.invalidate else None,
                              rebase=args.rebase)
    return {"status": result.status, "plan": asdict(plan), "output": result.output,
            "error": result.error, "metrics": result.metrics}


def main() -> None:
    parser = argparse.ArgumentParser(prog="chunkloop")
    parser.add_argument("command", choices=["demo", "run"])
    parser.add_argument("spec", nargs="?", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--rebase", action="store_true",
                        help="reuse unchanged blocks when the specification has changed")
    parser.add_argument("--invalidate", help="comma-separated block IDs to regenerate with dependents")
    parser.add_argument("--events", action="store_true", help="stream JSON events on stderr")
    args = parser.parse_args()
    if args.command == "run" and args.spec is None:
        parser.error("run requires a specification JSON path")
    if args.resume and not args.checkpoint:
        parser.error("--resume requires --checkpoint")
    if args.invalidate and not args.resume:
        parser.error("--invalidate requires --resume")
    if args.rebase and not args.resume:
        parser.error("--rebase requires --resume")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        result = asyncio.run(execute(args))
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(2, f"chunkloop: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
