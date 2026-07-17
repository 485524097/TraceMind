# TraceMind 基础工程架构

## 当前结构

当前仓库由 FastAPI 后端、Vue 3 前端和 Docker Compose 基础设施组成。后端负责配置加载、健康检查、知识库 CRUD 和外部服务客户端生命周期；前端提供项目首页、后端存活状态和知识库管理页面。

## 分层边界

- API 层：处理 HTTP 请求、响应、参数校验和状态码，不承载复杂业务逻辑。
- Service 层：控制知识库用例、业务异常以及 commit/rollback，不依赖 FastAPI HTTP 异常。
- Repository 层：封装 KnowledgeBase 的异步 SQLAlchemy 数据访问，不提交事务或处理 HTTP 错误。
- Integration 层：封装 Redis、Qdrant 等外部服务客户端及连接检查。

## 基础服务职责

- PostgreSQL：后续保存结构化业务数据；当前只验证异步连接，不含业务表。
- Redis：为 Celery 提供 Broker 与 Result Backend，并为后续缓存预留基础；当前不保存业务缓存。
- Qdrant：后续保存向量及其检索索引；当前只检查服务连接，不创建 Collection。
- Celery：提供后台任务执行基础；当前没有文档解析或其他业务任务。

## 知识库管理数据流

Vue 知识库页面通过原生 fetch Service 调用 `/api/v1/knowledge-bases`。FastAPI 路由完成 Schema 校验和 HTTP 错误映射，KnowledgeBase Service 执行业务规则和事务控制，KnowledgeBase Repository 通过异步 Session 访问 PostgreSQL，响应再按原路径返回页面。

健康检查流程保持不变：存活接口不访问外部服务；就绪接口并行检查 PostgreSQL、Redis 和 Qdrant。

## 存活与就绪检查

- 存活检查表示 FastAPI 进程能够响应，不依赖任何外部组件。
- 就绪检查表示应用所需的 PostgreSQL、Redis 和 Qdrant 均可连接；任一失败返回 HTTP 503，但不会导致应用退出。

## 尚未实现

当前只实现 KnowledgeBase 模型与 CRUD。尚未实现 Document/Chunk、上传、解析、全文或向量检索、Reranker、RAG、模型供应商和用户权限。这些能力不得从知识库管理功能推断为可用。
