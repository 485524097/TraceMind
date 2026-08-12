# ADR 001: Remove Java Symbol-aware Retrieval

- Status: Accepted
- Date: 2026-08-11

## Context

Stage 12 为 Java 方法和类实现了 Tree-sitter 解析、symbol chunk metadata、精确 scope、重载
方法消歧和 direct-scroll fallback。它验证了精确代码定位的技术可行性，但把 TraceMind
推向代码智能产品，与个人学习资料 RAG 的长期定位不一致。

历史固定评测共 12 个 case：Case Pass Rate、Scope Resolution Accuracy、Exact Target
Recall@5、Signature Exclusion Accuracy、Fallback Reason Accuracy、Negative Trigger
Accuracy 与 Path Disambiguation Accuracy 均为 1.0000；观测 P95 为 46739.19 ms，其中首个
cold case 为 46739.19 ms。完整逐 case 结果归档在
`docs/architecture/archive/stage12-java-symbol-evaluation.md`。

## Decision

从 v1.0 runtime 删除 Java parser、Tree-sitter 依赖、SymbolScopeResolver、symbol payload/
lookup/direct-scroll、symbol API/UI contract 及 active evaluation suite。通过新的 0010 migration
删除 DocumentChunk 的五个 symbol columns，并重建本地 Qdrant 索引。

Java、Python、JavaScript、TypeScript、SQL 等统一作为普通技术文本。保留通用 code chunk、
language、relative path、line range、Citation 和语言无关 Path Scope。

## Why not extend to Python or JavaScript

扩展更多 AST 会成倍增加 parser、schema、索引、检索规则和评测维护成本，却不加强 v1.0 的
日常学习闭环。通用 Dense + BM25 + RRF + Reranker 已能回答普通代码资料问题；以后只有在
真实长期使用数据证明必要时，才重新评估代码智能产品方向。

## Consequences

维护面、schema、payload、前端 metadata 和测试矩阵显著缩小。代价是 v1.0 不保证方法重载
级精确定位；用户仍可依靠文件 Path Scope、行号和 Citation 验证答案。
