# Stage 17A 本地数据持久性：Knowledge Base Archive v1

## 状态

- **设计**：已采用。
- **第一阶段（Export）**：已实现并通过当前后端门禁。
- **第二阶段（Source of Truth Restore）**：已实现并通过专项与 PostgreSQL 往返验证。
- **第三阶段（Parse / Index Rebuild）**：已实现，并通过 PostgreSQL、Qdrant 与固定 Retrieval 回归验证。
- **数据库迁移**：Archive/Restore 本身不改变业务实体；Rebuild 为持久化 operation/item 状态新增
  `20260814_0012`，支持进程重启后的查询、重试和 worker lease 接管。

## 问题与约束

TraceMind 的长期业务数据分布在 PostgreSQL 和本地原文件目录，DocumentChunk、Qdrant、Redis
与 Celery 状态则是可重建派生状态。只复制数据库或 uploads 目录都会得到不完整且无法验证的
备份。Stage 17A 需要提供单个 Knowledge Base 的可移植归档，同时保持 UUID、业务时间、真实
Evidence 和生成快照，不把运行时索引状态伪装成可恢复事实。

约束包括：本地优先、单机 MVP、最小修改；不实现 Merge、Import as Copy、增量备份、云同步、
加密归档、Agent 或通用 audit/repair；不归档 secret、`.env`、日志、模型缓存或 `.claude/`。

## Archive v1 实际结构

文件名为 `<safe-name>.tracemind.zip`。ZIP entry 使用 POSIX 分隔符，全部由 TraceMind 根据固定
目录、UUID 和受验证的小写 extension 生成：

```text
manifest.json
data/
  knowledge_base.json
  documents.jsonl
  document_versions.jsonl
  conversations.jsonl
  messages.jsonl
  knowledge_entries.jsonl
files/
  document_versions/
    <document-version-uuid>/
      content<extension>
```

`manifest.json` 固定包含：

- `archive_format = "tracemind.knowledge-base"`
- `archive_version = 1`
- `archive_id`、`tracemind_version`、`exported_at`
- Knowledge Base summary 和各实体数量
- 每个 `data/*` entry 的 path、未压缩 size、SHA-256 和 record count
- 每个原文件的 DocumentVersion UUID、path、size 和 SHA-256

v1 写入时使用 ZIP `stored` 方法。PDF/DOCX 等原文件通常已经压缩；不二次压缩可以让本系统
生成的归档天然满足 compression-ratio 安全规则，避免高重复文本被误判为 zip bomb。格式仍是
标准 ZIP，Restore 读取器后续只会接受明确 allowlist 中的方法。

## Export 数据字段

归档字段是显式 allowlist，不直接序列化 ORM 的全部列：

- KnowledgeBase：`id`、`name`、`description`、`created_at`、`updated_at`。
- Document：`id`、`knowledge_base_id`、`name`、`relative_path`、`source_type` 和业务时间。
  `normalized_name`、`normalized_path` 在 Restore 时由当前规范重新生成。
- DocumentVersion：`id`、`document_id`、`version_number`、`content_hash`、`file_size`、
  `mime_type`、`extension`、`created_at`；`storage_path` 和全部 parse/index runtime 字段排除。
- Conversation：全部长期业务字段。
- ConversationMessage：角色、状态、内容、trace、`sources` Evidence snapshot、
  `generation_metadata` 和创建时间。
- KnowledgeEntry：maintained fields、validation status、tags、provenance 外键、question/answer/
  sources/generation metadata snapshots 和业务时间；Stage 16 索引字段全部排除。

DocumentChunk、Qdrant vector/BM25、Redis/Celery 状态均不在归档中。Restore 后 DocumentChunk
由确定性 Parser/Chunker 重建；所有版本 Parse，但只有每个 Document 最新版本进入 Qdrant。
verified KnowledgeEntry 只用 maintained fields 重建索引，answer snapshot 不参与索引。以上
Source of Truth Restore 已实现；Parse/Index 和 KnowledgeEntry Rebuild 尚未实现。

## Export 一致性和清理

1. 在独立 `REPEATABLE READ` 事务中读取一个 Knowledge Base 的一致性快照。
2. Repository 对 source-of-truth 行使用共享锁，并按稳定顺序读取。
3. 在事务内把每个不可变原文件流式复制到
   `<document_storage_root>/.archive-tmp/staging/<random-operation>`，复制时重新计算 size/SHA-256，
   必须与数据库元数据完全一致。
4. 从显式 schema 生成 JSON/JSONL 和 manifest，在服务端完成整个临时 ZIP。
5. ZIP 成功后结束只读数据库事务，再把文件交给 `FileResponse`；用户下载期间不持有事务。
6. 响应完成后删除 ZIP；任何数据库、文件、校验或写入失败都会清理本次 staging/ZIP。

并发删除若先移动了原文件，Export 会安全失败而不是生成缺文件的归档；若复制已经完成，归档
使用已校验的稳定副本。第一阶段不改变普通导入或删除行为。

## 安全限制

当前默认配置：

- archive upload：1,207,959,552 bytes
- 单个 extracted file：104,857,600 bytes
- total extracted：1,073,741,824 bytes
- ZIP entries：20,000
- 单个 JSON/JSONL：67,108,864 bytes
- 单个 JSONL records：100,000
- compression ratio：100
- 流式 I/O chunk：1,048,576 bytes

Export writer 拒绝 absolute path、`..`、`.`/empty segment、反斜杠、Windows drive、NUL、重复
entry、非法 extension、symlink、special/missing source、超限 size/count，以及与数据库不一致的
原文件。ZIP entry 显式标记为普通文件。Restore reader 已实现 central directory、encrypted/
special entry、compression allowlist、未声明/缺失 entry、duplicate JSON key、严格 Pydantic、
UTF-8、checksum 和 resolve containment 校验；全程不调用 `extractall()`，也不访问 URL。

## Source of Truth Restore 事务

`POST /api/v1/knowledge-base-archives/restore` 使用以下实际顺序：

1. 流式保存上传到 `.archive-tmp/uploads`，限制 archive size。
2. 完整验证 ZIP structure、manifest、allowlist、size/count/ratio、JSON/JSONL、SHA-256、实体数量、
   DocumentVersion content hash、引用图及 Archive 内部 UUID/path/source 唯一性。
3. 只读查询数据库中 KB UUID/name、各实体 UUID、normalized path 和 Knowledge source assistant
   冲突；结束预检事务。任何冲突整包返回 409，不 remap、merge、replace 或自动重命名。
4. 按当前 `normalize_document_path()` 计算 normalized fields，按
   `LocalFileStorage.final_relative_path()` 计算目标 storage path；Archive path 只用于选择 ZIP
   entry，不能决定磁盘 destination。
5. 把全部原文件再次校验并写入 `.restore-tmp/<operation-id>/<knowledge-base-id>/...`，创建受控
   operation marker 和 filesystem journal。
6. 开启一个数据库事务，显式按 KnowledgeBase → Document → DocumentVersion → Conversation →
   Message → KnowledgeEntry 的外键顺序逐层 `flush()`。
7. 在 commit 前把完整 KB staging directory 原子 rename 到最终 UUID 目录，并将 journal 标记为
   promoted；随后 commit 数据库。
8. commit 成功后移除 marker、journal、operation/upload 临时状态，返回
   `restore_status=succeeded`、`rebuild_status=not_started`。

DocumentVersion 的 parse/index 字段全部重置为 pending/空值/零；KnowledgeEntry verified 设为
pending，unverified/outdated 设为 not_indexed。DocumentChunk 不插入，任何 Celery/Qdrant/模型
调用都不在第二阶段路径中。

## Restore journal 与失败补偿

Journal 只记录 journal version、operation/KB UUID、受控 staging/final 相对路径、promotion 状态、
创建时间及本次文件 path/size/SHA-256，不承载通用任务状态。staging/final 中的 operation marker
必须与 journal UUID 匹配，补偿逻辑才允许删除最终 KB 目录，防止合法或伪造 journal 影响普通
Document Storage。

启动时只扫描 `.restore-tmp/journals/*.json`：数据库不存在 KB 时，清理该 operation staging，
并且只在 marker 匹配时清理 final；数据库存在且 journal 声明的 final 文件全部通过 size/hash
校验时，只清理 marker/journal。无效 journal 或 DB 已存在但 final 不完整的情况不会删除普通数据，
会保留现场供人工处理。

普通 validation、preflight、staging、flush、promotion 或 commit 异常都会 rollback，并清理本次
staging、由匹配 marker 证明归本 operation 所有的 final、upload 和 journal。commit 已成功但
journal 清理失败时不撤销 Source of Truth，journal 留待 startup recovery 收口。

## 未采用方案

- 直接打包数据库 dump：粒度过大，且不能表达与文件、派生索引之间的恢复契约。
- 循环调用 `DocumentService.import_document`：会改 UUID，也不具备整包原子事务语义。
- 归档 DocumentChunk/Qdrant：体积大、耦合当前 parser/model/index generation，且会恢复陈旧派生状态。
- 在 HTTP 下载期间保持事务：慢客户端会长期占用数据库快照和锁。
- 在内存中构建整个 ZIP：大知识库会造成不可控内存占用。

## 验证与遗留风险

Export/Restore 专项测试覆盖安全路径、symlink/device/encrypted entry、size/hash、重复 entry、
compression ratio、严格 JSON、临时文件清理、共享锁查询、冲突预检、外键顺序、
字段 allowlist、历史版本原文件、Evidence/metadata/provenance snapshot、运行态排除、manifest
checksum、staging/promotion/flush/commit 失败补偿、journal recovery 和 API 错误映射。完整门禁结果记录在
`docs/development-log.md`。

第三阶段已经补齐以下 Derived State Rebuild 契约。

## Derived State Rebuild

`POST /api/v1/knowledge-bases/{knowledge_base_id}/rebuild` 创建一个 Knowledge Base 级持久化
operation；`GET` 返回当前状态；`POST .../rebuild/retry` 在同一个 operation 上只重新排队失败或
未完成 item。未创建 operation 时，GET 合成 `not_started`，不把 Restore 成功误报为可检索。

operation 状态为 `queued`、`running`、`succeeded`、`partially_failed` 或 `failed`。item 按
`document_parse`、`document_index`、`knowledge_entry_index` 分组，并独立记录 attempt、开始/完成时间
和安全错误。数据库 partial unique index 保证每个 Knowledge Base 同时最多一个 queued/running
operation；`run_generation` 是数据库 worker lease，而 Celery task id 只负责投递，不是真相来源。
heartbeat 超过配置阈值后，新的执行者可以原子接管 stale lease。

operation 在创建时冻结目标快照：每个 DocumentVersion 都建立 parse item；每个 Document 只有
`version_number` 最大的版本建立 index item；只有当前 `verified` KnowledgeEntry 建立 index item。
执行顺序固定为全部版本 Parse、最新版本 Document Index、verified KnowledgeEntry Index。历史版本
调用正式 Parser/Chunker 但禁止自动派发索引，因此保留 DocumentChunk 供追溯而不会成为 active
retrieval source。KnowledgeEntry 只使用 maintained fields，继续排除 answer snapshot；unverified 和
outdated 不进入 rebuild target。

Rebuild 复用 `DocumentParsingService`、`DocumentIndexingService`、
`KnowledgeEntryIndexingService` 与既有 Provider/Qdrant gateway，没有第二套解析、向量或检索实现。
Document/Knowledge 的 generation 与 point id 契约保持不变，Qdrant upsert 可安全重放；数据库 active
generation 和 Retrieval generation filter 共同保证只有最新成功代次可检索。历史 orphan point 即使
存在也不会被 active 查询命中，但空间审计与批量清理仍属于 Stage 17B。

queue dispatch 失败会把 operation 持久化为 `failed`，不会回滚或删除 Restore 后的 Source of Truth。
执行中部分 item 失败时，已有成功结果保留并返回 `partially_failed`；retry 跳过 succeeded item，只处理
pending/failed item。所有 item 成功才进入 `succeeded`。目标快照建立后并发新增的业务版本继续走正常
导入/索引流程，不动态并入当前 operation，避免一个 rebuild 永远追赶变化中的目标集合。

真实一次性 PostgreSQL + Qdrant 验证覆盖全部历史版本 Parse、每个 Document 仅 latest active、verified
KnowledgeEntry active、answer snapshot/unverified/outdated 排除、重复 upsert point count 不增加，以及
Export → 删除 DB/Storage/Derived → Restore（`not_started`）→ Rebuild → RAG 同时召回正确 Document 与
KnowledgeEntry。固定 24 Case Hybrid 回归保持 Hit@5 1.0000、Recall@5 0.8409、MRR@5 0.7424、
nDCG@5 0.6623、All-required@5 0.8182，质量阈值通过；P95 8291.81 ms，相对基线产生延迟 warning。

本机真实 Celery worker 能接收任务并完成 Parse，但在 worker 进程内加载
`Qwen/Qwen3-Embedding-0.6B` 长时间无进展；同一模型在前台进程可正常完成。因此完整 72 Chunk 往返
改为通过正式 Celery task 函数前台执行，未替换 Service、Provider 或 Qdrant。分布式 worker 的模型
加载兼容性仍是部署环境待验证项，不应被记录为已通过。

# Stage 17B-1 Consistency Audit

## Stage 17B-1 不修改 Source of Truth 或 Derived State

Consistency Audit 读取 PostgreSQL 元数据、DocumentVersion 原文件、DocumentChunk count、Qdrant
payload metadata 和 Restore recovery metadata。它不调用 Parse/Index dispatcher，不创建 Qdrant
collection，不删除 point，不执行 restore recovery，也不修改 Document、DocumentVersion、Chunk、
KnowledgeEntry、Source 文件或 runtime indexing state。公开 Audit 会持久化最小 Audit Snapshot/Finding
operational metadata，供显式 Repair 证明选择来源；因此它不是严格的数据库只读操作。Audit 使用同步 API，
不复用 Rebuild Operation，也不新增通用 Workflow Engine：

- `POST /api/v1/knowledge-bases/{knowledge_base_id}/consistency-audit`
- `POST /api/v1/consistency-audit`

Storage 构造器保留默认 `create_roots=True` 兼容现有流程；Audit 显式传入 `False`，避免仅构造审计依赖
就创建目录。公开报告包含随机 `audit_id`、scope、completed/partial、开始/完成时间、严格 summary 和
findings，并仅持久化不含正文、answer 或 secret 的 operational metadata；Repair 内部 revalidation 使用
不持久化的 inspection 路径。

## Consistency model 与 Source of Truth hierarchy

优先级固定为：PostgreSQL 长期业务实体与 DocumentVersion 原文件是 Source of Truth；DocumentChunk、
parse/index runtime、generation、Dense/BM25 和 Qdrant point 是 Derived State；Restore journal 是
recovery metadata。Qdrant payload 永远不能反向改变 PostgreSQL 事实。

Document active generation 完全复用正式 Retrieval 语义：只考虑每个 Document 最大
`version_number`，且 index status 为 succeeded/processing、active generation/parsed_at/indexed_at
存在，并满足 indexed_at 不早于 parsed_at。Document indexing 当前索引全部持久化 Chunk，所以 point
expected count 使用实际 PostgreSQL Chunk count，同时核对 `indexed_chunk_count`，不把 Retrieval 时
排除 heading 的查询规则误当成索引排除规则。

KnowledgeEntry active generation 必须同时满足 verified、succeeded/processing、active generation 与
indexed_at 存在，并且 `indexed_source_updated_at == updated_at`。Expected point count 直接复用 Stage 16
的 `build_knowledge_blocks()` 与 DeterministicChunker，只读取 maintained fields，明确不读取或使用
answer snapshot。

## Finding taxonomy 与 Severity

Finding code 是稳定 machine-readable code；message 是固定安全文本；details 只允许 UUID、状态、计数、
hash、规范 storage path 等技术元数据，不包含文档内容、answer、secret 或环境变量。

- `INFO`：观察信息，当前实现不为无异常对象制造噪声 finding。
- `WARNING`：stale/orphan derived data、非法 recovery metadata 或子系统不可用；不证明 Source of Truth
  已损坏。
- `ERROR`：active Retrieval、Chunk metadata 或 journal recovery state 不完整，可能影响 RAG。
- `CRITICAL`：原文件缺失、非普通文件、路径、size 或 SHA-256 与 PostgreSQL 不一致，Source of Truth
  已损坏。

主要 code 包括：`document_file_missing`、`document_file_not_regular`、
`document_file_size_mismatch`、`document_file_hash_mismatch`、`document_storage_path_invalid`、
`parsed_version_missing_chunks`、`chunk_count_mismatch`、`unexpected_chunks`、
`latest_index_generation_missing`、`active_index_points_missing`、
`active_index_point_count_mismatch`、`historical_generation_active`、
`verified_knowledge_index_missing`、`knowledge_index_point_count_mismatch`、
`non_verified_knowledge_active`、`orphan_qdrant_point`、`stale_qdrant_generation`、
`stale_knowledge_generation`、`invalid_qdrant_payload`、`restore_journal_cleanup_pending`、
`restore_journal_inconsistent`、`restore_journal_invalid` 和 `suspicious_storage_entry`。

## Storage、Qdrant、Journal 与 Cross-KB 规则

Storage 先按 `LocalFileStorage.final_relative_path()` 重算规范路径，再检查 symlink/special/missing、size，
最后按配置 I/O chunk 流式计算 SHA-256；从不把文件整体读入内存。全局 scan 只枚举 storage root 的
直接 entry，已知 KB UUID 与受控临时目录被排除，未知 entry 仅报告不删除。

Qdrant Gateway 新增唯一的 audit/read 方法，使用官方分页 `scroll`，固定 `with_payload=True`、
`with_vectors=False`，page size 由 `CONSISTENCY_AUDIT_QDRANT_PAGE_SIZE` 控制。payload UUID、source type
及实体归属与数据库 snapshot 对照；current active 和 processing attempt generation 被识别为合法，其他
有效实体 generation 归类 stale，缺失实体归类 orphan，缺字段/非法 UUID 归类 invalid。Audit 不调用
`ensure_collection()`，因此 Qdrant 不可用或 collection 不存在只产生 `qdrant_audit_unavailable`，报告
状态为 partial，已完成的 DB/Storage findings 仍返回。

Restore journal audit 抽取并复用 startup recovery 的同一严格 journal reader/path validation，再调用
现有 `final_restore_is_complete()`；DB 不存在或 final 已完整表示 cleanup pending，DB 存在但 final 不
完整表示 inconsistent，非法 journal 和无可信 journal 的 staging residue 分别报告。全局 scan 同时覆盖
所有 KB、未知 KB Qdrant payload、journal residue 与 suspicious storage entry。

Audit 对 Source of Truth 和 Derived State 是跨 PostgreSQL、文件系统和 Qdrant 的无锁只读观察，不暂停正常
导入/索引；它只写上述审计 operational metadata。并发 Parse/Index 可能造成短暂 finding；遇到正在变化的
对象应在操作完成后重跑 Audit。Stage 17B-1 不执行任何 Repair，即使 orphan 已被完全确认也不会删除。
# Stage 17B-2 Safe Derived State Repair

## Safe Repair Boundary

Repair 只接受某次已持久化 Audit 的 `audit_id`、单一 `knowledge_base_id` 和显式
`finding_ids`。Stage 17B-2 为此新增最小 Audit Snapshot：只保存 audit 时间、scope、status 与 finding
的 code、severity、entity identity、KB identity、固定安全消息和机器可读技术元数据；不保存文档正文、
Chunk content、answer snapshot、secret 或通用事件。该 operational metadata 写入不改变业务 Source of Truth；
它取代 Stage 17B-1“报告完全不持久化”的临时结论，以便 Repair 能证明调用方选择的是哪一次 finding。

API 不提供 repair-all。`dry_run=true` 是默认值，且只读取 snapshot 和当前事实：不创建 repair operation、
不 commit、不 enqueue、不 upsert/delete Qdrant，也不 rename/unlink 文件。只有显式 `dry_run=false` 才建立
最小 Repair Operation/Item 并交给 Celery；Parse/Embedding/Qdrant 工作不阻塞 HTTP 请求。
崩溃或失败后的显式恢复使用
`POST /knowledge-bases/{knowledge_base_id}/consistency-repair/{operation_id}/retry`；非 stale 的 active
operation 返回 409，stale operation 旋转 generation、重置未完成 item 并重新 enqueue。

## Repair Allowlist 与 non-repairable findings

显式 allowlist 仅包含：`parsed_version_missing_chunks`、`chunk_count_mismatch`、
`latest_index_generation_missing`、`active_index_points_missing`、
`active_index_point_count_mismatch`、`verified_knowledge_index_missing`、
`knowledge_index_point_count_mismatch`、`stale_qdrant_generation`、`orphan_qdrant_point` 和
`restore_journal_cleanup_pending`。每个 code 在 executor 中有独立 handler 映射，不使用 prefix、通配符或未知
code fallback repair。

文件 missing/size/hash/path/not-regular、journal inconsistent/invalid、suspicious storage、invalid payload 及未知
code 一律 `not_repairable`。系统不会创建空文件、覆盖原文件、修改 hash、移动未知文件、删除未知目录，也不会
从 Qdrant 反向修改 PostgreSQL；调用方必须人工处理 Source of Truth 或执行受验证 Restore。

## Revalidation、idempotence 与 post-repair verification

每个 item 执行前重新运行不持久化的单 KB Audit，并以 code、entity identity 和 generation（若存在）重新匹配。
finding 已消失时返回 `skipped/finding_no_longer_present`，不重复 Parse、Index 或 delete。因此重复提交同一修复不会
增加 Chunk/point，也不会把“point 已不存在”当作 fatal。Document index 还会重新确认目标仍是最大
`version_number`；正式 Document Index pipeline 在 claim 和 final activation 两个数据库锁边界都校验 latest，
供普通 ingestion、Rebuild 和 Repair 共用。KnowledgeEntry 必须仍为 verified；Source 文件出现 CRITICAL
finding 或无法完成完整性检查时，同时禁止 Parse 和 Document Index repair。

handler 返回成功后必须再次运行 targeted Audit。原 finding 仍存在时 item 记为 `verification_failed`，不能伪装为
`succeeded`。多 item 使用逐项 commit：A 成功、B 失败、C skipped 时保留 A，不以 KB 大事务回滚已恢复的
Derived State。

## Document、KnowledgeEntry、Qdrant 与 Journal safety

Chunk repair 只调用 `DocumentParsingService.parse_version(force=True, enqueue_index=False)`；Parse 与 Index 是独立
item。Document index 只调用正式 `DocumentIndexingService` 且只作用 latest version；KnowledgeEntry 只调用正式
index service，并继续使用 maintained fields，绝不使用 raw answer snapshot。

stale generation 删除前重新确认 generation 既非 active 也非 processing attempt，并逐页读取该 generation 的
metadata-only payload，要求每个 point 都属于预期 KB/document/version/generation，之后才复用
`delete_generation()`。orphan cleanup 先按精确 UUID retrieve，逐字段确认 KB/source/entity/generation 与最新 Audit
一致，再通过新增的最小 `delete_points(ids)` 仅删除已确认 ID；任何歧义都不删除。

Journal cleanup 复用 Restore 的严格 reader、marker/path validation、`final_restore_is_complete()`、
`finish_recovered_restore()` 和 `recover_absent_database_restore()`。Repair 不实现第二套目录删除逻辑；
`restore_journal_inconsistent` 始终留给人工处理。

## Stage 17 Seal：并发与崩溃恢复不变量

Restore 以 Knowledge Base UUID 稳定派生 PostgreSQL bigint advisory-lock key，不使用进程相关的
`hash()`。active Restore 使用专用 PostgreSQL connection 持有 session-level lock，覆盖 staging/journal、
filesystem promote、数据库 commit、提交结果确认及失败补偿；startup recovery 和 Repair journal cleanup
只能在 `pg_try_advisory_lock` 成功后判断并清理。连接或进程死亡时 PostgreSQL 自动释放 lock，下一次启动可
继续严格 journal/marker recovery。

Repair Operation 复用 Rebuild 的轻量 lease 模式：`run_generation`、`heartbeat_at`、stale takeover 和所有
item/operation terminal write 的 generation fencing。PostgreSQL partial unique index 保证同一 KB 最多一个
queued/running Repair，不同 KB 仍可并发。Repair Item 对 Finding 使用 `ON DELETE CASCADE`，保证删除 KB 时
Audit/Repair operational graph 构成完整删除闭包。

## Known Follow-up（不属于本轮 Seal Fix）

- stale Qdrant generation 验证当前会累计该 generation 的 metadata point，后续改为流式/分批验证。
- 大 KB Export 后续改为流式 JSONL、文件复制和增量 hash，缩短一致性事务并限制内存。
- Audit Snapshot 后续增加按时间或数量的保留策略。
