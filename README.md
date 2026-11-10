# ChunkLoop

ChunkLoop 是一个 Python 分块内容生产引擎。给定任务、证据和分块计划，它逐块生成内容、检查遗漏、按反馈重写，再按计划顺序合并结果。当前仓库提供可运行的本地实例和真实模型适配器。

这个仓库由 [ruanwen-agent 的分块多模型流水线提交](https://github.com/NightfirePlus/ruanwen-agent/commit/a6ae1a6ff95733d28804bbc1c8ff5917ec235cea) 演化而来。原来的华为软文业务代码留在 Git 历史中；当前工作树是独立实现。ChunkLoop 不含原项目知识库、品牌素材和网页工作台。

## 先运行一个实例

[`examples/maintenance_notice.py`](examples/maintenance_notice.py) 使用三条虚构事实写一则服务维护通知。第一版漏掉“文件上传暂不可用”，审核器返回具体问题；引擎只重写这一块，等它和恢复说明都通过后，再生成结尾。实例使用确定性写作器和审核器，无需模型密钥或下载数据。

```powershell
python -m examples.maintenance_notice
```

预期看到 `impact` 第 1 版退回、第 2 版通过，最终状态为 `completed`，并得到三句话组成的通知。输入、完整输出、实现方式和验证范围见[实例说明](docs/first-example.md)。需要 Python 3.10+；包无运行时第三方依赖。若本机使用仓库内的 Conda 环境，也可将上述 `python` 换成 `& '.\.conda\envs\ChunkLoop\python.exe'`。Windows 上运行异步任务需要本机回环套接字可用。

## 实现方式

新增[调度与完整性的小型研究](research/workflow-study.md)：包含相同审核规则下的串行/双队列对照、依赖链无收益案例、局部修订成功和失败案例，以及 48 次真实模型调用的完整记录。终审现在可以定位问题块并进行最多一轮局部修订；命令行 `--final-revisions 0` 可关闭自动终审修订。

真实模型的完整批次证据见 [实证记录](research/README.md)：24 题全部保留，展示篇幅改善、额外耗时与质量回退；可用 `python -m tools.verify_evidence` 离线复算。它与上面的确定性实例一起构成当前验证入口。[项目范围](docs/project-scope.md)说明保留代码的职责及历史工具的整理情况。

1. `Plan` 声明内容块、依赖和每块可读取的证据 ID。
2. `Engine` 把可执行的块送入写作队列，生成后送入审核队列；审核问题作为 `Issue` 回传给写作器。
3. 通过的块供下游读取；所有块处理完后按计划顺序合并。运行事件和块状态可用于检查过程。

写作器和审核器只需实现异步 `generate` / `review` 方法。引擎还提供检查点恢复、局部重跑、可选整篇终审和真实模型适配器；状态定义见[架构说明](docs/architecture.md)。默认审核为建议式：仍有未解决意见时会返回 `needs_review` 和草稿；只有调用方选择严格模式才会阻断。

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
