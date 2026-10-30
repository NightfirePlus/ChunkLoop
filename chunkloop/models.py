"""Small, domain-independent contracts for a ChunkLoop run."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass(frozen=True)
class Block:
    id: str
    instruction: str
    depends_on: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Plan:
    blocks: tuple[Block, ...]

    def validate(self) -> None:
        if not self.blocks:
            raise ValueError("plan must contain at least one block")
        ids = [b.id for b in self.blocks]
        if any(not i for i in ids) or len(ids) != len(set(ids)):
            raise ValueError("block IDs must be nonempty and unique")
        if any(not b.instruction for b in self.blocks):
            raise ValueError("each block needs an instruction")
        known = set(ids)
        edges = {b.id: set(b.depends_on) for b in self.blocks}
        if any(not deps <= known for deps in edges.values()):
            raise ValueError("dependency refers to an unknown block")
        visiting, visited = set(), set()

        def visit(block_id: str) -> None:
            if block_id in visiting:
                raise ValueError("dependency cycle")
            if block_id in visited:
                return
            visiting.add(block_id)
            for dep in edges[block_id]:
                visit(dep)
            visiting.remove(block_id)
            visited.add(block_id)

        for block_id in ids:
            visit(block_id)


@dataclass(frozen=True)
class Issue:
    code: str
    detail: str
    location: str = ""
    evidence_id: str = ""


@dataclass(frozen=True)
class Review:
    issues: tuple[Issue, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.issues


@dataclass(frozen=True)
class Policy:
    max_rewrites: int = 2
    writer_workers: int = 1
    reviewer_workers: int = 1
    call_timeout_seconds: float = 120.0

    def validate(self) -> None:
        if self.max_rewrites < 0 or self.writer_workers < 1 or self.reviewer_workers < 1:
            raise ValueError("invalid retry or worker count")
        if self.call_timeout_seconds <= 0:
            raise ValueError("call timeout must be positive")


@dataclass
class BlockState:
    status: str = "pending"
    version: int = 0
    rewrites: int = 0
    content: Any = None
    issues: list[Issue] = field(default_factory=list)


@dataclass
class State:
    blocks: dict[str, BlockState]
    events: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def fresh(cls, plan: Plan) -> State:
        return cls({block.id: BlockState() for block in plan.blocks})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def invalidate(self, plan: Plan, changed_ids: set[str]) -> set[str]:
        """Discard changed blocks and every downstream dependent; keep unrelated approvals."""
        known = {block.id for block in plan.blocks}
        if not changed_ids <= known:
            raise ValueError("cannot invalidate an unknown block")
        affected = set(changed_ids)
        while True:
            additions = {block.id for block in plan.blocks
                         if set(block.depends_on) & affected}
            if additions <= affected:
                break
            affected |= additions
        for block_id in affected:
            item = self.blocks[block_id]
            item.status = "pending"
            item.content = None
            item.issues = []
            item.rewrites = 0
        return affected

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> State:
        blocks = {}
        for block_id, raw in data["blocks"].items():
            issues = [Issue(**issue) for issue in raw.get("issues", [])]
            blocks[block_id] = BlockState(
                raw.get("status", "pending"), int(raw.get("version", 0)),
                int(raw.get("rewrites", 0)), raw.get("content"), issues,
            )
        return cls(blocks, list(data.get("events", [])))


@dataclass
class RunResult:
    status: str
    state: State
    output: Any = None
    error: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
