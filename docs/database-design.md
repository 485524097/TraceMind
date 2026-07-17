# TraceMind 数据库设计

## KnowledgeBase

`knowledge_bases` 是当前唯一的业务表，用于表示一个独立的知识资料边界。

| 字段 | PostgreSQL 类型 | 约束 | 说明 |
| --- | --- | --- | --- |
| `id` | `uuid` | 主键、非空 | 应用使用 UUID4 生成 |
| `name` | `varchar(100)` | 非空、唯一 | Schema 去除首尾空格后校验 |
| `description` | `text` | 可空 | 用户可显式清空 |
| `created_at` | `timestamp with time zone` | 非空、默认当前时间 | 统一使用 UTC 时间语义 |
| `updated_at` | `timestamp with time zone` | 非空、默认当前时间 | ORM 修改时自动更新 |

唯一约束名称为 `uq_knowledge_bases_name`。Service 会提前检查名称冲突，同时捕获数据库唯一约束异常，以覆盖并发写入场景。

## Migration

首条正式 migration：

```text
20260717_0001_create_knowledge_bases.py
```

`upgrade` 创建表、主键和唯一约束；`downgrade` 删除该表。Alembic `target_metadata` 指向统一 SQLAlchemy `Base.metadata`，后续模型必须显式导入到 models 包中。

## 当前关系边界

KnowledgeBase 当前没有 Document、Chunk、用户、标签或模型配置关系，因此删除时允许直接删除。没有实现软删除、归档或级联业务规则。

## CRUD API

- `POST /api/v1/knowledge-bases`
- `GET /api/v1/knowledge-bases`
- `GET /api/v1/knowledge-bases/{knowledge_base_id}`
- `PATCH /api/v1/knowledge-bases/{knowledge_base_id}`
- `DELETE /api/v1/knowledge-bases/{knowledge_base_id}`

列表使用 `created_at DESC, id DESC` 稳定排序。名称冲突返回 409，不存在返回 404，Schema 校验失败返回 422。
