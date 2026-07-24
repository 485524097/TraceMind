# TraceMind 基础工程架构

## 当前结构

当前仓库由 FastAPI 后端、Vue 3 前端、本地文档存储和 Docker Compose 基础设施组成。后端负责健康检查、知识库 CRUD、Document 增量导入及外部客户端生命周期；前端提供知识库和文档管理页面。

## 分层边界

- API 层：处理 HTTP 请求、响应、参数校验和状态码，不承载复杂业务逻辑。
- Service 层：控制知识库、文档和解析用例、数据库事务与文件补偿，不依赖 FastAPI HTTP 异常。
- Repository 层：封装 KnowledgeBase、Document、DocumentVersion 和 DocumentChunk 的异步 SQLAlchemy 数据访问，不提交事务或操作文件。
- Storage 层：流式写入、SHA-256、路径校验、原子移动和 trash 暂存，不处理 HTTP 或数据库。
- Parsing 层：同步、确定性的 Parser 与 Chunker，不访问数据库、HTTP、Celery 或向量服务。
- Embedding 层：通过 Provider Protocol 隔离模型实现，业务 Service 不直接依赖 SentenceTransformer。
- Indexing/Integration 层：封装 Qdrant Collection、Point、过滤检索及外部服务连接检查。

## 基础服务职责

- PostgreSQL：保存 KnowledgeBase、Document 和 DocumentVersion 元数据及约束。
- Redis：为 Celery 提供 Broker 与 Result Backend，并为后续缓存预留基础；当前不保存业务缓存。
- Qdrant：保存单一 Collection 中的 Dense、BM25 Sparse named vector 与可追溯 Payload，并执行 RRF。
- Celery：异步执行 DocumentVersion 解析和索引；每个任务创建独立 AsyncEngine/Session 并在结束时释放。

## 知识库管理数据流

Vue 知识库页面通过原生 fetch Service 调用 `/api/v1/knowledge-bases`。FastAPI 路由完成 Schema 校验和 HTTP 错误映射，KnowledgeBase Service 执行业务规则和事务控制，KnowledgeBase Repository 通过异步 Session 访问 PostgreSQL，响应再按原路径返回页面。

健康检查流程保持不变：存活接口不访问外部服务；就绪接口并行检查 PostgreSQL、Redis 和 Qdrant。

## 文档导入数据流

Vue 以 multipart/form-data 逐文件调用 Document API。Service 规范化文件名，Storage 分块写入 root 内临时文件并计算 SHA-256；Repository 按同知识库 normalized_name 查询逻辑文档和最新版本。新文件或新版本在 flush 后移动到 UUID 正式路径，commit 失败则删除本次文件；未变化直接清理临时文件。

删除 Document 时先原子移动文档目录到 `.trash/<operation_uuid>`，数据库提交失败则恢复，成功后清理 trash。KnowledgeBase 与 Document 不级联，非空知识库由 Service 和外键共同禁止删除。

## 存活与就绪检查

- 存活检查表示 FastAPI 进程能够响应，不依赖任何外部组件。
- 就绪检查表示应用所需的 PostgreSQL、Redis 和 Qdrant 均可连接；任一失败返回 HTTP 503，但不会导致应用退出。

## 文档解析数据流

上传事务提交后，DocumentParsingDispatcher 仅投递版本 UUID。Worker 锁定版本并根据状态决定执行、跳过或接管 stale processing；ParserRegistry 选择纯文本、Markdown、代码、PDF 或 DOCX Parser；DeterministicChunker 生成带页码、行号、章节和语言的 ChunkDraft；Service 在单一事务中替换该版本的 DocumentChunk 并更新状态。

自动入队失败不会回滚已导入文件，版本保持 pending 并返回 `parsing_queued=false`。强制重解析在新结果全部准备完成前保留旧 Chunk；解析或数据库写入失败时旧 Chunk 继续可用。

## Dense 与 BM25 索引数据流

解析成功后单独派发 indexing task。Worker 以 generation UUID claim 版本，在数据库事务外批量生成 Dense Embedding，并把原始 Chunk 上下文交给本地 Qdrant Server 生成 BM25 Sparse Vector；写入后核对 Point 数。最终通过行锁和 generation compare-and-set 激活索引，再尽力清理旧 generation。检索先从 PostgreSQL 获取当前版本的 active generation，再使用这些 generation 过滤 Qdrant。

新 Collection 同时包含 `dense_v1` 和带 IDF modifier 的 `bm25_v1`。已有 Dense Collection 在线增加 Sparse Schema，不删除 Collection 或 Point；旧 Point 在强制重新索引前没有 Sparse Vector，但仍能通过 Dense 路径参与 RRF。

Hybrid Search 为 Dense 与 BM25 各执行一个共享业务 Filter 的 Prefetch，再由 Qdrant `Fusion.RRF` 按排名融合。Dense Prefetch 使用现有 0.50 阈值，Sparse 和最终融合结果不应用该阈值。

## Citation-grounded RAG 数据流

RAG API 复用 Hybrid Search 和 active generation，Context Builder 将完整原始 Chunk 与
可追溯元数据组成 JSON Sources。LLM Provider 通过官方 OpenAI SDK 调用兼容的流式
Chat Completions；Citation Guard 过滤不存在的 `[Sx]`，FastAPI 原生 SSE 将 retrieval、
token、no_answer、done 或 error 事件发送给 Vue。该链路是单轮且无状态，不写入数据库。

## 尚未实现

当前尚未实现 OCR、AST 解析、Reranker、Weighted RRF、对话历史、Agent 和用户权限。“文件已导入”不代表“文件已解析”，“解析成功”也不代表“已建立检索索引”。
