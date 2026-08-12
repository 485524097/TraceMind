# TraceMind v1.0 Architecture

TraceMind 保持现有 FastAPI、SQLAlchemy、PostgreSQL、Qdrant、Redis、Celery 与 Vue 架构，
按五个 Plane 描述职责，不引入新的应用框架。

## 1. Ingestion Plane

File → Upload → Parse → Chunk → Embed/Index → Ready。

Storage 管理文件，Parsing 做确定性提取和通用 Chunk，Service 负责事务与任务编排，Qdrant
只存检索需要的通用文档元数据。代码文件保留 language、relative path、start/end line，
与其他技术文本走同一条路径。

v1.0 intentionally removes directory-based source ingestion. Source-project directory topology
preservation is not a v1.0 requirement. 用户仍可一次选择多个普通文件，但每个文件独立导入、
解析和建立索引。

## 2. Retrieval Plane

Query → optional Query Rewrite → Dense + BM25 → RRF → optional Reranker → Context。

Path Scope 是语言无关的精确文档限定。Retrieval 失败映射为安全错误；Reranker 不可用时
保留已有混合检索降级语义。

## 3. Answer Plane

Question → deterministic Router → Direct / Knowledge RAG → Streaming LLM → Citation Guard。

路由只识别规范化后完全匹配的寒暄白名单；子串、混合表达和不确定输入都进入 RAG。
SSE 在耗时准备前发送可验证的 pipeline 状态，不展示模型私有推理。

Query Embedding provider 在应用级复用。配置 RAG 时，lifespan 在后端完成启动后只调度一次
后台模型预热；模型缓存锁阻止重复加载，预热失败只记录安全诊断且不伪造 readiness。

## 4. Knowledge Plane

Conversation → KnowledgeEntry → Knowledge → derived Knowledge Map。

KnowledgeEntry 保存安全 allowlist snapshot。Knowledge Map 从 PostgreSQL 实时派生，不参与
检索，也不持久化图节点或边。

## 5. Experience & Observability Plane

上传字节、处理阶段、RAG pipeline、各阶段 latency、stream/cancel/error 是面向用户和开发
诊断的统一观测面。只有可靠 numerator/denominator 才显示百分比。

## 数据与迁移

历史 migration 不修改。`20260811_0010` 线性删除 DocumentChunk 的 Java Symbol 字段；旧
本地 Qdrant collection 需要重建，不提供无价值的旧 payload 兼容层。
