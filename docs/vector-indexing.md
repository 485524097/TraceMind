# Dense 与 BM25 双向量索引

## 当前范围

DocumentVersion 解析成功后异步生成 Dense Embedding，并把 Sparse 文本交给本地 Qdrant Server 生成 BM25。单一 Collection `tracemind_chunks` 使用 `dense_v1` 和 `bm25_v1` 两个 named vector。Dense 负责语义召回；BM25 负责关键词、类名、方法名、配置项和 SQL 标识符召回。

默认模型为 `Qwen/Qwen3-Embedding-0.6B`，向量维度 1024，距离为 Cosine。首次实际索引或查询会下载模型；模块导入、应用启动和 CI 单元测试不会下载模型。CI 使用 FakeEmbeddingProvider。

## 索引一致性

Worker claim 时生成 UUID generation，并将版本标记为 processing。Embedding 推理与 Qdrant 网络请求均在数据库事务之外执行。Qdrant 写入使用 wait=true，按 `QDRANT_UPSERT_BATCH_SIZE` 顺序分批，写入后按 generation 验证 Point 数量。业务操作使用 `QDRANT_OPERATION_TIMEOUT_SECONDS`，健康检查仍使用独立的 `HEALTHCHECK_TIMEOUT_SECONDS`。

最终数据库事务重新锁定 DocumentVersion，只有 `index_status=processing` 且 generation 仍匹配的 Worker 才能标记 succeeded。stale Worker、迟到 Worker或已失去所有权的 Worker 不能激活 generation，也不能覆盖新 Worker 状态。成功激活后尽力清理旧 generation；清理失败不破坏已激活索引。

Dense Embedding 输入仍由 Document、可选 Section、Type、可选 Language 和原始 Content 组成。Sparse 文本只拼接文档名、可选章节和未改写的 Chunk 正文，完整保留大小写、标点、下划线、连字符和配置路径，不预分词或处理 stopwords。Payload 中仍保存未经修改的原始 Chunk 正文和对应 hash。Point ID 仍由 `document_version_id:index_generation:chunk_index` 派生为确定性 UUIDv5。

## 检索事实来源

语义检索先从 PostgreSQL 查询知识库内每个 Document 的当前版本。`succeeded` 状态下有效的 active generation 可以检索；force reindex 期间，如果 `indexed_at >= parsed_at`，`processing` 状态下的旧 active generation 仍可检索；reparse 后 `indexed_at < parsed_at` 的旧 generation 不可检索。随后把这些 generation 作为 Qdrant 必选过滤条件，Qdrant Payload 不能单独决定当前版本。

可选过滤包括 language 和 document_id。独立 heading Point 保留在索引中以维持 Chunk 计数语义，但通过 Qdrant `must_not` 排除。Dense API 的默认 `SEMANTIC_SEARCH_SCORE_THRESHOLD=0.50` 保持不变。Hybrid 查询只把该阈值用于 Dense Prefetch；BM25 Prefetch 无固定阈值，最终 RRF 结果也无二次阈值。返回结果保留原始正文、score 及完整引用元数据。

## 配置

- `QDRANT_COLLECTION_NAME=tracemind_chunks`
- `QDRANT_DENSE_VECTOR_NAME=dense_v1`
- `QDRANT_SPARSE_VECTOR_NAME=bm25_v1`
- `QDRANT_BM25_MODEL=qdrant/bm25`
- `QDRANT_BM25_TOKENIZER=multilingual`
- `QDRANT_BM25_LANGUAGE=none`
- `QDRANT_OPERATION_TIMEOUT_SECONDS=60`
- `QDRANT_UPSERT_BATCH_SIZE=64`
- `SEMANTIC_SEARCH_SCORE_THRESHOLD=0.50`
- `HYBRID_DENSE_PREFETCH_LIMIT=20`
- `HYBRID_SPARSE_PREFETCH_LIMIT=20`
- `EMBEDDING_MODEL_NAME=Qwen/Qwen3-Embedding-0.6B`
- `EMBEDDING_DIMENSION=1024`
- `EMBEDDING_BATCH_SIZE=16`
- `EMBEDDING_DEVICE=auto`
- `DOCUMENT_INDEX_STALE_AFTER_SECONDS=1800`

新 Collection 自动创建 Dense + Sparse Schema，Sparse 使用 IDF modifier。已有 Dense Collection 通过 `create_vector_name` 在线增加 Sparse Schema，随后复核配置；并发 400/409 只有在复核成功时才视为成功。系统绝不自动删除或重建 Collection。旧 Dense-only Point 不自动补写 Sparse Vector，仍可通过 Dense 查询及 Hybrid Dense Prefetch 召回；强制重新索引后补齐 BM25。

## API

- `POST /api/v1/knowledge-bases/{kb_id}/documents/{document_id}/versions/{version_id}/index`
- `GET /api/v1/knowledge-bases/{kb_id}/documents/{document_id}/versions/{version_id}/index-status`
- `POST /api/v1/knowledge-bases/{kb_id}/search/semantic`
- `POST /api/v1/knowledge-bases/{kb_id}/search/hybrid`

索引请求 body 为 `{"force": false}`。两种检索 body 均支持 query、limit、language 和 document_id。Dense API 的 score 是余弦相似度；Hybrid API 的 score 是 Qdrant RRF 排名融合分数，不是余弦相似度，不能与 0.50 比较。
