# ChunkLoop

ChunkLoop 是一个 Python 分块内容生产引擎。它把一个任务拆成有依赖关系的块，让写作器与审核器分别消费两条队列；审核发现的问题只退回对应块。全部块通过后才合并，并可执行跨块终审。

这个仓库由 [ruanwen-agent 的分块多模型流水线提交](https://github.com/NightfirePlus/ruanwen-agent/commit/a6ae1a6ff95733d28804bbc1c8ff5917ec235cea) 演化而来。原来的华为软文业务代码留在 Git 历史中；当前工作树是独立实现。ChunkLoop 不含原项目知识库、品牌素材和网页工作台。

## 核心能力

- **双队列重叠**：写作器生成下一块时，审核器可检查上一块；可配置两类工作者数量。
- **依赖与证据范围**：块声明依赖和允许使用的证据 ID；依赖通过前不生成，适配器只收到该块允许的证据。
- **问题回流**：审核器返回结构化 `Issue`；重写次数有上限，超限后阻断合并。
- **版本与恢复**：每次生成提升块版本；JSON 检查点原子写入，恢复时保留已通过块和待审核内容。
- **局部重跑**：指定块失效时，自动让它和下游依赖块重跑，其他已通过块复用。
- **终审与指标**：可接入整份结果审核；输出首块通过时间、总耗时、队列等待、处理耗时和重写次数。

## 快速开始

需要 Python 3.10+。本机已在 `.conda/envs/ChunkLoop` 创建独立 Conda 环境。由于本机 `conda.exe` 有 DLL 启动问题，直接调用该环境中的 Python 最稳定：

```powershell
& '.\.conda\envs\ChunkLoop\python.exe' -m chunkloop.cli demo --events
& '.\.conda\envs\ChunkLoop\python.exe' -m unittest discover -s tests -v
& '.\.conda\envs\ChunkLoop\python.exe' -m benchmarks.compare
```

在其他机器上可以使用 `conda env create -f environment.yml` 创建同名环境。包无运行时第三方依赖。Windows 上运行异步任务需要本机回环套接字可用。

### 使用真实模型

复制 [`examples/basic.json`](examples/basic.json) 并填写任务、证据和块计划。也可省略 `blocks`，由规划模型生成计划；这时还需设置 `CHUNKLOOP_PLANNER_MODEL`。设置 OpenAI 兼容端点的环境变量：

```powershell
$env:CHUNKLOOP_BASE_URL = 'https://your-endpoint/v1'
$env:CHUNKLOOP_API_KEY = '<your-key>'
$env:CHUNKLOOP_WRITER_MODEL = 'writer-model'
$env:CHUNKLOOP_REVIEWER_MODEL = 'reviewer-model'
$env:CHUNKLOOP_PLANNER_MODEL = 'planner-model' # 仅自动规划时需要
$env:CHUNKLOOP_PLANNER_ENABLE_THINKING = 'false' # 端点支持时可缩短结构化规划耗时
$env:CHUNKLOOP_REVIEWER_ENABLE_THINKING = 'false'
& '.\.conda\envs\ChunkLoop\python.exe' -m chunkloop.cli run examples/basic.json --checkpoint data/run.json
```

中断后加 `--resume`；只重做某块及其下游时加 `--resume --invalidate design`。`--invalidate` 用逗号分隔多个块 ID。修改规格中的指令或证据后加 `--resume --rebase`，它会比较旧检查点的输入清单，只复用未变化的块。通用 `context` 变化会让全部块失效。`--events` 将实时事件以 JSON 行输出到标准错误流。单次模型调用默认不设时限；生产部署可按需要用 `--call-timeout` 设置。不要将凭证写入规格文件或提交到仓库。模型端点需要支持 `/chat/completions` 和 JSON 对象响应格式。

### 嵌入 Python

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

详见 [架构与状态契约](docs/architecture.md)、[验证方法](docs/validation.md)及[真实模型验证记录](docs/real-model-validation-2026-10-30.md)。项目代码遵循 [Apache-2.0](LICENSE)；保留原项目历史和许可来源。
