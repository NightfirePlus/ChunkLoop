# 验证方法

本轮架构改动的[流程研究](../research/workflow-study.md)包含 36 次受控运行、8 次真实模型运行（共 48 次调用），分别记录加速、依赖链无收益、有限修订成功与失败。所有原始结果均随仓库保留。

当前可随仓库离线复核的真实模型证据见 [实证记录](../research/README.md)，运行 `python -m tools.verify_evidence` 可核对完整 24 题。它同时保留篇幅改善、质量回退、审核意见和耗时增加。

先看[维护通知实例](first-example.md)：它给出输入、计划、审核轨迹和实际成稿，可在本地直接复现。这是流程验证，使用虚构事实和确定性适配器，不代表真实模型的内容质量。

已有的 [WritingBench 适配与验证](writingbench.md)、[诊断实验](writingbench-diagnosis-2026-10-31.md)和[真实模型验证记录](real-model-validation-2026-10-30.md)保留完整实验条件与问题。这些探索性结果用于定位篇幅、语言、审核可靠性等问题，不作为普遍质量或效率优势的证据。

## 自动测试

`python -m unittest discover -s tests -v` 在 2026-11-10 的收尾验证中通过 43 项测试，覆盖独立块写审重叠、审核退回与重写上限、依赖快照与不确定性传递、块级证据范围、检查点恢复、输入哈希失配、局部失效重跑、有限终审修订、无法定位时停止、修订执行失败保留旧稿、计划拒绝循环依赖，以及历史实证复算等行为。

## 可复现延迟基准

`python -m benchmarks.compare --blocks 6 --write-delay 0.08 --review-delay 0.08 --repeats 3` 在同一进程、同一延迟参数和同样的模拟适配器下比较逐块串行写审与 ChunkLoop 双队列。输出中位数和降幅。该基准只验证调度重叠，不代表真实模型质量、网络延迟或费用。真实模型评测应固定端点、模型、提示词、输入集和缓存，并记录输出质量、请求数、token 数及错误率。

原软文仓库的 [2026-10-22 历史测试](https://github.com/NightfirePlus/ruanwen-agent/blob/a6ae1a6ff95733d28804bbc1c8ff5917ec235cea/docs/runtime-benchmark-2026-10-22.md) 同时改变了模型、缓存和其他流程，可作为设计动机，不能用于证明独立包的净收益。
