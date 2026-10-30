"""Review-driven asynchronous chunk scheduler."""
from __future__ import annotations

import asyncio
import inspect
import time
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol

from .models import Block, Issue, Plan, Policy, Review, RunResult, State
from .storage import JsonCheckpoint


class Writer(Protocol):
    async def generate(
        self, block: Block, context: Mapping[str, Any], evidence: Mapping[str, Any],
        passed: Mapping[str, Any], feedback: tuple[Issue, ...],
    ) -> Any: ...


class Reviewer(Protocol):
    async def review(
        self, block: Block, content: Any, context: Mapping[str, Any],
        evidence: Mapping[str, Any],
    ) -> Review: ...


class Engine:
    def __init__(
        self, writer: Writer, reviewer: Reviewer, *, policy: Policy | None = None,
        final_reviewer: Callable[..., Any] | None = None,
        assembler: Callable[[list[Any]], Any] | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.writer = writer
        self.reviewer = reviewer
        self.policy = policy or Policy()
        self.policy.validate()
        self.final_reviewer = final_reviewer
        self.assembler = assembler or (lambda parts: parts)
        self.on_event = on_event

    async def run(
        self, plan: Plan, context: Mapping[str, Any], evidence: Mapping[str, Any], *,
        checkpoint: JsonCheckpoint | None = None, resume: bool = False,
        invalidate: set[str] | None = None, rebase: bool = False,
    ) -> RunResult:
        plan.validate()
        available = set(evidence)
        for block in plan.blocks:
            if not set(block.evidence_ids) <= available:
                raise ValueError(f"block {block.id} refers to missing evidence")
        if rebase and (not resume or checkpoint is None):
            raise ValueError("rebase requires a resumed checkpoint")
        rebase_affected: set[str] = set()
        if resume and checkpoint:
            if rebase:
                state, rebase_affected = checkpoint.load_rebased(plan, context, evidence)
            else:
                state = checkpoint.load(plan, context, evidence)
        else:
            state = State.fresh(plan)
        if set(state.blocks) != {block.id for block in plan.blocks}:
            raise ValueError("checkpoint block IDs do not match plan")
        if invalidate:
            if not resume:
                raise ValueError("invalidation requires a resumed checkpoint")
            state.invalidate(plan, invalidate)
        for item in state.blocks.values():
            if item.status == "generating":
                item.status = "pending"
            elif item.status == "reviewing":
                item.status = "pending_review"
            if item.status not in {"pending", "pending_review", "passed", "needs_review", "blocked"}:
                raise ValueError(f"invalid checkpoint status: {item.status}")
            if item.status == "pending_review" and item.content is None:
                raise ValueError("checkpoint has review task without content")

        started = time.monotonic()
        by_id = {block.id: block for block in plan.blocks}
        write_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        review_queue: asyncio.Queue = asyncio.Queue()
        results: asyncio.Queue = asyncio.Queue()
        serial = 0
        metrics: dict[str, Any] = {
            "written": 0, "reviewed": 0, "rewrites": 0,
            "writer_seconds": 0.0, "reviewer_seconds": 0.0,
            "writer_queue_wait_seconds": 0.0, "reviewer_queue_wait_seconds": 0.0,
            "first_passed_seconds": None,
        }

        def persist() -> None:
            if checkpoint:
                checkpoint.save(plan, context, evidence, state)

        def emit(kind: str, block_id: str = "", **details: Any) -> None:
            event = {"kind": kind, "block_id": block_id,
                     "at": datetime.now(timezone.utc).isoformat(), **details}
            state.events.append(event)
            persist()
            if self.on_event:
                self.on_event(event)

        def queue_write(block: Block) -> None:
            nonlocal serial
            item = state.blocks[block.id]
            item.version += 1
            item.status = "generating"
            serial += 1
            priority = 0 if item.rewrites else 1
            write_queue.put_nowait((priority, serial, block.id, item.version,
                                    tuple(item.issues), time.monotonic()))
            emit("queued_write", block.id, version=item.version,
                 rewrite=bool(item.rewrites), evidence_ids=list(block.evidence_ids))

        def schedule_ready() -> None:
            usable = {"passed"}
            if self.policy.review_gate == "advisory":
                usable.add("needs_review")
            for block in plan.blocks:
                item = state.blocks[block.id]
                if item.status == "pending" and all(
                    state.blocks[dep].status in usable for dep in block.depends_on
                ):
                    queue_write(block)

        async def writer_worker() -> None:
            while True:
                _, _, block_id, version, feedback, queued_at = await write_queue.get()
                metrics["writer_queue_wait_seconds"] += time.monotonic() - queued_at
                block = by_id[block_id]
                scoped = {key: evidence[key] for key in block.evidence_ids}
                passed = {dep: state.blocks[dep].content for dep in block.depends_on}
                began = time.monotonic()
                try:
                    call = self.writer.generate(block, context, scoped, passed, feedback)
                    content = (await asyncio.wait_for(call, self.policy.call_timeout_seconds)
                               if self.policy.call_timeout_seconds is not None else await call)
                    await results.put(("written", block_id, version, content,
                                       time.monotonic() - began))
                except Exception as exc:
                    await results.put(("error", block_id, version,
                                       f"{type(exc).__name__}: {exc}",
                                       time.monotonic() - began))

        async def reviewer_worker() -> None:
            while True:
                block_id, version, content, queued_at = await review_queue.get()
                metrics["reviewer_queue_wait_seconds"] += time.monotonic() - queued_at
                block = by_id[block_id]
                scoped = {key: evidence[key] for key in block.evidence_ids}
                began = time.monotonic()
                try:
                    call = self.reviewer.review(block, content, context, scoped)
                    review = (await asyncio.wait_for(call, self.policy.call_timeout_seconds)
                              if self.policy.call_timeout_seconds is not None else await call)
                    if not isinstance(review, Review):
                        raise TypeError("reviewer must return Review")
                    await results.put(("reviewed", block_id, version, review,
                                       time.monotonic() - began))
                except Exception as exc:
                    await results.put(("error", block_id, version,
                                       f"{type(exc).__name__}: {exc}",
                                       time.monotonic() - began))

        workers = [asyncio.create_task(writer_worker()) for _ in range(self.policy.writer_workers)]
        workers += [asyncio.create_task(reviewer_worker()) for _ in range(self.policy.reviewer_workers)]
        error = ""
        try:
            if rebase:
                emit("rebased", affected=sorted(rebase_affected))
            for block in plan.blocks:
                item = state.blocks[block.id]
                if item.status == "pending_review":
                    item.status = "reviewing"
                    review_queue.put_nowait((block.id, item.version, item.content, time.monotonic()))
                    emit("queued_review", block.id, version=item.version, resumed=True)
            terminal = {"passed", "needs_review"} if self.policy.review_gate == "advisory" else {"passed"}
            while not all(item.status in terminal for item in state.blocks.values()):
                schedule_ready()
                if any(item.status == "blocked" for item in state.blocks.values()):
                    error = "checkpoint contains a blocked block"
                    break
                active = any(item.status in {"generating", "reviewing"}
                             for item in state.blocks.values())
                if not active:
                    error = "no runnable blocks; check dependencies"
                    break
                kind, block_id, version, payload, seconds = await results.get()
                item = state.blocks[block_id]
                if version != item.version:
                    emit("stale_result", block_id, version=version)
                    continue
                if kind == "error":
                    item.status = "blocked"
                    error = f"{block_id}: {payload}"
                    emit("blocked", block_id, version=version, reason=str(payload))
                    break
                if kind == "written":
                    metrics["written"] += 1
                    metrics["writer_seconds"] += seconds
                    item.content = payload
                    item.status = "reviewing"
                    review_queue.put_nowait((block_id, version, payload, time.monotonic()))
                    emit("queued_review", block_id, version=version)
                    continue
                metrics["reviewed"] += 1
                metrics["reviewer_seconds"] += seconds
                item.issues = list(payload.issues)
                if payload.passed:
                    item.status = "passed"
                    if metrics["first_passed_seconds"] is None:
                        metrics["first_passed_seconds"] = time.monotonic() - started
                    emit("passed", block_id, version=version)
                elif item.rewrites < self.policy.max_rewrites:
                    item.rewrites += 1
                    metrics["rewrites"] += 1
                    item.status = "pending"
                    emit("rewrite_requested", block_id, version=version,
                         issues=[issue.__dict__ for issue in item.issues])
                else:
                    if self.policy.review_gate == "strict":
                        item.status = "blocked"
                        error = f"{block_id}: review failed after {item.rewrites} rewrites"
                        emit("blocked", block_id, version=version,
                             issues=[issue.__dict__ for issue in item.issues])
                        break
                    item.status = "needs_review"
                    emit("review_needs_attention", block_id, version=version,
                         issues=[issue.__dict__ for issue in item.issues])
        finally:
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

        metrics["total_seconds"] = time.monotonic() - started
        if error:
            for item in state.blocks.values():
                if item.status in {"generating", "reviewing"}:
                    item.status = "pending"
            persist()
            return RunResult("blocked", state, error=error, metrics=metrics)

        try:
            output = self.assembler([state.blocks[block.id].content for block in plan.blocks])
        except Exception as exc:
            emit("assembly_failed", reason=str(exc))
            metrics["total_seconds"] = time.monotonic() - started
            return RunResult("blocked", state, error=f"assembly failed: {exc}", metrics=metrics)
        final_review = None
        if self.final_reviewer:
            try:
                decision = self.final_reviewer(output, context, evidence)
                if inspect.isawaitable(decision):
                    decision = (await asyncio.wait_for(decision, self.policy.call_timeout_seconds)
                                if self.policy.call_timeout_seconds is not None else await decision)
                if not isinstance(decision, Review):
                    raise TypeError("final reviewer must return Review")
                final_review = decision
            except Exception as exc:
                emit("final_review_error", reason=str(exc))
                metrics["total_seconds"] = time.monotonic() - started
                return RunResult("blocked", state, output=output,
                                 error=f"final review error: {exc}", metrics=metrics)
            if not decision.passed:
                if self.policy.review_gate == "strict":
                    emit("final_review_failed", issues=[issue.__dict__ for issue in decision.issues])
                    metrics["total_seconds"] = time.monotonic() - started
                    return RunResult("blocked", state, output=output,
                                     error="final review failed", metrics=metrics,
                                     final_review=final_review)
                emit("final_review_findings", issues=[issue.__dict__ for issue in decision.issues])
        needs_review = any(item.status == "needs_review" for item in state.blocks.values())
        needs_review = needs_review or (final_review is not None and not final_review.passed)
        status = "needs_review" if needs_review else "completed"
        emit(status)
        metrics["total_seconds"] = time.monotonic() - started
        return RunResult(status, state, output=output, metrics=metrics,
                         final_review=final_review)
