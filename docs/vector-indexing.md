# Dense 向量索引与语义检索

## 当前范围

TraceMind 当前仅实现 Dense Semantic Search。DocumentVersion 解析成功后异步生成 Dense Embedding，并写入单一 Qdrant Collection `tracemind_chunks` 的 named vector `dense_v1`。当前没有 BM25、Sparse Vector、Hybrid Search、Reranker、RAG 或 LLM Answer。

默认模型为 `Qwen/Qwen3-Embedding-0.6B`，向量维度 1024，距离为 Cosine。首次实际索引或查询会下载模型；模块导入、应用启动和 CI 单元测试不会下载模型。CI 使用 FakeEmbeddingProvider。

## 索引一致性

Worker claim 时生成 UUID generation，并将版本标记为 processing。Embedding 推理与 Qdrant 网络请求均在数据库事务之外执行。Qdrant 写入使用 wait=true，按 `QDRANT_UPSERT_BATCH_SIZE` 顺序分批，写入后按 generation 验证 Point 数量。业务操作使用 `QDRANT_OPERATION_TIMEOUT_SECONDS`，健康检查仍使用独立的 `HEALTHCHECK_TIMEOUT_SECONDS`。

最终数据库事务重新锁定 DocumentVersion，只有 `index_status=processing` 且 generation 仍匹配的 Worker 才能标记 succeeded。stale Worker、迟到 Worker或已失去所有权的 Worker 不能激活 generation，也不能覆盖新 Worker 状态。成功激活后尽力清理旧 generation；清理失败不破坏已激活索引。

Document Embedding 输入由 Document、可选 Section、Type、可选 Language 和原始 Content 组成，以补充文件与章节上下文；Payload 中仍保存未经修改的原始 Chunk 正文和对应 hash。Point ID 仍由 `document_version_id:index_generation:chunk_index` 派生为确定性 UUIDv5。修改 Embedding 输入格式后，已有文档必须执行强制重新索引。

## 检索事实来源

语义检索先从 PostgreSQL 查询知识库内每个 Document 的当前版本。`succeeded` 状态下有效的 active generation 可以检索；force reindex 期间，如果 `indexed_at >= parsed_at`，`processing` 状态下的旧 active generation 仍可检索；reparse 后 `indexed_at < parsed_at` 的旧 generation 不可检索。随后把这些 generation 作为 Qdrant 必选过滤条件，Qdrant Payload 不能单独决定当前版本。

可选过滤包括 language 和 document_id。独立 heading Point 保留在索引中以维持 Chunk 计数语义，但通过 Qdrant `must_not` 排除，不参与语义检索。默认 `SEMANTIC_SEARCH_SCORE_THRESHOLD=0.50` 在 Qdrant 查询阶段过滤低分结果；这是 Dense Baseline，并不宣称适合所有资料。无满足阈值结果时返回空列表。返回结果保留原始正文、score 及完整引用元数据，前端默认展示 5 条。

## 配置

- `QDRANT_COLLECTION_NAME=tracemind_chunks`
- `QDRANT_DENSE_VECTOR_NAME=dense_v1`
- `QDRANT_OPERATION_TIMEOUT_SECONDS=60`
- `QDRANT_UPSERT_BATCH_SIZE=64`
- `SEMANTIC_SEARCH_SCORE_THRESHOLD=0.50`
- `EMBEDDING_MODEL_NAME=Qwen/Qwen3-Embedding-0.6B`
- `EMBEDDING_DIMENSION=1024`
- `EMBEDDING_BATCH_SIZE=16`
- `EMBEDDING_DEVICE=auto`
- `DOCUMENT_INDEX_STALE_AFTER_SECONDS=1800`

Collection 不存在时自动创建并建立必要 Payload Index。若已有 Collection 的 vector name、dimension 或 distance 不兼容，系统明确失败，绝不自动删除或重建。

## API

- `POST /api/v1/knowledge-bases/{kb_id}/documents/{document_id}/versions/{version_id}/index`
- `GET /api/v1/knowledge-bases/{kb_id}/documents/{document_id}/versions/{version_id}/index-status`
- `POST /api/v1/knowledge-bases/{kb_id}/search/semantic`

索引请求 body 为 `{"force": false}`。语义检索 body 支持 query、limit、language 和 document_id。
