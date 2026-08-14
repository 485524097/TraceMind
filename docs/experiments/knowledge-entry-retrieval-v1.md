# Verified Knowledge Retrieval v1 验证记录

日期：2026-08-14

## 问题

Stage 16 把已验证 KnowledgeEntry 加入默认 RAG 候选集。需要确认两件事：原有文档检索没有质量回退；新增知识来源只在具备真实、有效索引代次时参与召回，且引用身份不会伪装成 Document。

## 约束

- 固定文档评测继续使用 24 条 synthetic corpus、冻结 Gold Dataset 和正式 baseline，不修改问题、阈值、BM25、RRF 或 Top K。
- 文档回归必须限定到原评测 Document，因此不会混入 KnowledgeEntry；这用于隔离验证原有检索路径。
- 当前固定语料没有 KnowledgeEntry Gold Evidence，不能用文档指标推断知识来源的真实召回质量。
- Query Embedding 使用本地 `Qwen/Qwen3-Embedding-0.6B`、1024 维和 CPU；没有资料发送到远程服务，也没有外部模型费用。

## 采用方案

1. 在保留的隔离 Collection `tracemind_eval_dim1024` 上运行正式 Hybrid 回归，使用 `--fail-on-regression` 与 `hybrid_v1.json` 比较。
2. 使用真实 Qdrant 集成测试写入 Document 与 KnowledgeEntry 两种 payload，验证统一搜索、来源区分和 entry-scoped 删除。
3. 使用 Service/Repository 测试验证 verified 进入索引，unverified/outdated 不可检索，内容更新会使旧代次立即失效，且 `answer_snapshot` 不进入索引文本。

没有采用临时构造知识问答质量分数，因为用少量手写向量或自问自答语料得到的 Recall/MRR 不具备产品解释力。

## 固定文档回归结果

预热后的正式回归进程退出码为 0，所有冻结质量阈值通过。

| 指标 | 正式 baseline | Stage 16 | 变化 |
| --- | ---: | ---: | ---: |
| Hit@1 | 0.5909 | 0.5909 | 0.0000 |
| Hit@5 | 1.0000 | 1.0000 | 0.0000 |
| Recall@5 | 0.8409 | 0.8409 | 0.0000 |
| MRR@5 | 0.7364 | 0.7424 | +0.0061 |
| nDCG@5 | 0.6572 | 0.6623 | +0.0051 |
| All-required@5 | 0.8182 | 0.8182 | 0.0000 |
| P50 | 235.27 ms | 2949.00 ms | +2713.74 ms |
| P95 | 375.39 ms | 3549.68 ms | +3174.29 ms |

延迟触发了 baseline 的 P95 警告，但没有触发质量失败。该次运行固定在 CPU；其结果位于同环境近期 1024 维实验的波动区间内。由于 Document-scoped HTTP endpoint 不执行本次新增的知识联合召回，不能把延迟差异归因于 Stage 16，但也不能声称延迟没有风险。

第一次运行的首条请求在模型冷启动时达到 60 秒超时，导致 Recall、MRR 和 All-required 各损失一条 Case 并使回归进程失败。模型预热后按相同参数重跑通过。因此冷启动超时被保留为真实部署风险，不作为检索质量回退结论。

## 新增知识来源验证结果

- 真实 Qdrant 集成测试确认同一 Collection 能联合返回 Document 与 KnowledgeEntry，并能按 `knowledge_entry_id` 删除知识点而不删除文档点。
- PostgreSQL 集成测试确认派生索引状态更新不改变 maintained `updated_at`，只有内容版本相符的 verified active generation 可进入检索。
- Prompt、Citation Guard、RAG source schema 和前端 Evidence 测试确认知识来源继续使用真实 `[Sx]`，显示“已验证知识”并链接维护页。

## 遗留风险与下一步

- 需要新增独立的 KnowledgeEntry Gold Dataset，至少覆盖经验优先、文档优先、冲突事实、outdated 排除和多来源引用，再记录 Recall、引用正确率、回答支持率、端到端延迟和模型费用。
- 本轮没有启用远程 LLM，因此没有测量最终回答质量、token 成本或远程数据暴露；这些指标必须在选定 Provider 后单独记录。
- 本地 CPU 冷启动可能超过当前 60 秒评测请求超时，后续应评估模型预热、健康就绪语义或更明确的首次请求提示。
