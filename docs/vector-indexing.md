# Dense 向量索引与语义检索

## 当前范围

TraceMind 当前仅实现 Dense Semantic Search。DocumentVersion 解析成功后异步生成 Dense Embedding，并写入单一 Qdrant Collection `tracemind_chunks` 的 named vector `dense_v1`。当前没有 BM25、Sparse Vector、Hybrid Search、Reranker、RAG 或 LLM Answer。

默认模型为 `Qwen/Qwen3-Embedding-0.6B`，向量维度 1024，距离为 Cosine。首次实际索引或查询会下载模型；模块导入、应用启动和 CI 单元测试不会下载模型。CI 使用 FakeEmbeddingProvider。

## 索引一致性

Worker claim 时生成 UUID generation，并将版本标记为 processing。Embedding 推理与 Qdrant 网络请求均在数据库事务之外执行。Qdrant 写入使用 wait=true，按 `QDRANT_UPSERT_BATCH_SIZE` 顺序分批，写入后按 generation 验证 Point 数量。业务操作使用 `QDRANT_OPERATION_TIMEOUT_SECONDS`，健康检查仍使用独立的 `HEALTHCHECK_TIMEOUT_SECONDS`。

最终数据库事务重新锁定 DocumentVersion，只有 `index_status=processing` 且 generation 仍匹配的 Worker 才能标记 succeeded。stale Worker、迟到 Worker或已失去所有权的 Worker 不能激活 generation，也不能覆盖新 Worker 状态。成功激活后尽力清理旧 generation；清理失败不破坏已激活索引。

Point ID 是由 `document_version_id:index_generation:chunk_index` 派生的确定性 UUIDv5。Payload 保存知识库、文档、版本、Chunk、generation、正文、hash、类型、语言、章节、页码和行号。

## 检索事实来源

语义检索先从 PostgreSQL 查询知识库内每个 Document 的当前版本，并只接受 `index_status=succeeded` 的 active generation；随后把这些 generation 作为 Qdrant 必选过滤条件。Qdrant Payload 不能单独决定当前版本。

可选过滤包括 language 和 document_id。返回结果包含 score、正文及完整引用元数据。

## 配置

- `QDRANT_COLLECTION_NAME=tracemind_chunks`
- `QDRANT_DENSE_VECTOR_NAME=dense_v1`
- `QDRANT_OPERATION_TIMEOUT_SECONDS=60`
- `QDRANT_UPSERT_BATCH_SIZE=64`
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
