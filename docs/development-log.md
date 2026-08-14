# 开发日志

本文件用于保留每次开发的决策与验证证据。按时间倒序新增记录；不要用提交信息替代本日志。

## 2026-08-10 - UI Refresh 收尾基线

- **目标**：把未提交的 UI Refresh 整理为可独立评审的基线，修复 Conversation 历史回答与 Evidence Inspector 的来源关联，补齐窄屏布局、设计 token 和关键交互回归测试。
- **范围与约束**：只修改前端展示、Conversation 状态关联、前端测试和本记录；不开始后续知识沉淀功能，不新增数据模型或 migration，不修改 Retrieval、RAG service、Java Symbol、Evaluation dataset/corpus/expected/baseline。
- **决策**：Evidence 选择使用 assistant message id 作为状态身份，每条回答继续使用自身 `sources`，citation 仍复用既有 `parseAnswerSegments` 产出的 `[Sx]` identity；Inspector DOM 定位使用 message id 与 source id 组合，避免不同回答都含 `[S1]` 时串联。加载或切换 Conversation 时默认选择该会话最后一条 assistant，streaming 时选择当前临时 assistant，并在服务端 message id 返回后同步更新。
- **响应式与 token**：桌面保留 200px Conversation、弹性 Answer、360px Evidence 三栏；960px 以下改为 Conversation 左栏加 Answer/Evidence 上下布局，680px 以下三块纵向排列，Evidence 仍可访问。`--code-bg`、`--code-border` 映射到现有 surface/border token，旧 `--text-secondary` 使用改为 `--color-text-secondary`，未引入新颜色体系或 UI framework。
- **旧实现处理**：全仓前端引用搜索确认 `RagAnswerPanel.vue` 只被其专属测试引用，当前 View/production code 均未使用，因此删除组件、专属测试及 `.rag-*` 废弃样式；同时删除已被 `.conv-*` 结构替代的旧 `.conversation-*` Conversation CSS。
- **浏览器验收修复**：真实 Edge 验收发现 Knowledge Base 行把 Dropdown 嵌套在 `RouterLink` 中，点击 `···` 会直接导航；改为同一 resource row 内的主体链接和同级 Dropdown，保持行主体导航且让菜单独立可操作。另确认按需引入 Element Plus 时遗漏 Dropdown/Menu/Item 样式，导致菜单无背景和列表样式，补齐三个现有样式入口。两项均未改变 API、数据或页面信息架构。
- **未采用方案**：不重写 ConversationView，不把 citation identity 改成全局编号，不隐藏窄屏 Evidence，不为 Element Plus Popper/Teleport 浏览器实现编写脆弱测试，也不扩大到服务层或检索逻辑。
- **验证**：`npm run type-check` 通过；`npx eslint src/ --max-warnings 100` 通过；`npx vitest run --reporter=verbose` 为 15 files、83 tests 全部通过；`npm run build` 通过。真实 Edge 最终只读页面/响应式回归 52/52 通过，覆盖 Home、Knowledge Bases、Documents、Conversation、历史 `[S1]` 绑定、Conversation 切换及 1440/900/600px；独立真实 streaming 在 23 秒完成，观察到 23 个增量快照、retrieval Evidence、临时 id → 持久化 id 和最终 source identity，cancel 状态恢复通过。Console 无 Vue/Element Plus/runtime 错误，只有 Vite 缺失 `favicon.ico` 的 404 开发噪声。
- **遗留项**：提交、推送、PR 和合并均等待人工确认；后续知识沉淀阶段保持未开始。

## 2026-08-05 - Stage 12A 最终自动化验收补充

- **目标**：补齐 Stage 12A-3、Stage 12A-4 及 Hybrid 跨 generation 确定性 RRF 修复的最终自动化验收证据；本记录覆盖此前日志中“集成测试跳过”和“旧 24 条严格 baseline 未通过”的中间状态。
- **RAG 与 Conversation 作用域贯通**：Symbol Scope 在 Query Rewrite 前完成解析和验证；Query Rewrite 只替换剩余的 `semantic_query`，不能改变已冻结的显式 `document_id`、Path Scope、Symbol Scope 或 overload signature。Hybrid Search 与 Reranker 复用同一个 `PreparedRetrievalQuery`，不会根据改写后的文本二次解析作用域。最终回答 Prompt 始终使用用户原始问题。
- **安全元数据**：只有 `symbol_scope_mode=exact` 时，Prompt 才接收经过验证的 kind、qualified name 和 signature。SSE 与 Conversation `generation_metadata` 只公开 `symbol_scope_mode`、`symbol_scope_reason`、`scoped_symbol_kind`、`scoped_symbol_qualified_name` 和 `scoped_symbol_signature`，不向 Prompt、SSE、Conversation、日志或前端暴露内部 `symbol_lookup_key`。
- **前端展示**：Search 调试页、RAG 当前执行过程和 Conversation 历史详情均展示安全 Symbol Scope。exact 显示“精确符号：<signature/qualified name/kind>”；`not_found`、`ambiguous` 和 `unsupported` 分别显示安全回退文案；none 不显示符号作用域行。旧响应、旧会话、未知枚举和损坏元数据统一安全降级为 none。
- **引用展示**：引用身份按 relative path、symbol signature、qualified name、symbol name、section、line range 的顺序展示。`ranking_mode=symbol_exact` 显示为“精确符号命中”；direct-scroll 的 `score=1.0` 不解释为 100% 相关度或模型置信度。
- **Hybrid 确定性修复**：确认原实现存在分支同分排序和最终 Fusion 同分排序两层跨 generation 不稳定。Dense 与 Sparse 改为使用 Qdrant `query_batch_points` 独立获取候选，分支按 `(-raw_score, stable_payload_key)` 排序，再在应用层使用固定 `k=2`、weight=1 的 RRF，最终按 `(-rrf_score, stable_payload_key)` 排序。
- **稳定排序键**：依次使用 `relative_path`（缺失时回退 `document_name`）、`start_line`、`end_line`、`chunk_index`、`content_hash`、`document_id`、`chunk_id`，Point ID 仅作为最后兜底。未使用 generation 或随机 UUID 作为主要排序身份。
- **未修改范围**：未修改 Dense threshold、BM25 文本或配置、Filter 语义、Candidate/Final Top K、Reranker、Semantic Search、Symbol direct scroll、固定评测 dataset、corpus、expected 或 baseline。
- **后端验证**：
  - 确定性 RRF/Qdrant Gateway：28 passed。
  - Stage 12A 专项：88 passed。
  - 非集成全量：467 passed，21 deselected。
  - 隔离 PostgreSQL/Qdrant 集成全量：21 passed，467 deselected，1 warning，耗时 66.92 秒。
  - 集成测试使用名称以 `_test` 结尾的隔离数据库 `tracemind_test`，未使用开发数据库。
  - Ruff format：153 files already formatted。
  - Ruff check：通过。
  - mypy：90 source files 通过。
  - compileall：通过。
- **前端验证**：
  - lint：通过。
  - type-check：通过。
  - Vitest：15 files，87 tests passed。
  - production build：通过。
- **固定 24 条 Hybrid Evaluation**：
  - 退出码：0。
  - Recall@5：0.840909。
  - MRR@5：0.742424。
  - All-required@5：0.818182。
  - Hit@1：0.590909。
  - P95：4092.49 ms，仅保留环境相关 warning。
  - 24 条普通查询均保持 `symbol_scope_mode=none`。
  - baseline、corpus、dataset 和 expected 未修改。
- **Java Symbol Evaluation**：
  - 12/12 cases passed。
  - Case Pass Rate：1.0。
  - Scope Resolution Accuracy：1.0。
  - Exact Target Recall@5：1.0。
  - Signature Exclusion Accuracy：1.0。
  - Fallback Reason Accuracy：1.0。
  - Negative Trigger Accuracy：1.0。
  - Path Disambiguation Accuracy：1.0。
  - 临时知识库和 Qdrant 数据清理成功。
- **三 generation 可复现性实验**：
  - Query Vector SHA 跨 generation 一致，最大绝对差为 0。
  - 同 generation 连续五次 Dense、Dense Exact、Sparse 和 Fusion 排序一致。
  - Dense、Dense Exact、Sparse 和应用层 Fusion Top 10 跨 generation 一致。
  - `ret-010` 目标排名固定为 1 / 1 / 1。
  - `ret-015` 目标排名固定为 3 / 3 / 3。
  - `ret-016` 目标排名固定为 1 / 1 / 1。
  - Point ID 跨 generation 仍然变化，但不再影响最终结果。
  - 临时知识库和 Qdrant generation 清理成功。
- **依赖证据**：`backend/pyproject.toml` 和 `backend/uv.lock` 相对 `develop` 无 Diff。当前环境未执行 `uv lock --check`，但本阶段未修改依赖声明或锁文件。
- **兼容性**：旧 Java 文档仍需重新解析并重新索引才能生成 lookup keys；仅强制重新索引旧 Chunk 无效。旧非 Java 文档和旧 Qdrant Point 继续兼容普通检索。
- **浏览器手工验收**：
  - 精确重载 Search：通过，仅命中 `demo.UserService#source(String)`。
  - 跨 package 的简单 owner 查询：正确判定 `ambiguous` 并回退普通检索。
  - 使用实际 relative path `demo/UserService.java` 后，Path Scope 与 Symbol Scope 联合消歧通过。
  - 不存在符号：正确判定 `not_found` 并回退普通检索。
  - RAG exact scope：通过，执行详情展示验证后的签名，引用包含相对路径、方法签名和行号。
  - `ranking_mode=symbol_exact` 正确显示为“精确符号命中”，未显示为 100% 相关度或模型置信度。
  - Conversation 页面刷新后，回答、引用和 Symbol Scope 均从持久化 metadata 正常恢复。
  - 普通 Redis 查询保持 `symbol_scope_mode=none`，不显示符号作用域行。
  - Search、RAG 和 Conversation 的 Network Response 中未发现 `"scoped_symbol_lookup_key":` 或 `"symbol_lookup_keys":` 字段。
- **当前状态**：自动化测试、独立 Symbol Evaluation、固定 24 条回归、三 generation 可复现性实验、隔离集成测试和浏览器手工验收均已完成。当前工作区未提交、未推送。

## 2026-08-04 - Hybrid 跨 generation 确定性 RRF 排序

- **目标**：修复相同语料重新索引后 Point ID 随 generation 改变，进而导致 Hybrid 同分候选顺序和固定评测 MRR/Hit@1 漂移的问题；保持召回参数、过滤语义、RRF 公式和最终 Top K 不变。
- **两层根因**：三代修复前实验确认 query vector SHA 完全一致且最大绝对差为 0，Dense 与 Dense Exact 一致，候选集合也一致；但 Dense/Sparse 分支的原始同分候选由 Qdrant 按 generation-dependent Point ID 等内部顺序返回，随后内建 Fusion 又用这些分支 rank 计算 RRF。`ret-010`、`ret-016` 的最终前两名均为 `0.8333334`，旧应用层再以 Point ID 破同分；`ret-015` 的三个 Sparse score 完全相同，底层分支 rank 随代变化并进一步产生 `0.25/0.20/0.16666667`。因此只替换最终 Fusion tie-break 无法修复已经进入 RRF 计算的分支 rank 漂移。
- **决策与实现**：`QdrantGateway.hybrid_search` 使用 qdrant-client 1.18.0 已有且类型匹配的 `query_batch_points`，并行批量取得原 Dense 与 Sparse 候选；Dense 保留原 threshold，Sparse 不增加 threshold，两个分支继续使用同一 Filter 和原 prefetch limit。集中纯函数按 `relative_path`（缺失回退 `document_name`）、有效起止行、`chunk_index`、`content_hash`、`document_id`、`chunk_id` 排序，Point ID 仅作最终兜底；各分支只在 raw score 完全相同时使用该键，不 round、不加 epsilon。
- **RRF 语义**：应用层仍使用 Qdrant 默认的 zero-based `1 / (2 + rank)`，固定 `k=2`、Dense/Sparse weight 均为 1，不混合原始 Dense/BM25 score；同 Point 两分支贡献累加，最终按 `(-rrf_score, stable_payload_key)` 排序并截取原 API limit。未修改 Dense threshold、BM25 文本/配置、prefetch limit、Filter、Symbol Scope、Reranker、Top K、dataset、corpus、expected 或 baseline。
- **未采用方案**：不再依赖 Qdrant 内建 Fusion 的未声明同分顺序；不把 generation 或随机 UUID 作为主要稳定键；不只修最终同分，因为这不能恢复 `ret-015` 在 Sparse 分支中已经漂移的 rank；不针对三个 Case 编写特例。
- **三代修复后验证**：三个 generation ID 均不同，Fusion Top 5 Point ID 序列也有 3 种，但三个 Case 的 query vector SHA 各只有 1 个且最大绝对差为 0；同代 5 次 Dense、Dense Exact、Sparse、应用层 Fusion 均只有 1 种序列，跨代四层 Top 10 也各只有 1 种序列。`ret-010` 目标 rank 固定 `1/1/1`（line 53 在 149 前），`ret-015` 固定 `3/3/3`（等分 Sparse 按 line 118、145、157），`ret-016` 固定 `1/1/1`（line 155 在 157 前）。作为根因对照，底层 Qdrant Fusion 的 `ret-015` 仍为 `3/4/5`，而应用层结果已稳定。实验正常清理临时文档、知识库和 Qdrant generation。
- **质量回归**：固定 24 条 Hybrid Evaluation 退出码 0；Recall@5 `0.840909`、MRR@5 `0.742424`、All-required@5 `0.818182`、Hit@1 `0.590909`，无质量失败且 baseline 未改。P95 `4092.49 ms` 仍触发观察性延迟 warning。24 个普通查询的 scope 字段均因默认 `none` 被响应的 `exclude_defaults` 省略，未启用 Symbol Scope。独立 Java Symbol Evaluation 12/12 通过，七项硬指标均为 1.0000，临时数据清理成功；本次冷启动 P95 `46739.19 ms` 仅记录。
- **自动化验证**：确定性 RRF/Qdrant Gateway 28 passed；Stage 12A 后端专项 88 passed；后端非集成 467 passed / 21 deselected；Ruff check、153 files format check、compileall、mypy 90 个 app source files 通过。前端 lint、type-check、15 files / 87 tests、build 通过。隔离 PostgreSQL/Qdrant 集成全量真实执行：21 passed / 467 deselected / 1 warning，耗时 66.92 秒。`TEST_DATABASE_URL` 指向名称以 `_test` 结尾的隔离数据库 `tracemind_test`，未使用开发数据库。
- **兼容与恢复**：旧 Point 缺失路径、行号或 hash 时稳定键安全降级，仍可参加普通检索；无需重建 Collection 或数据库迁移。若需回滚，只需恢复 Hybrid 为 Qdrant 内建 Fusion，不涉及数据迁移；诊断报告保留 Qdrant Fusion 与应用层 Fusion 两组结果，便于复查。

## 2026-08-04 - Java 符号作用域贯通 RAG 与会话元数据

- **目标**：把 Stage 12A-2 已验证的 Java Symbol Scope 安全贯通到 RAG、Query Rewrite、Prompt、SSE 与 Conversation generation metadata，同时保持普通问题、路径限定和显式 `document_id` 的既有行为。
- **范围**：RAG 检索编排、Query Rewrite 边界、Reranker 结果标签、Grounded Prompt、安全 SSE 元数据、Conversation 成功/无答案/失败/取消持久化和旧会话 JSON 兼容；不修改 Qdrant Filter、Dense/BM25/RRF、Reranker 参数、Top K、Citation Guard、前端和固定评测资产。
- **决策**：在 Query Rewrite 前只调用一次 `prepare_retrieval_query`，先完成路径和符号解析与 Qdrant 验证，再仅用 rewrite 结果替换 `semantic_query`。Hybrid 接收冻结的 `PreparedRetrievalQuery`，禁止根据改写文本重新解析范围；最终 Prompt 的 question 始终使用用户原始问题。只有 `symbol_scope_mode=exact` 时才向 Prompt 提供已验证的 kind、qualified name 和 signature；`fallback`/`none` 不提供符号身份。
- **安全与兼容**：SSE 和 Conversation metadata 只暴露 scope mode/reason 及已验证的符号显示字段，不暴露内部 `symbol_lookup_key`、Prompt、源码正文或内部异常。检索准备和 Hybrid 的技术故障统一映射为安全 503，并尽可能保留故障前已冻结的安全范围信息。旧 Conversation metadata 字典读取时补齐 `symbol_scope_mode=none` 和空符号字段，无需数据库迁移。
- **排序语义**：Reranker 不改变候选排序算法、分数或参数；仅在输入候选来自 exact direct-scroll 时保留 `ranking_mode=symbol_exact`，避免符号精确命中的来源身份在 RAG 主链中丢失。
- **未采用方案**：不让 Query Rewrite 重新生成符号范围，避免改写引入或改变精确过滤；不把 lookup key 作为公开调试字段，避免泄露内部持久化格式；不在本阶段增加前端展示或独立 Java symbol evaluation dataset，留待 12A-4。
- **问题与处理**：验证环境中的 PowerShell 找不到 `py` 启动器，且沙箱内项目 `.venv` 的 uv trampoline 返回 Windows permission denied；改为在获准的沙箱外直接调用现有 `.venv\\Scripts` 工具。该错误发生在测试进程启动前，不是业务代码或测试失败。
- **验证**：12A-3 定向测试 102 passed；全量非集成测试 461 passed / 21 deselected；隔离 PostgreSQL/Qdrant 集成测试 21 passed / 461 deselected；mypy 检查 90 个 source files，Ruff format/check、compileall 和 `git diff --check` 通过。当前机器 PATH 无 `uv` 且项目 Python 无 `uv` 模块，因此 `uv lock --check` 未能执行；本次未修改 `pyproject.toml` 或 `uv.lock`。
- **遗留项**：12A-4 再实现前端符号作用域展示与独立符号检索评测；本阶段不更新现有 24 条 Hybrid baseline，也不运行该评测，因为没有修改检索算法、过滤设计、参数或排序顺序。

## 2026-08-04 - Symbol Scope 前端展示与独立 Java 检索评测

- **目标**：在不让前端重新解析 Java 符号、不公开 lookup key、不修改检索参数的前提下，把后端已验证的 Symbol Scope 展示到 Search 调试页、RAG 当前执行过程和 Conversation 历史详情，并建立独立的 Java Symbol Retrieval Evaluation。
- **前端展示**：Search 在 Path Scope 附近显示符号类型、qualified name 和 signature；RAG 从 retrieval 事件起即时显示，并由 done/no-answer/error 的最终安全元数据更新；Conversation 只读取 assistant message 已持久化的 `generation_metadata`，页面刷新后仍可恢复。统一工具将未知、损坏或缺失枚举降级为 `none`，不显示无价值的“无符号”行。
- **用户文案**：exact 为“精确符号：<signature/qualified name/kind>”；`not_found` 为“符号未找到，已回退普通检索”；`ambiguous` 为“符号存在歧义，已回退普通检索”；`unsupported` 为“符号匹配范围过大，已回退普通检索”；none 不显示。旧 SSE 和旧 Conversation JSON 缺少字段时按 none 处理。
- **引用规则**：主身份仍是 `relative_path`，符号身份按 signature → qualified name → symbol name → section 降级，随后展示行号。`ranking_mode=symbol_exact` 显示“精确符号命中”，不把 direct-scroll 的 `score=1.0` 表述为 100% 相关或模型置信度；既有 retrieval/rerank score 语义未改变。
- **独立评测资产**：新增 5 个无外部依赖 Java 文件，覆盖 demo/example 同名 `UserService`、String/int overload、多个 constructor、field、nested type、record compact constructor 和 Unicode identifier；12 条独立 cases 覆盖完整限定、简单 owner、参数重载排除、overload family、路径/document_id 消歧、constructor、compact constructor、Unicode、not_found、ambiguous、普通配置键 negative trigger、qualified-dot 和 direct scroll。新 corpus、dataset、baseline、report 与旧 24 条资产完全隔离。
- **评测链路与结果**：runner 通过本地 HTTP API 创建临时 Knowledge Base、逐文件上传并等待真实 Parser/DocumentChunk/Embedding/Qdrant 主链，再调用 Hybrid Search；最终 12/12 通过，Case Pass Rate、Scope Resolution Accuracy、Exact Target Recall@5、Signature Exclusion Accuracy、Fallback Reason Accuracy、Negative Trigger Accuracy、Path Disambiguation Accuracy 均为 1.0000，P95 3483.88 ms 仅记录。所有临时 Java 文档、知识库和 Qdrant generation 均清理成功，残留临时知识库为 0。
- **旧 24 条回归**：未修改旧 dataset、corpus、expected 或 `hybrid_v1` baseline。使用新临时知识库重新解析和索引后，Recall@5 仍为 0.8409、All-required@5 仍为 0.8182，但 MRR@5 为 0.6970、Hit@1 为 0.5000，严格 runner 退出码 1；`ret-010`、`ret-016` 的目标从第 1 移到第 2，`ret-015` 从第 5 升到第 3。全部旧查询仍由既有专项测试确认为 `symbol_scope_mode=none`，因此没有证据表明排序漂移来自 Symbol Scope；禁止通过更新 baseline 或反复重建直到偶然通过来掩盖该结果。P95 4254.66 ms，保留已有延迟警告。临时旧评测知识库和 points 已清理，残留为 0。
- **验证**：前端 Symbol Scope 专项 4 files / 32 tests，前端全量 15 files / 87 tests，lint、type-check、build 通过；Symbol Evaluation 单元测试 3 passed；后端非集成 464 passed / 21 deselected，隔离集成 21 passed / 464 deselected；Ruff format 151 files、Ruff check、mypy 90 source files、compileall 和 `git diff --check` 通过。`git diff --exit-code develop -- backend/pyproject.toml backend/uv.lock` 返回 0；当前环境 PATH 无 `uv` 且项目 Python 无 `uv` 模块，因此未运行 `uv lock --check`。
- **问题与处理**：首次端到端运行因本地开发数据库停留在 Alembic `20260730_0006` 导致 Chunk replacement `ProgrammingError`；临时数据清理后按标准迁移升级到 `20260803_0008 (head)`，再运行成功。旧固定 corpus 的索引实测约 11–12 分钟，首次 10 分钟等待超时后已清理，第二次使用 20 分钟安全上限完成。实际外部 LLM 回答的浏览器手工验收未执行；Search 主链由真实评测覆盖，RAG/Conversation UI 和故障/无答案恢复由自动化测试覆盖。
- **兼容与遗留**：Stage 12A 前已解析的旧 Java 文档仍需用户主动重新解析并重新索引才能生成 lookup keys；不自动处理正式数据。当前合并阻塞项是旧 24 条严格 baseline 退出码 1，需要独立调查固定 corpus 重建后的 RRF 同分排序/索引可复现性，不应在本 PR 中调整 Dense/BM25/RRF/Top K 或 baseline。

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
# 2026-08-11 — Stage 13 Problem & Solution Knowledge

## 问题与约束

Conversation answers lacked a durable structured knowledge lifecycle. The implementation had to
preserve traceable evidence while staying independent from Retrieval and avoiding Tag/Source
subsystems.

## 采用方案

- Added one `knowledge_entries` table with editable fields, normalized tag values, nullable source
  foreign keys and immutable server-derived snapshots.
- Enforced one entry per completed assistant answer and same-KB provenance.
- Added Knowledge list/detail/edit/delete UI and a shared Evidence source renderer.
- Kept mutations within Service-layer transactions and protected non-empty knowledge bases.

## 未采用方案

Tag tables, source association tables, manual entries and new full-text infrastructure were
rejected as unnecessary for the MVP. Retrieval and Qdrant were not changed.

## 验证与结果

- Backend gates: Ruff check/format and mypy (95 source files) passed; pytest completed with
  475 passed and 24 skipped.
- Disposable PostgreSQL 18 migration/constraint/`SET NULL` integration completed with 4 passed;
  the test database used temporary credentials and no persistent volume.
- Frontend type-check, lint and production build passed; Vitest completed with 18 files and
  87 tests passed. The repository-wide Prettier check still reports 21 baseline files (including
  the already non-Prettier `main.css`); all new Stage 13 Vue/TypeScript files pass targeted
  Prettier validation. Unrelated baseline files were intentionally not reformatted.
- Headless Chrome walkthrough covered Knowledge list, detail, Evidence and a 500 px narrow
  viewport. The content and Evidence remain accessible without horizontal page overflow or
  console/runtime errors. Save/edit/delete state transitions are covered by Vitest and API tests
  against the same public contracts.

## 遗留风险

Substring search and canonical lowercase-like tags are intentional MVP limits. Snapshots preserve
deleted-document evidence, but later Knowledge Map relationships only include live documents.

# 2026-08-11 — Stage 14 Derived Knowledge Map

## Problem and constraints

TraceMind needed a way to understand relationships among its own saved knowledge without turning
the final MVP stage into a graph database or GraphRAG project. The map must remain read-only,
KB-scoped and transparent about why entries are related.

## Adopted design

- Added one runtime-derived endpoint over the existing Knowledge Base, Document and KnowledgeEntry
  repositories. There is no graph model, migration, persistence, cache or retrieval dependency.
- Live citation edges are the intersection of snapshot document IDs and current same-KB Documents.
  Related entry pairs aggregate shared normalized tags and shared live Documents.
- Added Cytoscape core only, using its built-in `cose` layout and interaction APIs. The UI provides
  type filters, Fit Graph, selection inspection and navigation to Knowledge/Document locations.
- Added Document query/focus handling so a map node can highlight the existing resource row.

## Alternatives not adopted

Graph databases, graph persistence, entity/LLM relation extraction, graph retrieval, layout
plugins, Vue wrappers and new browser-test infrastructure were rejected as outside the display-only
scope.

## Validation and result

- Backend Ruff check/format and mypy (98 source files) passed; the full suite completed with
  479 passed and 24 skipped.
- Frontend type-check, lint and build passed; Vitest completed with 20 files and 93 tests passed.
  Lazy-loading the Map route keeps the main production chunk at 412.14 kB and isolates the
  Cytoscape view in a 441.23 kB chunk, with no Vite chunk-size warning.
- Headless Chrome rendered a 7-node/10-edge live map at desktop and 500 px widths. Zoom/pan/drag
  are delegated to Cytoscape core; Fit/filter/selection/navigation lifecycle is covered by Vitest.
  Knowledge and Evidence remained accessible, the narrow inspector moved below the graph, and a
  Document node target filtered, scrolled to and highlighted its existing resource row without a
  console/runtime error.

## Current limits

The endpoint derives relationships in memory and intentionally has no large-graph pagination,
clustering or cache. These optimizations should be justified by real local data sizes before being
added.

# 2026-08-11 — Reproducible PyTorch CUDA environment

## Problem and constraints

The project depended on PyTorch only transitively through `sentence-transformers`. The lock file
therefore selected the PyPI CPU wheel on Windows, so repairing the virtual environment with
`uv sync --frozen` replaced the previously hand-installed CUDA wheel. The repair had to preserve
the Stage 13/14 code and the Linux CPU deployment while making the local Windows CUDA environment
reproducible from project metadata.

## Adopted design

- Declared `torch==2.13.0` directly and pinned it with explicit uv indexes: CUDA 12.6 on Windows,
  and the official CPU index on non-Windows platforms. A single lock now contains hashed wheels for
  all supported platform branches.
- Kept the existing execution split: the launcher runs the Reranker on CUDA while Backend, query
  embedding, indexing embedding and Celery remain on CPU. A CUDA-enabled Torch build can still run
  those CPU workloads on Windows; Linux containers and CI resolve the CPU wheel.
- Kept CUDA validation in the tracked provider-level preflight. The intentionally local/ignored
  launcher avoids a second Torch import, validates occupied ports with service health endpoints,
  runs migrations synchronously, and waits for Backend/Frontend readiness before opening the UI.
  Explicit CPU device settings retain their existing behavior.
- Model startup failures now log the model, requested device, Torch version, Torch CUDA runtime,
  CUDA availability, safe error classification and the original traceback. HTTP error responses
  remain generic and do not expose exception details.

## Alternatives not adopted

A one-off `pip install`, an unlocked accelerator override and automatic `uv pip --torch-backend`
were rejected because they do not make project-level `uv sync` reproducible. CPU/CUDA extras were
also not selected: uv project extras require callers to remember an extra on every sync, so a plain
sync could still replace the intended local wheel. The platform split matches the current Windows
local launcher and Linux CPU container/CI topology without adding another dependency workflow.

## Validation and result

- `uv 0.11.20`; NVIDIA driver `571.96`; GPU `NVIDIA GeForce GTX 1650`.
- `uv sync --frozen` installed `torch 2.13.0+cu126`; a second identical sync retained it.
  `torch.version.cuda` is `12.6`, `torch.cuda.is_available()` is true, and `uv pip check` reports
  102 compatible packages.
- A Linux x86-64 frozen dry run selected `torch 2.13.0+cpu`.
- `Qwen/Qwen3-Reranker-0.6B` loaded from the launcher's offline cache on `cuda:0` and a two-pair
  `CrossEncoder.predict()` smoke test completed. An isolated local Reranker server returned
  `200 {"ready":true}` from `/health/ready`.
- Backend gates passed: Ruff check; Ruff format check (167 files); mypy (98 source files); pytest
  (480 passed, 24 skipped, one existing Starlette deprecation warning).

## Current limits

CUDA availability still depends on a compatible NVIDIA driver and a complete offline model cache.
The provider logs an immediate diagnostic when the requested CUDA runtime is unavailable or model
loading fails; the launcher reports the corresponding readiness timeout instead of claiming that
all services started successfully.

# 2026-08-11 — Stage 15 v1.0 architecture reset and daily-use readiness

## 问题与约束

Java Symbol Retrieval 与目录导入把产品推向多语言代码智能，维护面已经超过个人学习 RAG
的 v1.0 价值。与此同时，RAG 在首次 Embedding、远程 LLM 和大文档 CPU 索引期间缺少足够早、
足够可信的反馈。此次收敛必须保留 Path Scope、Dense/BM25/RRF/Reranker 语义、Citation、
Knowledge 与 Knowledge Map，且不得修改固定检索评测资产或引入新的工作流框架。

## 采用方案

- 删除 Tree-sitter Java、Symbol parser/scope/query/direct-scroll、五个数据库字段、Qdrant
  symbol payload/index、前端 Symbol UI 和 active Symbol evaluation。Java 与其他代码统一进入
  Generic CodeParser；新的 `20260811_0010` 迁移线性删除旧字段。
- 删除 directory-based ingestion，只保留普通多文件上传。上传百分比来自 XHR transferred
  bytes；Parse/Index 使用真实状态与 elapsed time，不制造处理百分比。
- 增加保守的 full-string Direct/RAG router。Direct 只运行现有 streaming LLM；RAG 将耗时
  prepare 移入 SSE generator，并暴露不含 prompt、完整 retrieval query 或私有推理的 pipeline
  event 与稳定 timing。
- Query Embedding provider 改为 app-level 复用，并在 RAG 已配置时进行一次非阻塞后台模型
  预热。真实断流暴露 AnyIO level cancellation 会再次取消数据库终态事务，因此用 shielded
  terminal transaction 保证 assistant `cancelled` 状态落库。
- 主界面文案收敛为中文，统一管理页宽度，补齐 Knowledge Select 样式，并修复窄屏 Map 的
  空态和节点标签溢出。Evidence 仅以通用文档或 language/line-range code 信息呈现。

## 未采用方案

未保留 dormant Symbol compatibility、未扩展 Python/JavaScript AST、未恢复目录拓扑、未修改
Retrieval 阈值/排序/评测资产，也未增加 Agent、GraphRAG、Playwright 或新的 UI framework。
曾实测把 CPU indexing batch 从 2 提升到 16；同一 148-chunk 文档超过 29 分钟仍未完成，
而 batch 2 的完整结果为 1103.11 秒（此前一次为 1219.28 秒）。该优化被撤销，两个被取消的
冗余 generation 均由已存在的 active snapshot 恢复，且没有写入 Qdrant point。

## 验证与结果

- Backend reproducibility：`uv sync --frozen` 与 `uv pip check` 通过，100 packages compatible；
  Torch 仍为 `2.13.0+cu126`，CUDA 12.6、GTX 1650 可用。Reranker ready=200，最小两候选
  inference 排序正确，server latency 1136 ms。
- Backend gates：Ruff check、Ruff format（160 files）与 mypy（94 source files）通过；pytest
  收集 438 项，414 passed、24 skipped、0 failed（1 个 Starlette deprecation warning）。
  Disposable PostgreSQL 18 的 migration/constraint/集成集为 22 passed，Qdrant 隔离集成为
  2 passed；本机 schema 当前为 0010 head。
- Frontend gates：type-check、lint、Prettier 与 production build 通过；Vitest 为 18 files、
  85 tests、0 failed。生产产物 main JS 409.49 kB，Map lazy chunk 441.50 kB，无 chunk warning。
- Qdrant 重建后为 green、267 exact points；payload index 仅有 knowledge base、document、
  version、generation、language、chunk type 六类通用字段。13/13 原有文档均为 succeeded。
- 固定 Hybrid evaluation 的 before/after 质量完全一致：Hit@1 0.5909、Hit@5 0.9545、
  Recall@5 0.8182、MRR@5 0.7311、nDCG@5 0.6467、All-required@5 0.7727，17/22
  answerable cases passed。最终 warm P95 由 4648.09 ms 降至 2464.07 ms；但原 baseline
  regression gate 仍因 Recall 下降 0.0227 与 All-required 下降 0.0455 而 exit 1，Stage 15
  没有降低阈值或改变 baseline。
- Direct cold 总耗时 46068 ms，warm 三次中位 9890 ms、最大 15621 ms；首个 SSE event 为
  37–355 ms，所有 retrieval timing/candidate 均为 0。RAG warm retrieval 中位数：知识库主题
  5473 ms、PDF 总结 3271 ms、明确事实 9472 ms；跨资料 cold retrieval 14420 ms。远程 LLM
  流式总耗时出现超过本轮 10 分钟采样窗口的长尾，因此完整 4-case cold/warm total matrix
  未宣称通过。
- 真实 acceptance KB 逐个导入 Markdown、Python、PDF、DOCX，四种 parser 全部 succeeded，
  分别产生 9/5/1/2 chunks。worker 模型 warm 后单文档 index 为 3.47–30.75 秒；但一个
  148-chunk FIFO 任务曾使 upload-to-ready 等待约 19 分钟。Retry Parse 请求返回 202，随后
  Parse 与 Index 再次 succeeded。临时 KB、文档、Knowledge 与 Conversation 均已删除。
- 真实 completed answer 保存为 Knowledge 后得到 1 个 Evidence snapshot；Knowledge detail
  和 5-node/5-edge Map 在桌面/390 px 窄屏均可访问。真实首 token 后断流最终持久化为
  assistant `cancelled`；不存在 Conversation 返回安全 404。浏览器未发现应用级阻断错误，
  截图和临时验收资料未进入仓库。

## 遗留风险

- 固定 retrieval baseline gate 是真实红项，尽管 Stage 15 before/after 质量未变化。后续只能
  通过独立 Retrieval Experiment 修复，不得在本次架构收敛中修改阈值或评测资产。
- 当前配置使用远程 OpenAI-compatible LLM，RAG 会发送问题、必要历史和检索 Source；其
  TTFT/总耗时存在显著长尾。敏感资料必须切换本地 Provider 或先完成脱敏。
- Celery solo/FIFO 加上 CPU Qwen Embedding 会让大文档阻塞后续小文档。v1.0 已提供真实阶段
  和 elapsed feedback，但尚未解决队列调度与 CPU 吞吐；batch 16 已被实测否决。

# 2026-08-11 — v1.0 final stabilization

## Problem and constraints

The final release pass had three concrete daily-use risks: remote Qwen responses had highly
variable thinking latency, a CPU-only solo Celery worker allowed one large indexing task to starve
small documents, and saved Knowledge answers displayed Markdown as plain text. The fixed Retrieval
corpus, expected evidence, baseline, thresholds and production ranking algorithms remained frozen.

## Adopted design

- Added an optional provider capability switch for Qwen thinking. Empty configuration preserves the
  provider default; the local launcher explicitly disables thinking for predictable daily-use
  latency. The SSE completion metadata now separates local pre-LLM work, first token, generation,
  conversation persistence and total response time without exposing prompts or private reasoning.
- Kept one CPU embedding model process, but changed the local worker to a two-thread pool with
  prefetch one. The tracked Celery default also uses prefetch one, preventing a worker from reserving
  a backlog that other workers cannot receive. No queue, database or indexing algorithm changed.
- Rendered only the solution and immutable answer snapshot with markdown-it core. Raw HTML,
  linkification, typographer features and images are disabled. Lists, emphasis and fenced/inline code
  use the existing design tokens; no remote content is loaded.
- Documented that the Retrieval release gate requires a dedicated Qdrant collection. Qdrant BM25
  IDF statistics are collection-wide, so a payload-filtered evaluation document inside the normal
  user collection is not a reproducible baseline environment.

## Alternatives not adopted

Changing frozen retrieval thresholds or ranking behavior, increasing CPU embedding batch size,
creating multiple embedding model processes, adding new queues/databases, and introducing a broad
Markdown extension stack were rejected. A permanent global thinking default was also rejected;
providers that do not expose this capability retain their existing behavior.

## Measured results

- Direct mode before the provider switch ranged from 9.1 to 87.2 seconds in four valid Unicode
  samples. Afterwards, one cold sample completed in 6.38 seconds and three subsequent samples in
  2.07–2.23 seconds; retrieval remained zero and local pre-LLM work remained 2–4 ms warm.
- RAG afterwards completed in 14.03–23.55 seconds for two fact and two summary requests. Retrieval
  took 9.43–12.53 seconds, with reranking responsible for about 6.85–7.16 seconds. The timing split
  shows the next optimization target without changing candidate counts or retrieval quality here.
- With the previous solo worker, a 148-chunk document took about 1103 seconds and a later small
  document waited about 19 minutes. With threads=2 and prefetch=1, a 72-chunk document took 466.21
  seconds while a later 20-chunk document started indexing after 1.43 seconds and completed in
  117.73 seconds, about 5 minutes 47 seconds before the large document. Both versions succeeded and
  the temporary KB/documents were deleted; the normal Qdrant collection returned to exactly 267
  points.
- Running the frozen gate in the shared 267-point collection failed Recall@5 and All-required@5
  thresholds (0.8182 and 0.7727). The formal runner against the same current code and exact 72
  corpus points in an isolated temporary collection exited zero with Hit@1 0.5909, Hit@5 1.0000,
  Recall@5 0.8409, MRR@5 0.7424, nDCG@5 0.6623 and All-required@5 0.8182; every frozen regression
  threshold passed. The temporary backend/collection was deleted and no evaluation asset or
  retrieval implementation changed.

## Final validation and browser acceptance

- `uv sync --frozen` retained Torch 2.13.0+cu126 and CUDA 12.6 on the GTX 1650; `uv pip check`
  reported 100 compatible packages. Backend Ruff check/format (160 files) and mypy (94 source
  files) passed; pytest completed with 415 passed and 24 skipped. Disposable PostgreSQL 18
  migration/constraint integration tests were 22 passed, and isolated Qdrant tests were 2 passed.
- Frontend type-check, lint, Prettier and build passed; Vitest completed with 19 files and 87 tests
  passed. Lazy-loading Knowledge Detail keeps markdown-it out of the main bundle: main JavaScript is
  132.45 kB, Knowledge Detail 106.94 kB and Knowledge Map 441.53 kB, with no chunk-size warning.
- A real Edge walkthrough created a disposable KB from the UI and uploaded `architecture.md`
  (29 chunks) with the 72-chunk fixed corpus concurrently. Both started indexing within 18 ms; the
  smaller document became Ready after about 316 seconds and the larger after about 545 seconds.
  Direct completed in about 14 seconds with zero retrieval; a RAG fact request completed in 15.47
  seconds, returned the correct value 15 and displayed five source snapshots.
- Saving that answer produced a Knowledge detail with two rendered strong elements and four inline
  code elements. No script or remote image element was created. Documents, Knowledge and Map had no
  horizontal overflow at 1440 px or 390 px and produced no browser console exception. The exact
  temporary KnowledgeEntry, Conversation, two Documents and KB were deleted; Qdrant returned to 267
  points, and the temporary browser profile was removed.

## Remaining risks

Remote provider latency and output grounding remain externally variable. CPU reranking is now the
largest stable local RAG cost. The two-thread worker shares provider objects successfully in the
measured workload, but deployments with different models or memory limits should validate their own
concurrency before increasing it further.

# 2026-08-12 — v1.0 release packaging

## Problem and constraints

The repository README still described an early development state and did not present the completed
document-to-knowledge workflow. This pass is release packaging only: no runtime code, dependency,
migration, retrieval asset, evaluation baseline or product behavior may change. Local configuration,
credentials and `.claude/` remain outside version control.

## Adopted design

- Rewrote the README around the verified v1.0 product boundary, five capability groups, two Mermaid
  diagrams, the fixed Retrieval Evaluation results, public-repository startup commands, important
  configuration, non-goals and known limitations.
- Added user-facing v1.0.0 release notes summarizing highlights, architecture decisions, verified
  gates, limitations and upgrade guidance without reproducing the development history.
- Reserved four commented README image paths under `docs/images/` for Conversation, Documents,
  Knowledge and Knowledge Map. No screenshot or synthetic product image is committed; the project
  owner will capture and review the final public-demo images manually.

## Alternatives not adopted

Runtime version metadata, application code, dependencies and existing evaluation assets were not
changed because they are outside this documentation-only release commit. Browser screenshot
automation was also rejected for this pass after the owner chose manual capture.

## Validation and remaining work

Local Markdown links, Mermaid fence structure, Quick Start commands, tracked diff scope and secret /
absolute-path patterns were checked against the current repository. Before publishing, the owner
must add the four reviewed screenshots, align runtime/package image version metadata if desired,
    and promote the release branch through the agreed develop/main/tag/GitHub Release flow.

# 2026-08-14 — Stage 16 Verified Knowledge Retrieval Loop

## 问题与约束

Stage 13 已能把 completed answer 保存为结构化 KnowledgeEntry，但 RAG 仍只检索 Document
Chunk。沉淀经验无法自动参与后续问答，与“问题 → 方案 → 经验 → 复用”的产品闭环不一致。
实现必须继续使用 Provider 抽象、真实来源和确定性流程；不得把 assistant answer snapshot
直接当作事实，不得破坏现有文档检索、Path Scope 和 Citation Guard。

## 采用方案

- 为 KnowledgeEntry 增加独立索引状态、active/attempt generation、模型维度、Chunk 数和安全
  错误字段；迁移 `20260814_0011` 使存量条目保持 `not_indexed`，不隐式信任历史回答。
- 只索引 `verified` 条目的 maintained fields，明确排除 answer snapshot。采用现有确定性
  Chunker、Embedding Provider、Celery 和 Qdrant Collection，payload 使用真实
  `knowledge_entry_id` 与 `source_type=knowledge_entry`。
- RAG 在一次 Query Embedding 后，把数据库确认有效的文档代次与已验证知识代次合并到同一轮
  Dense/BM25/RRF；文档/路径或 language scope 不混入知识来源。
- Knowledge 来源继续使用 `[Sx]`，Evidence Inspector 明确显示“已验证知识”并链接详情。前端
  展示 `未进入检索/等待索引/正在索引/可检索/索引失败`，失败可显式重试。
- 用户内容更新时间与派生索引状态分离：Repository 在索引状态写入时显式保留 `updated_at`；
  内容编辑会立即使旧索引代次不再满足 active-generation 条件。

## 未采用方案

未采用同步索引、第二 Qdrant Collection、把 KnowledgeEntry 伪装成 Markdown Document、索引
完整 answer snapshot、Agent 或 GraphRAG。前四项分别会造成请求阻塞、重复向量化/跨库融合、
虚假来源身份和模型回答自我强化；后两项与当前确定性闭环无关。

## 验证方法与当前结果

- 新增 KnowledgeEntry 索引与统一 RAG 召回专项测试，覆盖 verified 索引、outdated 清理、
  answer snapshot 排除、文档/知识代次联合召回和知识 Prompt/Citation 契约。
- 迁移集成测试增加索引状态更新不改变 maintained `updated_at`、有效代次可查询的断言。
- 后端静态检查通过；420 项非集成测试通过。PostgreSQL 集成测试 23 项通过、2 项按配置
  跳过，独立 Qdrant 集成测试 3 项通过，其中包含 Migration 往返、索引时间语义和联合来源
  召回。
- 前端 type-check、lint、build 通过，19 个测试文件共 88 项测试分组运行通过。
- 24 Case 固定 Hybrid 回归在模型预热后通过正式质量阈值：Hit@5 1.0000、Recall@5
  0.8409、MRR@5 0.7424、nDCG@5 0.6623、All-required@5 0.8182。P95 为
  3549.68 ms，较历史 baseline 触发延迟警告；首次冷启动请求曾达到 60 秒超时。完整边界与
  风险记录在 `docs/experiments/knowledge-entry-retrieval-v1.md`。

## 遗留风险

- Qdrant 或队列在删除清理时长期不可用，可能留下不会被检索但占用空间的 orphan point；需要
  后续一致性审计/修复命令处理。
- 存量 KnowledgeEntry 不自动进入索引，必须经用户明确验证或重试，避免把历史生成内容静默
  升级为事实。
- 固定 synthetic retrieval corpus 不包含 KnowledgeEntry 来源；需要增加真实、可本地留存的
  问题解决案例评测，分别观察文档与知识来源的召回、引用支持率、延迟和成本。
