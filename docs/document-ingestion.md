# Document 增量导入

## 模型关系

KnowledgeBase 拥有多个 Document，Document 拥有多个 DocumentVersion。删除 KnowledgeBase 不级联 Document；非空知识库返回 409。删除 Document 时 PostgreSQL 通过 ON DELETE CASCADE 删除版本元数据。

当前版本定义为同一 Document 下最大的 `version_number`，从 1 连续递增。

## 文件逻辑身份与增量结果

文件名先取 basename、Unicode NFC、去除首尾空格，再以 NFC+casefold 形成 `normalized_name`。同一知识库内该名称唯一：

1. 首次出现：创建 Document 和 Version 1，返回 `created` 与 HTTP 201。
2. 同名且当前 SHA-256 相同：不创建版本，返回 `unchanged` 与 HTTP 200。
3. 同名但 SHA-256 不同：保留历史并创建下一版本，返回 `version_created` 与 HTTP 201。

不同文件名即使内容相同也属于不同 Document。

新建 DocumentVersion 的初始解析状态为 pending。数据库提交完成后才尝试投递 Celery；`created` 和 `version_created` 会自动入队，`unchanged` 仅在当前版本为 pending 或 failed 时重试入队。响应使用 `parsing_queued` 明确本次是否成功投递。

Redis/Celery 暂时不可用不会回滚已完成的导入：文件与版本保留，状态保持 pending，`parsing_queued=false`，用户可从页面手动重试。

## 本地文件结构

```text
<storage_root>/
  .upload-tmp/
  .trash/<operation_uuid>/
  <knowledge_base_id>/<document_id>/<document_version_id>/content.<extension>
```

数据库只保存 `<knowledge_base_id>/<document_id>/<version_id>/content.md` 形式的 POSIX 相对路径。读取时 resolve 后必须仍在 root 内；用户文件名不会参与物理路径。

本地默认 root 为 `../data/uploads`（从 backend 启动时对应仓库 `data/uploads`）；Docker 内为 `/app/data/uploads`，backend 与 celery-worker 挂载同一宿主目录。

## 类型、大小和文件名限制

默认上限 50 MiB，1 MiB 分块。允许 md、txt、pdf、docx、Java/JSP、JavaScript/TypeScript/Vue、SQL、XML、JSON、YAML、properties 和 Python。扩展名小写判断；MIME 只作为可空元数据，不作为信任依据。

空文件、空名、`.`、`..`、NUL、超过 255 字符和不允许的扩展名会被拒绝。上传以分块方式写入 root 内临时文件并增量计算 SHA-256，不把整个文件读入单个 bytes。

## 数据库与文件一致性

上传先创建/flush 元数据，再将临时文件原子移动到服务端 UUID 正式目录，最后 commit。数据库失败会 rollback 并删除本次临时或正式文件；唯一约束覆盖并发文档创建和版本号竞争。

删除先把 Document 目录原子移动到 `.trash/<operation_uuid>`，再删除数据库记录。commit 失败恢复目录；成功后递归清理。最终清理失败只记录 warning 并保留 trash 供人工处理，不恢复已经成功的数据库删除。

## API

- `POST /api/v1/knowledge-bases/{knowledge_base_id}/documents`，multipart 字段 `file`
- `GET /api/v1/knowledge-bases/{knowledge_base_id}/documents`
- `GET /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}`
- `GET /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/versions`
- `GET /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/download`
- `GET /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/versions/{version_id}/download`
- `DELETE /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}`

列表支持 offset、limit 和不区分大小写的名称 query。响应不包含 storage_path 或服务器绝对路径。

## 测试

默认 `uv run pytest -m "not integration"` 不需要 Docker；存储测试使用 `tmp_path`。设置指向 `_test` 数据库的 `TEST_DATABASE_URL` 后运行 `uv run pytest -m integration`。完整 migration 往返和跨平台验证命令见 [开发指南](development.md)。

## 当前边界

“文件已导入”不代表“文件已解析”，“文件已解析”也不代表“已经建立检索索引”。解析与 Chunking 见 [文档解析说明](document-parsing.md)；当前没有 Embedding、BM25、Qdrant 写入或 RAG。
