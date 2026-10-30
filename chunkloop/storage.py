"""Atomic JSON checkpoints with input identity validation."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from .models import Block, BlockState, Plan, State


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def fingerprint(plan: Plan, context: Mapping[str, Any], evidence: Mapping[str, Any]) -> str:
    payload = {"plan": asdict(plan), "context": context, "evidence": evidence}
    return _digest(payload)


def manifest(plan: Plan, context: Mapping[str, Any], evidence: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "context": _digest(context),
        "blocks": {block.id: _digest({"block": asdict(block),
                                      "evidence": {key: evidence[key] for key in block.evidence_ids}})
                   for block in plan.blocks},
    }


class JsonCheckpoint:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def save(self, plan: Plan, context: Mapping[str, Any], evidence: Mapping[str, Any], state: State) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"schema": 2, "fingerprint": fingerprint(plan, context, evidence),
                "manifest": manifest(plan, context, evidence),
                "plan": asdict(plan), "state": state.to_dict()}
        temporary = self.path.with_name(self.path.name + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def load(self, plan: Plan, context: Mapping[str, Any], evidence: Mapping[str, Any]) -> State:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("schema") != 2:
            raise ValueError("unsupported checkpoint schema")
        if data.get("fingerprint") != fingerprint(plan, context, evidence):
            raise ValueError("checkpoint plan or inputs changed")
        return State.from_dict(data["state"])

    def load_rebased(self, plan: Plan, context: Mapping[str, Any],
                     evidence: Mapping[str, Any]) -> tuple[State, set[str]]:
        """Reuse unchanged blocks, invalidating changed inputs and downstream blocks."""
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("schema") != 2 or not isinstance(data.get("manifest"), dict):
            raise ValueError("checkpoint has no supported input manifest")
        old = data["manifest"]
        new = manifest(plan, context, evidence)
        state = State.from_dict(data["state"])
        state.blocks = {block.id: state.blocks.get(block.id, BlockState())
                        for block in plan.blocks}
        if old.get("context") != new["context"]:
            changed = set(state.blocks)
        else:
            old_blocks = old.get("blocks", {})
            changed = {block_id for block_id, signature in new["blocks"].items()
                       if signature != old_blocks.get(block_id)}
        affected = state.invalidate(plan, changed)
        return state, affected

    def load_plan(self) -> Plan:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("schema") != 2 or not isinstance(data.get("plan"), dict):
            raise ValueError("checkpoint has no supported plan snapshot")
        blocks = tuple(Block(row["id"], row["instruction"],
                             tuple(row.get("depends_on", [])),
                             tuple(row.get("evidence_ids", [])))
                       for row in data["plan"]["blocks"])
        plan = Plan(blocks)
        plan.validate()
        return plan
