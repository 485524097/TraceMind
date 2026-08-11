# Archived Stage 12 Java Symbol Evaluation

该评测是已删除 Java Symbol-aware Retrieval 的历史证据，不再是 v1.0 active gate。

| Metric | Result |
| --- | ---: |
| Cases | 12 / 12 passed |
| Case Pass Rate | 1.0000 |
| Scope Resolution Accuracy | 1.0000 |
| Exact Target Recall@5 | 1.0000 |
| Signature Exclusion Accuracy | 1.0000 |
| Fallback Reason Accuracy | 1.0000 |
| Negative Trigger Accuracy | 1.0000 |
| Path Disambiguation Accuracy | 1.0000 |
| P95 latency | 46739.19 ms (observational) |

Case latency (ms): 46739.19, 2619.95, 2347.19, 2362.61, 2562.84, 2783.56,
2533.15, 2779.84, 2371.12, 2236.16, 2179.71, 2610.87。

这套结果证明了实现正确性，但不能证明它适合 TraceMind 的产品方向；ADR 001 记录删除决策。
