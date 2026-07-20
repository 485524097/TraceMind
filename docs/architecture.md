# TraceMind 基础工程架构

## 当前结构

当前仓库由 FastAPI 后端、Vue 3 前端、本地文档存储和 Docker Compose 基础设施组成。后端负责健康检查、知识库 CRUD、Document 增量导入及外部客户端生命周期；前端提供知识库和文档管理页面。

## 分层边界

- API 层：处理 HTTP 请求、响应、参数校验和状态码，不承载复杂业务逻辑。
- Service 层：控制知识库与文档用例、数据库事务和文件补偿，不依赖 FastAPI HTTP 异常。
- Repository 层：封装 KnowledgeBase、Document 和 DocumentVersion 的异步 SQLAlchemy 数据访问，不提交事务或操作文件。
- Storage 层：流式写入、SHA-256、路径校验、原子移动和 trash 暂存，不处理 HTTP 或数据库。
- Integration 层：封装 Redis、Qdrant 等外部服务客户端及连接检查。

## 基础服务职责

- PostgreSQL：保存 KnowledgeBase、Document 和 DocumentVersion 元数据及约束。
- Redis：为 Celery 提供 Broker 与 Result Backend，并为后续缓存预留基础；当前不保存业务缓存。
- Qdrant：后续保存向量及其检索索引；当前只检查服务连接，不创建 Collection。
- Celery：提供后台任务执行基础；当前没有文档解析或其他业务任务。

## 知识库管理数据流

Vue 知识库页面通过原生 fetch Service 调用 `/api/v1/knowledge-bases`。FastAPI 路由完成 Schema 校验和 HTTP 错误映射，KnowledgeBase Service 执行业务规则和事务控制，KnowledgeBase Repository 通过异步 Session 访问 PostgreSQL，响应再按原路径返回页面。

健康检查流程保持不变：存活接口不访问外部服务；就绪接口并行检查 PostgreSQL、Redis 和 Qdrant。

## 文档导入数据流

Vue 以 multipart/form-data 逐文件调用 Document API。Service 规范化文件名，Storage 分块写入 root 内临时文件并计算 SHA-256；Repository 按同知识库 normalized_name 查询逻辑文档和最新版本。新文件或新版本在 flush 后移动到 UUID 正式路径，commit 失败则删除本次文件；未变化直接清理临时文件。

删除 Document 时先原子移动文档目录到 `.trash/<operation_uuid>`，数据库提交失败则恢复，成功后清理 trash。KnowledgeBase 与 Document 不级联，非空知识库由 Service 和外键共同禁止删除。

## 存活与就绪检查

- 存活检查表示 FastAPI 进程能够响应，不依赖任何外部组件。
- 就绪检查表示应用所需的 PostgreSQL、Redis 和 Qdrant 均可连接；任一失败返回 HTTP 503，但不会导致应用退出。

## 尚未实现

当前实现原始文件导入和版本管理，但尚未实现文本提取、Document Chunk、全文或向量索引、Reranker、RAG、模型供应商和用户权限。“文件已导入”不代表“文件已解析或已建立检索索引”。
