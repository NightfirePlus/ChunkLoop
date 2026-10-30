# 验证方法

真实百炼模型的调度对照、完整链路结果与限制见 [2026-10-30 真实模型验证记录](real-model-validation-2026-10-30.md)。

## 自动测试

`python -m unittest discover -s tests -v` 目前覆盖：独立块的写审重叠、审核退回与重写上限、依赖块读取已通过内容、块级证据范围、检查点恢复、输入哈希失配、局部失效重跑、终审阻断和计划拒绝循环依赖。

## 可复现延迟基准

`python -m benchmarks.compare --blocks 6 --write-delay 0.08 --review-delay 0.08 --repeats 3` 在同一进程、同一延迟参数和同样的模拟适配器下比较逐块串行写审与 ChunkLoop 双队列。输出中位数和降幅。该基准只验证调度重叠，不代表真实模型质量、网络延迟或费用。真实模型评测应固定端点、模型、提示词、输入集和缓存，并记录输出质量、请求数、token 数及错误率。

原软文仓库的 [2026-10-22 历史测试](https://github.com/NightfirePlus/ruanwen-agent/blob/a6ae1a6ff95733d28804bbc1c8ff5917ec235cea/docs/runtime-benchmark-2026-10-22.md) 同时改变了模型、缓存和其他流程，可作为设计动机，不能用于证明独立包的净收益。
