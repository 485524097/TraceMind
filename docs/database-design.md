# TraceMind 数据库设计

## KnowledgeBase

`knowledge_bases` 表示一个独立的知识资料边界。

| 字段 | PostgreSQL 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | `uuid` | 主键、非空 | 应用使用 UUID4 生成 |
| `name` | `varchar(100)` | 非空、唯一 | Schema 去除首尾空格后校验 |
| `description` | `text` | 可空 | 用户可显式清空 |
| `created_at` | `timestamp with time zone` | 非空、默认当前时间 | 统一使用 UTC 时间语义 |
| `updated_at` | `timestamp with time zone` | 非空、默认当前时间 | ORM 修改时自动更新 |

唯一约束名称为 `uq_knowledge_bases_name`。Service 会提前检查名称冲突，同时捕获数据库唯一约束异常，以覆盖并发写入场景。

## Document

`documents` 以 `(knowledge_base_id, normalized_name)` 唯一标识同一知识库内的逻辑文件。字段包括 UUID、知识库外键、展示名称、NFC+casefold 名称、固定 `upload` 来源和带时区时间。外键 `fk_documents_knowledge_base_id` 使用 RESTRICT，不自动级联删除。

列表按 `created_at DESC, id DESC`；Repository 通过聚合子查询一次取得最新版本和版本总数。

## DocumentVersion

`document_versions` 保存不可变文件版本元数据，并保存该版本独立的解析与 Dense 索引状态、安全错误摘要及 active generation。

- `(document_id, version_number)` 唯一。
- Document 外键使用 ON DELETE CASCADE。
- 当前版本是最大 `version_number`，不增加循环 `current_version_id`。
- 数据库不保存 storage root、绝对路径、解析正文或向量值。

解析状态为 `pending`、`processing`、`succeeded` 或 `failed`。`chunk_count >= 0`；`processing` 使用 `parse_started_at` 支持超时接管，`succeeded` 的 Chunk 可预览但尚不代表已建立检索索引。

索引状态同样为 `pending`、`processing`、`succeeded` 或 `failed`。`active_index_generation` 在 processing 时是 attempt token，在 succeeded 时是检索必须使用的 active generation。另存索引起止时间、尝试时间、Chunk 数、Embedding 模型和维度以及安全错误摘要；`indexed_chunk_count >= 0`，Embedding 维度为空或为正数。

## DocumentChunk

`document_chunks` 通过 `document_version_id` 归属于单一版本并使用 ON DELETE CASCADE。`(document_version_id, chunk_index)` 唯一，索引从 0 连续递增。正文同时保存 SHA-256 与 Python 字符数；可选来源字段包括 1-based PDF 页码、成对的 1-based 起止行号、章节标题和语言。

数据库约束检查非负索引、正字符数、64 位哈希、正页码/行号、行号成对出现及结束行不早于开始行。正文非空白与连续索引由 Parsing Service 在写入前保证。表中不包含 embedding、Qdrant point、BM25 或检索分数。

## Migration

首条正式 migration：

```text
20260717_0001_create_knowledge_bases.py
```

`upgrade` 创建表、主键和唯一约束；`downgrade` 删除该表。Alembic `target_metadata` 指向统一 SQLAlchemy `Base.metadata`，后续模型必须显式导入到 models 包中。

第二条 migration `20260717_0002_create_documents.py` 创建 documents、document_versions、外键、唯一/检查约束和索引；downgrade 回到 `20260717_0001` 时只移除这两张表。

第三条 migration `20260717_0003_create_document_chunks.py` 扩展 document_versions 的解析字段并创建 document_chunks；downgrade 到 `20260717_0002` 只移除解析字段和 Chunk 表，不触发文件解析或 Celery。

第四条 migration `20260720_0004_add_document_vector_indexing.py` 增加 DocumentVersion 的 Dense 索引状态、generation、模型元数据和约束；不在 PostgreSQL 保存向量。

## 当前关系边界

KnowledgeBase 包含 Document 时禁止删除；Document 删除会级联删除 DocumentVersion，版本删除继续级联删除 DocumentChunk。当前没有用户、标签、软删除、归档或检索索引关系。

## CRUD API

- `POST /api/v1/knowledge-bases`
- `GET /api/v1/knowledge-bases`
- `GET /api/v1/knowledge-bases/{knowledge_base_id}`
- `PATCH /api/v1/knowledge-bases/{knowledge_base_id}`
- `DELETE /api/v1/knowledge-bases/{knowledge_base_id}`

Document API 详见 [文档导入说明](document-ingestion.md)。

列表使用 `created_at DESC, id DESC` 稳定排序。名称冲突返回 409，不存在返回 404，Schema 校验失败返回 422。
