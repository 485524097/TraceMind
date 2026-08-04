# 开发日志

本文件用于保留每次开发的决策与验证证据。按时间倒序新增记录；不要用提交信息替代本日志。

## 2026-08-03 - Java 符号精确作用域与安全回退

- **目标**：在不改变普通 Dense/Hybrid/Reranked 排名参数的前提下，将可可靠解析且能在当前知识库 active generation 中验证的 Java 符号冻结为精确 Qdrant 作用域；不存在、歧义或超出安全扫描上限时保留用户符号文本并回退普通检索。
- **范围**：`symbol_lookup_keys` Qdrant Payload、keyword Payload Index 在线补建与类型验证、Symbol Scope Resolver、Dense/Hybrid 公共过滤器、exact 空结果 direct scroll，以及三个 Search API 的安全调试元数据；不实现 Query Rewrite/RAG/Conversation 符号作用域或前端展示，不修改 Dense threshold、BM25、RRF、Reranker 参数、Top K 或固定 baseline 资产。
- **索引决策**：`symbol_lookup_keys` 使用 Qdrant `keyword` Payload Index，数组元素通过 `MatchValue` 做精确匹配。新 Point 仅在 key 列表非空时写入该字段；`ensure_collection` 对新旧 Collection 幂等补建，`wait=True`，400/409 竞争后刷新确认，已存在但不是 keyword 时拒绝继续。该过程不重建 Collection、不删除旧 Point、不修改 HNSW，也不触发 HNSW rebuild。Qdrant 官方资料检索日期：2026-08-03。
- **作用域语义**：优先级保持显式 `document_id` → 已验证完整路径 → 已验证符号 → 普通检索。带参数方法只验证完整 signature key；无参数限定的方法按同一 document/kind/qualified name 归为 overload family，长符号拆分出的多个 Point 也视为同一逻辑身份。嵌套类型除完整名和 simple name 外，还生成从首个类型式大写段开始的后缀 key，使 `demo.Outer.Nested` 可由 `Outer.Nested` 精确验证，但不做 import 或类型解析。跨 document、跨 qualified owner 或跨 kind 命中为 `ambiguous`，无可靠命中为 `not_found`，扫描达到安全上限且仍有下一页为 `unsupported`。
- **回退与兼容**：新解析的 Java 文档在正常索引后即可精确匹配；Stage 12A 以前解析的旧 Java 文档必须先重新解析、再重新索引，单独强制重索引无法凭空生成 lookup keys。旧非 Java 文档无需处理，继续参与普通检索。旧 Point 缺失、为 null 或含异常类型的 lookup keys 不能建立 exact 身份，但仍参与无符号过滤的普通检索。Qdrant 技术异常继续映射为安全 503，不伪装成 `not_found`，也不静默扩大范围。普通无符号查询和仅路径查询不增加 symbol index ensure/scroll 验证请求，仍只执行原有检索请求。
- **问题与处理**：初版 qualified-dot 候选会把 `tide.collect.late-grace-seconds` 一类小写配置键当作符号并产生无效验证请求；最终规则要求成员引用的直接 owner 或全限定类型的末段呈 Java 类型式大写开头，并排除 URL、Markdown 链接及 `import`/`package` 声明。该规则只产生待验证候选，精确身份仍由 Qdrant payload 验证。专项测试直接读取固定 24 条评测查询，确认全部不产生 Symbol candidate，因此均保持 `symbol_scope_mode=none`。
- **direct scroll**：exact Dense/Hybrid 受现有阈值影响返回空时，以同一 symbol filter 分页 scroll，按 `relative_path`、`start_line`、`end_line`、`chunk_index`、`chunk_id` 稳定排序；`score=1.0` 仅是 exact identity sentinel，由 `ranking_mode=symbol_exact` 明确区分，并非语义置信度。若原始 Point 存在但经 document/symbol 作用域校验后为空，也会刷新 active generation 并至多重试一次，绝不移除 symbol filter。
- **合并前审查修正**：payload index 无论正常创建还是 400/409 竞争，都在创建批次结束后重新读取 Collection schema，逐项确认存在且为 keyword；RAG 在 12A-3 前显式关闭符号解析，避免多余探测后丢弃作用域；`PreparedRetrievalQuery` 调用改用具名参数，降低字段扩展后的错位风险；lookup key 运行时规范化拒绝字符串及混合类型容器。
- **验证**：专项测试 188 passed；完整非集成测试 447 passed / 21 deselected；完整隔离集成测试 21 passed / 447 deselected，其中真实 Qdrant 1.18.2 使用随机临时 Collection 验证数组 MatchValue、keyword schema 与 scroll，PostgreSQL 使用 `tracemind_stage12a_test` 验证 Migration/persistence，并额外完成显式 upgrade/downgrade/upgrade。最终代码状态下固定 24 条 Hybrid baseline 退出码 0：Recall@5 0.840909、MRR@5 0.742424、All-required@5 0.818182、Hit@1 0.590909；相对 baseline 无质量回归，仅 P95 延迟阈值警告。MRR 的 +0.006061 只来自普通查询 `ret-015` 的同一召回集合内目标 Chunk 从第 5 移至第 3；该查询没有 symbol candidate，新增 filter 为 `None` 时不产生条件，判断为当前 Qdrant/索引快照排序差异而非符号作用域路径，未修改 baseline。Ruff format 147 files、Ruff check、mypy 90 source files、compileall、`uv lock --check` 与 `git diff --check` 通过。
- **遗留项**：12A-3 的 Query Rewrite/RAG/Conversation 作用域贯通和前端展示尚未开始；独立 Java symbol evaluation dataset 留到 12A-4。

## 2026-08-03 - Java 符号 lookup key 与查询候选解析

- **目标**：为 Java 类型、方法重载、构造函数、字段、初始化器和枚举常量生成可持久化、大小写敏感的 `v1` lookup key，并以纯函数解析显式符号查询候选，为后续精确检索验证提供确定性输入。
- **范围**：共享 Java 符号规范化、Tree-sitter AST lookup key、`ParsedBlock` → `ChunkDraft` → `DocumentChunk` → Repository 主链、nullable JSON Migration 和 Symbol Query Parser；不接入 Qdrant、检索作用域、Dense/Hybrid/Reranked、Query Rewrite、RAG、API 或前端。
- **决策**：只做 Unicode NFC，不做 casefold、import 解析或泛型擦除；参数 key 忽略参数名、注解、修饰符、返回类型和 throws，保留泛型、wildcard 和数组维度，并把 varargs 规范化为数组。多变量 field 共享一个 Chunk，但为每个 declarator 生成 key；compact constructor 从 record components 派生参数类型，失败时只保留无参数列表级 key。
- **兼容性**：`symbol_lookup_keys` 仅允许 `None` 或非空去重列表；数据库空数组加载后规范化为 `None`；旧 Chunk 不回填。公共 Chunk/Search/RAG API 暂不暴露该内部字段，CodeParser fallback 保持 `None`。
- **12A-2 冻结约束**：exact symbol 存在性必须使用独立 Qdrant filter/count/scroll 验证，不能用 Dense threshold 判断；已验证 exact scope 但 Hybrid 返回空时必须直接 scroll 精确 Point；同一长符号的多个 Chunk 按 `start_line`、`chunk_index` 稳定排序；需要语义排序时只复用现有 Reranker；不得修改 Dense threshold、BM25、RRF 或 Top K。
- **验证**：`uv lock --check` 通过；目标单元测试 72 passed；隔离 PostgreSQL Migration roundtrip 与 Repository 持久化 2 passed；Ruff format 137 files、Ruff check、mypy 89 source files、compileall 和 `git diff --check` 通过。
- **遗留项**：Qdrant Payload/Index、符号存在性验证、检索回退、RAG 和前端尚未开始，留待 Stage 12A-2 及后续阶段。

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
