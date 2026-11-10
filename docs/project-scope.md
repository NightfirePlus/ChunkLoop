# 当前项目范围

截至 2026-11-10，第一阶段的交付范围已完成：可运行的核心流程、示例、回归测试和保留正负案例的小型研究。当前按研究原型收尾；复杂长文稳定收益与生产环境可靠性属于后续验证范围。阶段结论见 [README](../README.md)，具体结果见[本轮流程研究](../research/workflow-study.md)。

项目的必要组成是：`chunkloop/` 的引擎、契约、持久化与模型适配器；`examples/` 的运行入口；`tests/` 的回归检查；`research/` 的完整批次证据；`tools/` 的生成、评分、复算和历史验证工具。`benchmarks/` 的模拟延迟实验只用于观察调度重叠。

本次整理删除了已经完成使命的三个选题脚本，以及 `assemble_extension_scores`、`combine_adaptive_scores`、`replay_adaptive_writingbench`、`report_routed_writingbench`、`reconcile_writingbench_connection_retries`；固定的选题与对账结果已进入实证证据。另删除独立探索分支 `compare_length_expansion`、`compare_coverage_revision` 和对应测试，它们不被当前引擎或生成入口调用。历史文档中的这些命令仅记录当时的操作，不再是当前可运行入口。编辑前的代码备份位于本机忽略追踪的 `.tmp/cleanup-2026-11-10.zip`。

保留三份历史批次汇总器是为了保留当时的评分口径；旧版 24 题离线复核从 `python -m tools.verify_evidence` 进入，本轮流程实验使用 `python -m tools.study_workflow --summarize` 并指定报告文件。新增实验应复用生成、评分和复算入口，直接保存输入、输出、失败状态及评审理由。只有结果表而没有原始产物的结论应明确标注证据范围。

优点、缺点和实现细节分别由运行证据与代码支持。维护通知用于理解反馈回流；真实模型批次用于观察质量、篇幅、耗时和失败；二者都不能代替一般性优越性的证明。
