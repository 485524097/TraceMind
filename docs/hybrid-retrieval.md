# Dense + BM25 RRF 混合检索

## 目标与边界

Dense Embedding 适合自然语言语义召回；BM25 适合关键词、类名、方法名、配置项和 SQL
标识符等精确召回。TraceMind 使用 Qdrant 的两个 named vector：

- `dense_v1`：Cosine Dense Vector。
- `bm25_v1`：`qdrant/bm25` 服务端 Sparse Vector，tokenizer 为 `multilingual`，
  language 为 `none`，modifier 为 `IDF`。

BM25 由本地 Qdrant Server 执行，不使用 FastEmbed、不下载 BM25 模型，也不访问
Qdrant Cloud。Hybrid 负责第一阶段召回；可选本地 Cross-Encoder 负责第二阶段重排。
当前不实现 Weighted RRF 或检索评测集自动调参。

## 索引与在线升级

新 Collection 同时创建 Dense 与 Sparse Schema。已有 Dense-only Collection 使用
`create_vector_name` 在线增加 `bm25_v1`，不删除 Collection、现有 Point 或 Payload
Index；添加后会重新读取并验证 Schema。并发请求返回 400/409 时，只有复核到正确的
IDF Sparse Schema 才视为成功。

Sparse 文本依次拼接文档名、非空章节标题和原始 Chunk 正文，不添加固定英文标签，
不改写、截断、预分词或清洗技术标识。旧 Point 不会因 Schema 升级自动获得 Sparse
Vector，但仍可通过 Dense 路径参与混合检索。对已有文档执行“强制重新索引”可补齐
BM25。

## 查询与分数

Hybrid Search 在 Qdrant Query API 中执行两个 Prefetch：

1. Dense Prefetch 使用 `dense_v1` 和现有 0.50 Dense threshold。
2. BM25 Prefetch 使用 `bm25_v1`，不设置固定 threshold。

两条路径使用相同的 knowledge base、active generation、可选 language/document 和
heading 排除 Filter。最终由 `Fusion.RRF` 按排名融合，不在应用层手工合并，也不对
最终结果再应用 0.50 threshold。RRF score 是融合排名分数，不是余弦相似度或回答
置信度。

Dense 调试接口保持为
`POST /api/v1/knowledge-bases/{knowledge_base_id}/search/semantic`；Hybrid 接口为
`POST /api/v1/knowledge-bases/{knowledge_base_id}/search/hybrid`；Reranked 调试接口
为 `POST /api/v1/knowledge-bases/{knowledge_base_id}/search/reranked`。RAG 默认使用
Hybrid Top 10 后重排到 Top 5；Reranker 不可用时保持原始 RRF 顺序降级。Reranker
raw score 不与 Dense 或 RRF score 直接比较，也不设置阈值。
