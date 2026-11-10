"""A small, deterministic ChunkLoop example with one review-driven rewrite."""
from __future__ import annotations

import asyncio

from chunkloop import Block, Engine, Issue, Plan, Policy, Review


# Fictional facts for this example. No model endpoint or external data is used.
FACTS = {
    "F1": "22:00—22:30",
    "F2": "文件上传暂不可用",
    "F3": "维护结束后自动恢复，无需用户操作",
}
PLAN = Plan((
    Block("impact", "说明维护时间和影响", evidence_ids=("F1", "F2")),
    Block("recovery", "说明恢复方式", evidence_ids=("F3",)),
    Block("summary", "汇总通知", depends_on=("impact", "recovery")),
))


class NoticeWriter:
    async def generate(self, block, context, evidence, passed, feedback):
        if block.id == "summary":
            if "文件上传暂不可用" not in passed["impact"] or "自动恢复" not in passed["recovery"]:
                raise ValueError("汇总所需的上游内容不完整")
            return "如需上传文件，请在维护结束后再试。"
        if block.id == "recovery":
            return f"文件上传功能将在{evidence['F3']}。"
        # Simulate an incomplete first draft so the feedback path is visible.
        if block.id == "impact" and not feedback:
            return f"服务将在{evidence['F1']}维护。"
        return f"服务将在{evidence['F1']}维护，届时{evidence['F2']}。"


class FactReviewer:
    async def review(self, block, content, context, evidence):
        missing = tuple(
            Issue("missing_fact", f"缺少 {fact_id}：{fact}", evidence_id=fact_id)
            for fact_id, fact in evidence.items() if fact not in content
        )
        return Review(missing)


async def main():
    engine = Engine(NoticeWriter(), FactReviewer(),
                    policy=Policy(max_rewrites=1, review_gate="strict"),
                    assembler=lambda parts: "\n".join(parts))
    result = await engine.run(PLAN, {"task": "编写一则示例维护通知"}, FACTS)
    print(f"状态：{result.status}")
    print("审核轨迹：")
    for event in result.state.events:
        if event["kind"] == "rewrite_requested":
            issues = "；".join(issue["detail"] for issue in event["issues"])
            print(f"  {event['block_id']} 第 {event['version']} 版退回：{issues}")
        elif event["kind"] == "passed":
            print(f"  {event['block_id']} 第 {event['version']} 版通过")
    print("成稿：")
    print(result.output or result.error)
    if result.status != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
