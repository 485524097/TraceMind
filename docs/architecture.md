# TraceMind 基础工程架构

## 当前结构

当前仓库由 FastAPI 后端、Vue 3 前端和 Docker Compose 基础设施组成。后端负责配置加载、健康检查和外部服务客户端生命周期；前端仅提供项目首页和后端存活状态展示。

## 分层边界

- API 层：处理 HTTP 请求、响应、参数校验和状态码，不承载复杂业务逻辑。
- Service 层：后续承载用例编排和业务规则，本阶段尚未创建。
- Repository 层：后续封装 PostgreSQL 与其他持久化访问，本阶段仅建立异步 Session 基础。
- Integration 层：封装 Redis、Qdrant 等外部服务客户端及连接检查。

## 基础服务职责

- PostgreSQL：后续保存结构化业务数据；当前只验证异步连接，不含业务表。
- Redis：为 Celery 提供 Broker 与 Result Backend，并为后续缓存预留基础；当前不保存业务缓存。
- Qdrant：后续保存向量及其检索索引；当前只检查服务连接，不创建 Collection。
- Celery：提供后台任务执行基础；当前没有文档解析或其他业务任务。

## 本阶段数据流

浏览器打开 Vue 首页后，前端服务层请求 `GET /api/v1/health/live`。FastAPI 直接返回应用名称和版本，不访问外部服务。运维或开发者请求 `GET /api/v1/health/ready` 时，后端并行检查 PostgreSQL、Redis 和 Qdrant，并只返回组件级结果。

## 存活与就绪检查

- 存活检查表示 FastAPI 进程能够响应，不依赖任何外部组件。
- 就绪检查表示应用所需的 PostgreSQL、Redis 和 Qdrant 均可连接；任一失败返回 HTTP 503，但不会导致应用退出。

## 尚未实现

本阶段没有实现知识库、Document/Chunk 模型、上传、解析、全文或向量检索、Reranker、RAG、模型供应商、用户权限以及对应前端页面。这些能力不得从当前基础工程状态推断为可用。
