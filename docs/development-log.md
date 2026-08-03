# 开发日志

本文件用于保留每次开发的决策与验证证据。按时间倒序新增记录；不要用提交信息替代本日志。

## 2026-08-03 - Java 符号元数据检索与引用贯通

- **目标**：把 Java Parser 产生的四个可空符号字段贯通到索引、检索、RAG、会话来源快照和前端展示，同时兼容旧 Chunk、旧 Qdrant Point 与旧会话 JSON。
- **范围**：Qdrant Payload、Dense/BM25 索引文本、Dense/Hybrid/Reranked Search Result、`RagSource`、Grounded Prompt、Conversation Source JSON、Chunk/搜索/引用前端类型与展示；不修改 Query Rewrite、Path Scope、检索参数、Citation Guard 或评测基线。
- **决策**：Qdrant Payload 对四字段统一写入字符串或 `null`，读取时仅接受非空字符串；索引文本仅在符号存在时按 `Symbol`、`Signature`、`Kind` 顺序追加标签，无符号 Chunk 保持原输出；前端使用可空可选字段并按 signature、qualified name、name、section、document name 降级。
- **兼容性**：旧 Point 缺失字段或字段类型异常时返回 `None`；旧 Conversation Source JSON 无需迁移；普通文档、非 Java Parser 和 Java fallback 的字段保持 `None`；相同文件重索引后新 Point 自动携带符号字段。
- **验证**：Backend Ruff format/check 132 files、mypy 86 files、compileall 通过；非集成测试 383 passed / 20 deselected；隔离 PostgreSQL Migration upgrade/downgrade/upgrade 通过且四列均 nullable；integration 20 passed / 383 deselected；Frontend lint、type-check、14 files / 75 tests、build 通过。
- **容器验证**：按约束仅执行一次公共镜像构建；构建在读取 `python:3.12.13-slim-bookworm` 元数据时因 Docker Hub 匿名令牌请求连接超时退出（exit code 1），未生成 `tracemind-app:0.1.0`。该失败发生在 Dockerfile 执行前，不属于代码、磁盘或镜像解包故障；未自动重试。PostgreSQL、Redis、Qdrant 仍为 healthy，三个命名 Volume 均存在。
- **遗留项**：网络恢复后由用户决定是否重试一次公共镜像构建；镜像成功后再在 Backend/Celery Worker 两个服务入口验证 Tree-sitter ABI 14。当前 ABI 容器验证未执行；不提交、不推送。

## 2026-07-31 - Docker 构建磁盘峰值优化

- **目标**：缓解 Windows Docker Desktop 在大型 Torch/CUDA 应用镜像并行导出时的 C 盘空间耗尽、Engine 失联和重复镜像解包问题。
- **范围**：仅调整 Backend Dockerfile 的文件属主层和 Compose 的应用镜像复用；不修改 Stage 11B 业务实现、检索参数、依赖版本或评测基线。
- **背景与约束**：Docker 数据仍位于 C 盘默认 WSL VHDX，数据盘约 53.21 GB；C 盘可用约 11.14 GB、E 盘可用约 32.74 GB，均不满足本任务规定的安全构建余量。必须保留 PostgreSQL、Redis、Qdrant Volume，禁止直接移动或删除 VHDX。
- **决策**：使用 `COPY --chown=app:app` 赋予应用源码属主，移除遍历大型 `.venv` 的 `RUN chown -R app:app /app`；Backend 与 Celery Worker 共用 `tracemind-app:0.1.0`，仅 Backend 保留 build 定义，Worker 只覆盖启动命令。
- **未采用方案**：本次不拆分 CPU/GPU Torch dependency group，不删除镜像、缓存或 Volume，不改变本地 Windows uv/CUDA 环境，也不在 Docker Engine 不可用和磁盘余量不足时重新构建。
- **问题与处理**：初始 `docker version` 仅返回 Client，Linux Engine 命名管道不存在；完成 Volume 归档校验、Docker Desktop 重装与数据恢复后，Docker 数据盘迁移至 `E:\DockerData`，三个项目 Volume 保持原数据。
- **验证**：Docker Client/Server 29.6.2；PostgreSQL、Redis、Qdrant 均为 healthy；三个命名 Volume 存在；Compose 配置与 `git diff --check` 通过。公共镜像构建与镜像内 ABI 探针留待 Stage 11B 全部门槛通过后执行一次。
- **遗留项**：评估为 Backend 提供 CPU-only Torch 依赖组、Celery Worker 按实际 GPU 部署需求选择 CUDA 组；该调整涉及锁文件与部署矩阵，应作为独立 Docker 优化任务处理。

## 2026-07-30 - Java 符号级解析后端

- **目标**：使用 Tree-sitter 为 Java 文档生成可追溯的类型、成员与初始化器 Chunk 元数据。
- **范围**：Java Parser、通用代码分块复用、Chunk 符号字段、数据库迁移及后端局部测试；尚未进入索引、RAG、Conversation 或前端。
- **决策**：以绝对 UTF-8 半开字节区间收集可靠符号，统一校验、排序、回补未覆盖源码后转换为 `ParsedBlock`；Tree-sitter 整体不可用时复用 `CodeParser` 安全降级。
- **未采用方案**：不保存完整类型正文，不实现跨文件类型解析、调用图、符号表或其他语言 Tree-sitter Parser。
- **实现摘要**：`.java` 独占 `JavaTreeSitterParser`；类型 Chunk 仅包含声明头和左花括号；成员独立成块；紧邻同父级 Javadoc 归入声明；四个可空符号字段贯穿解析、Chunk、Repository 与 Schema。
- **验证**：Parser/Chunker 36 passed；解析服务、Repository 与 Schema 16 passed；Ruff、compileall、局部 mypy 和 `git diff --check` 通过。PostgreSQL 集成测试因未配置外部测试数据库而 14 skipped。
- **遗留项**：后续贯通 Qdrant Payload、Dense/BM25 包装、Search Result、RAG/Conversation Source 与前端展示，并在数据库可用时执行 migration 往返及集成持久化测试。

## 记录模板

### YYYY-MM-DD - <主题>

- **目标**：要解决的用户问题与完成标准。
- **范围**：涉及的模块、接口、数据或配置；明确不包含的范围。
- **背景与约束**：现状、兼容性、性能、隐私、成本或交付约束。
- **决策**：采用的方案，以及为什么适合当前约束。
- **未采用方案**：候选方案及不采用原因。
- **实现摘要**：关键文件、数据流或行为变化。
- **问题与处理**：遇到的问题、根因、处理方式和仍存风险。
- **验证**：执行命令、测试/评测数据、人工验证步骤及结果。
- **遗留项**：未解决问题、技术债和后续动作。
- **关联**：相关 issue、PR、设计文档、实验记录或提交。

## 2026-07-30 - 建立工程化开发规范

- **目标**：建立可持续记录设计决策、问题和验证证据的文档体系。
- **范围**：根目录开发规范及 `docs/design/`、`docs/experiments/` 文档骨架；不修改业务代码。
- **决策**：采用“任务日志 + 专项设计文档 + 实验评测计划”三层记录。任务日志记录事实，设计文档沉淀长期决策，实验文档约束量化验证。
- **未采用方案**：未单独引入 ADR 工具或第三方知识库，避免 MVP 阶段增加维护系统和访问依赖。
- **验证**：确认目录和文件已创建；未执行代码测试，因为没有修改业务代码。
- **遗留项**：首个检索/RAG 功能开发时，应补充首份架构决策和评测基线。
