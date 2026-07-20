# Document 解析与 Chunking

## 架构与数据流

解析模块位于 `backend/app/parsing`。DocumentParser 只把本地文件转换为 ParsedDocument，不访问数据库、HTTP、Celery 或向量服务。ParserRegistry 按扩展名选择实现，DeterministicChunker 再把 ParsedBlock 转为 ChunkDraft。

上传事务提交后，DocumentParsingDispatcher 投递 DocumentVersion UUID。Celery Task 使用 `asyncio.run`，为当前任务创建独立 AsyncEngine、async_sessionmaker 和 Session，结束时始终 dispose Engine。Worker 读取安全相对 storage_path，解析、切分，并由 Service 在事务中写入 DocumentChunk。

## 支持格式与边界

| Parser | 扩展名 | 当前范围 |
| --- | --- | --- |
| MarkdownParser | `.md` | ATX Heading、fenced code、章节与 1-based 行号 |
| PlainTextParser | `.txt .json .yaml .yml .xml .properties` | UTF-8 文本与段落边界，不构建语法树 |
| CodeParser | `.java .jsp .js .ts .vue .sql .py` | 保留缩进、按空行和完整行切分 |
| PdfParser | `.pdf` | 使用 pypdf 按页提取文本层 |
| DocxParser | `.docx` | 使用 python-docx 按顺序读取段落和顶层表格 |

文本、Markdown 和代码只接受 UTF-8 或 UTF-8-SIG，统一 CRLF/CR 为 LF，拒绝 NUL 和不可解码内容。JSP/Vue 当前是行级混合文本解析，不是 AST 解析；不会执行用户代码。

PDF 页码从 1 开始，Chunk 不跨页。加密、损坏、超过页数限制或没有可提取文本会返回安全错误。扫描型 PDF 当前不支持 OCR，不提取图片、附件、JavaScript 或元数据正文。

DOCX 按文档顺序读取段落与顶层表格，Heading 1–9 更新章节；表格行以换行连接、单元格以制表符连接。DOCX 不提供可靠最终页码，不提取图片或执行宏；`.doc` 不支持。

## ParsedBlock 与 Chunk

ParsedBlock 包含正文、block_type、可选页码、成对起止行号、章节标题和语言。正文不得为空白，页码与行号均为 1-based。

ChunkDraft/DocumentChunk 包含从 0 连续递增的 chunk_index、正文、UTF-8 SHA-256、Python 字符数及同一组引用字段。Chunker 对相同输入和配置输出一致；PDF 不跨页，Markdown 优先不跨章节并保持 fenced code，代码使用完整行 overlap，DOCX 优先保持段落和表格边界。只有超长单行使用硬字符切分。

DocumentChunk 尚未写入 Qdrant，也没有 Embedding、BM25 或检索分数。

## 状态机与一致性

- `pending`：尚未解析，或自动入队失败后等待重试。
- `processing`：Worker 已接管，parse_started_at 存在。
- `succeeded`：当前 Chunk 可用，chunk_count 与记录数一致。
- `failed`：没有可用 Chunk，保存安全错误码和摘要。

未过期的 processing 和非强制 succeeded 会幂等跳过。processing 超过 `DOCUMENT_PARSE_STALE_AFTER_SECONDS` 后可由重复任务接管，不增加分布式锁。

重解析不会提前删除旧 Chunk。Parser 和 Chunker 完整成功后，删除旧 Chunk、批量插入新 Chunk、更新 Parser 和状态在单一事务中完成；失败 rollback。已有 Chunk 的重解析失败会保留 succeeded 与旧 chunk_count，同时保存最近错误。

自动入队失败不回滚已保存文件，上传响应返回 `parsing_queued=false`。手动重试使用同一 Dispatcher；队列不可用返回 503。

## API

- `POST /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/versions/{version_id}/parse?force=false`
- `GET /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/versions/{version_id}/parse-status`
- `GET /api/v1/knowledge-bases/{knowledge_base_id}/documents/{document_id}/versions/{version_id}/chunks?offset=0&limit=20`

成功入队返回 202；无需重复入队返回 200；不存在返回 404；队列不可用返回 503。Chunk API 按 chunk_index 升序并同时返回版本状态，不暴露 storage_path、绝对路径、Broker URL 或 traceback。

## 配置与本地运行

默认限制为 5,000,000 提取字符、1,000 PDF 页、1,800 字符 Chunk、200 字符 overlap 和 1,800 秒 processing stale 时间。所有值必须为正，overlap 小于 Chunk 上限，提取字符上限不得小于 Chunk 上限。

启动 Worker：

```bash
cd backend
uv run celery -A app.worker.celery_app:celery_app worker --loglevel=INFO
```

运行验证：

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest -m "not integration"
```

专用 `_test` PostgreSQL 数据库用于 migration 往返、约束、级联和事务测试。测试 fixture 在 tmp_path 动态生成，不下载网络文件，也不读取用户 uploads。

`parse_status=succeeded` 只表示结构化 Chunk 已保存，不代表可以进行全文、向量、混合检索或 RAG。
