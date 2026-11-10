# ChunkLoop

ChunkLoop 是一个 Python 分块内容生产引擎。给定任务、证据和分块计划，它逐块生成内容、检查遗漏、按反馈重写，再按计划顺序合并结果。当前仓库提供可运行的本地实例和真实模型适配器。

这个仓库由 [ruanwen-agent 的分块多模型流水线提交](https://github.com/NightfirePlus/ruanwen-agent/commit/a6ae1a6ff95733d28804bbc1c8ff5917ec235cea) 演化而来。原来的华为软文业务代码留在 Git 历史中；当前工作树是独立实现。ChunkLoop 不含原项目知识库、品牌素材和网页工作台。

## 当前阶段与结论

截至 2026-11-10，第一阶段的核心原型与小型实证研究已完成，可以作为阶段性交付。已实现分块写审、依赖快照、局部重写、有限整篇修订和检查点恢复，并提供运行示例、43 项回归测试及原始实验记录。当前定位是可复现的研究原型；复杂长文的稳定质量收益与生产环境可靠性尚未验证。

| 验证内容 | 已观察到的结果 | 限制与负面结果 |
| --- | --- | --- |
| 本轮真实模型调度对照：8 次运行、48 次调用 | 独立块中位耗时由串行 6.068 秒降至双队列 4.452 秒；八份成稿均包含三条指定事实 | 完整依赖链耗时基本持平；任务很小，每种条件仅重复两次，不构成稳定加速保证 |
| 本轮受控流程实验：36 次运行 | 有限局部修订能补回预设遗漏 | 无法修复时增加调用与耗时，仍返回待审结果；这属于故障注入验证 |
| 旧版 24 题长文实验 | 保留全部题目、成稿、审核意见和评分，可离线复算 | 包含质量回退和额外耗时；采用本地模型评分，不能作为新增架构的效果证据 |

完整方法、正负案例和复现命令见[本轮流程研究](research/workflow-study.md)与[历史实证记录](research/README.md)。`completed` 只表示当前检查没有剩余意见，不保证内容真实、满足所有隐含要求或已适合发布。后续研究可围绕具体任务验证复杂约束、错误定位和修订引入新问题的风险。

## 先运行一个实例

[`examples/maintenance_notice.py`](examples/maintenance_notice.py) 使用三条虚构事实写一则服务维护通知。第一版漏掉“文件上传暂不可用”，审核器返回具体问题；引擎只重写这一块，等它和恢复说明都通过后，再生成结尾。实例使用确定性写作器和审核器，无需模型密钥或下载数据。

```powershell
python -m examples.maintenance_notice
```

预期看到 `impact` 第 1 版退回、第 2 版通过，最终状态为 `completed`，并得到三句话组成的通知。输入、完整输出、实现方式和验证范围见[实例说明](docs/first-example.md)。需要 Python 3.10+；包无运行时第三方依赖。若本机使用仓库内的 Conda 环境，也可将上述 `python` 换成 `& '.\.conda\envs\ChunkLoop\python.exe'`。Windows 上运行异步任务需要本机回环套接字可用。

## 实现方式

终审可定位问题块并进行有限局部修订，默认最多一轮，可通过 `--final-revisions` 配置；设为 0 可关闭自动终审修订。无法定位的问题保留给人工处理。

[项目范围](docs/project-scope.md)说明保留代码的职责及历史工具的整理情况。旧版 24 题可用 `python -m tools.verify_evidence` 离线复算；本轮实验的离线汇总命令见[流程研究](research/workflow-study.md)。

1. `Plan` 声明内容块、依赖和每块可读取的证据 ID。
2. `Engine` 把可执行的块送入写作队列，生成后送入审核队列；审核问题作为 `Issue` 回传给写作器。
3. 通过的块供下游读取；所有块处理完后按计划顺序合并。运行事件和块状态可用于检查过程。

写作器和审核器只需实现异步 `generate` / `review` 方法。引擎还提供检查点恢复、局部重跑、可选整篇终审和真实模型适配器；状态定义见[架构说明](docs/architecture.md)。默认审核为建议式：仍有未解决意见时会返回 `needs_review` 和草稿；审核意见在严格模式下才会阻断，执行错误始终返回 `blocked`。

## 运行测试

```powershell
python -m unittest discover -s tests -v
```

## 使用真实模型

复制 [`examples/basic.json`](examples/basic.json) 并填写任务、证据和块计划。也可省略 `blocks`，由规划模型生成计划；这时还需设置 `CHUNKLOOP_PLANNER_MODEL`。设置 OpenAI 兼容端点的环境变量：

```powershell
$env:CHUNKLOOP_BASE_URL = 'https://your-endpoint/v1'
$env:CHUNKLOOP_API_KEY = '<your-key>'
$env:CHUNKLOOP_WRITER_MODEL = 'writer-model'
$env:CHUNKLOOP_REVIEWER_MODEL = 'reviewer-model'
$env:CHUNKLOOP_PLANNER_MODEL = 'planner-model' # 仅自动规划时需要
python -m chunkloop.cli run examples/basic.json --checkpoint data/run.json
```

中断后加 `--resume`；只重做某块及其下游时加 `--resume --invalidate design`。`--invalidate` 用逗号分隔多个块 ID。修改规格中的指令或证据后加 `--resume --rebase`，它会比较旧检查点的输入清单，只复用未变化的块。通用 `context` 变化会让全部块失效。`--events` 将实时事件以 JSON 行输出到标准错误流。单次模型调用默认不设时限；生产部署可按需要用 `--call-timeout` 设置。不要将凭证写入规格文件或提交到仓库。模型端点需要支持 `/chat/completions` 和 JSON 对象响应格式。

默认审核是建议式：`completed` 表示没有未解决的模型意见，`needs_review` 表示草稿和意见都已返回，`blocked` 留给执行错误或显式严格审核。确实需要“任何未解决的审核意见都不放行”时使用 `--strict-review`。该选项是调用方的发布策略，不是 ChunkLoop 对所有任务的默认要求。

## 嵌入 Python

```python
import asyncio
from chunkloop import Block, Engine, Plan
from chunkloop.demo import DemoWriter, DemoReviewer

plan = Plan((Block("a", "介绍方案", evidence_ids=("F1",)),
             Block("b", "总结", depends_on=("a",))))
result = asyncio.run(Engine(DemoWriter(), DemoReviewer()).run(
    plan, {"topic": "示例"}, {"F1": "已登记的事实"}))
print(result.status, result.output)
```

接入自己的异步写作器和审核器时，参考 [`chunkloop/engine.py`](chunkloop/engine.py) 中的 `Writer`、`Reviewer` 协议。内容和上下文如需保存到 JSON 检查点，必须可 JSON 序列化。

## 设计边界

ChunkLoop 管理任务状态和证据分配，不判断证据本身是否可信，也不保证模型审核没有漏检。生产使用时应由调用方提供可信证据、确定性规则和发布前审批。模型适配器只把证据作为数据传入提示词；具体模型的可靠性需要按任务评估。

已有的公开题目适配、探索性评分和问题诊断保留在[验证方法](docs/validation.md)、[WritingBench 记录](docs/writingbench.md)与[诊断实验](docs/writingbench-diagnosis-2026-10-31.md)。这些记录用于说明已运行的实验及发现的问题，不构成通用质量或效率优势的结论。项目代码遵循 [Apache-2.0](LICENSE)；保留原项目历史和许可来源。
