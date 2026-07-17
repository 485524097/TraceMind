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

`document_versions` 保存不可变版本元数据：正整数版本号、64 位 SHA-256、正数文件大小、可空 MIME、带点小写扩展名、POSIX 相对 `storage_path` 和带时区创建时间。

- `(document_id, version_number)` 唯一。
- Document 外键使用 ON DELETE CASCADE。
- 当前版本是最大 `version_number`，不增加循环 `current_version_id`。
- 数据库不保存 storage root、绝对路径、解析正文或索引状态。

## Migration

首条正式 migration：

```text
20260717_0001_create_knowledge_bases.py
```

`upgrade` 创建表、主键和唯一约束；`downgrade` 删除该表。Alembic `target_metadata` 指向统一 SQLAlchemy `Base.metadata`，后续模型必须显式导入到 models 包中。

第二条 migration `20260717_0002_create_documents.py` 创建 documents、document_versions、外键、唯一/检查约束和索引；downgrade 回到 `20260717_0001` 时只移除这两张表。

## 当前关系边界

KnowledgeBase 包含 Document 时禁止删除；Document 删除会在数据库级联删除 DocumentVersion。当前没有 Chunk、用户、标签、软删除或归档关系。

## CRUD API

- `POST /api/v1/knowledge-bases`
- `GET /api/v1/knowledge-bases`
- `GET /api/v1/knowledge-bases/{knowledge_base_id}`
- `PATCH /api/v1/knowledge-bases/{knowledge_base_id}`
- `DELETE /api/v1/knowledge-bases/{knowledge_base_id}`

Document API 详见 [文档导入说明](document-ingestion.md)。

列表使用 `created_at DESC, id DESC` 稳定排序。名称冲突返回 409，不存在返回 404，Schema 校验失败返回 422。
