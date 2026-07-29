# 本地 Cross-Encoder Reranker

## 定位

TraceMind 使用两阶段检索：

1. Qdrant Dense + BM25 RRF 负责从当前 active generation 召回前 10 个候选。
2. 独立本地 Cross-Encoder 按 Query 与完整候选文本的相关性重新排序，最终返回前 5 个。

Dense 解决语义召回，BM25 解决关键词和技术标识召回，Reranker 只负责二阶段排序。当前
模型为 `Qwen/Qwen3-Reranker-0.6B`，推荐 CUDA、float16、`max_length=1024`、
`batch_size=2`。模型 raw score 是排序 Logit，不是概率、置信度、准确率或百分比，不
设置 raw score threshold。

## 独立服务

CrossEncoder 只在 `app.reranker_server` 进程中加载。主 Backend 仅保留轻量异步 HTTP
Client，不在 Backend 或 Celery Worker 中加载模型。服务只监听 `127.0.0.1:8011`，
必须使用一个 Uvicorn Worker，进程内最大并发推理数固定为 1。

Windows 本地离线启动：

```cmd
cd /d E:\pycharmprojects\TraceMind\backend
set HF_HOME=E:\ai-cache\huggingface
set HF_HUB_OFFLINE=1
set TRANSFORMERS_OFFLINE=1
uv run --no-sync uvicorn app.reranker_server:app --host 127.0.0.1 --port 8011 --workers 1
```

模型缓存路径是本机运行配置，不硬编码到仓库。禁止使用多个 Uvicorn Worker，也不要把
服务监听到 `0.0.0.0`。`/health/live` 只表示进程存活；`/health/ready` 只有模型成功
加载且未因 OOM 失效时才返回 ready。

## 显存与运行模式

GTX 1650 4GB 无法稳定同时驻留 GPU Reranker 与 GPU Embedding Worker。默认
`QUERY_EMBEDDING_DEVICE=cpu`、`INDEX_EMBEDDING_DEVICE=cpu`。交互模式运行 Reranker
时保持索引 Worker 使用 CPU或停止。

需要 GPU 索引时：

1. 停止 Reranker 服务。
2. 设置 `INDEX_EMBEDDING_DEVICE=cuda`。
3. 启动单独 Celery Worker并完成索引。
4. 停止 GPU Celery Worker。
5. 恢复 CPU Worker或重新启动 Reranker。

旧 `EMBEDDING_DEVICE` 仍作为未配置新变量时的兼容回退值。

## 降级语义

RAG 默认请求 Hybrid Top 10。Reranker 成功时使用重排后的 Top 5；连接失败、超时、
503、OOM或响应无效时，静默回退原始 Hybrid RRF Top 5，不能把 Reranker 故障当成
无答案，也不能让 RAG 返回 503。

`/search/reranked` 是明确的调试接口，不执行静默降级；Reranker 未启用或不可用时返回
安全 503。原有 `/search/hybrid` 和 `/search/semantic` 保持不变。

当前不使用 BGE 备用模型，不实现训练、微调、量化、Weighted RRF 或阈值自动调优。
当前也没有检索评测集；后续应通过真实数据评估 MRR、Recall@K 和 nDCG。
