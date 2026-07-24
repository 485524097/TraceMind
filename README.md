# TraceMind

TraceMind 是一个面向中文开发者的、本地优先、答案可追溯的 AI 个人知识库。

## 当前目标

- 管理技术文档和代码资料
- 支持全文检索与语义检索
- 支持基于知识库的 AI 问答
- 回答可以追溯到文件、页码、章节和代码行
- 沉淀开发问题与解决经验

## 当前状态

- 项目已完成知识库管理、本地文档增量导入、异步解析、可追溯 Chunk、Dense/BM25 混合检索、本地 Cross-Encoder 重排和单轮流式 RAG
- 尚未发布可用版本

## 规划技术栈

- Python 3.12
- FastAPI
- SQLAlchemy 2
- PostgreSQL
- Redis
- Celery
- Qdrant
- Vue 3
- TypeScript
- Vite
- Docker Compose

## 安全说明

- 不要导入未经授权的公司代码、客户数据或内部文档。
- 不要提交 API Key、密码和私人敏感资料。

## 当前可用能力

- 使用 Docker Compose 启动 PostgreSQL、Redis 和 Qdrant
- 启动 FastAPI，并通过存活与就绪接口检查服务状态
- 启动 Vue 3 首页并检查后端存活状态
- 创建、查询、修改和删除知识库
- 在 Vue 3 管理页面维护知识库名称与描述
- 上传、列出、搜索、下载和删除知识库文档
- 按文件名和 SHA-256 判断首次导入、内容未变化或创建新版本
- 查看并下载 DocumentVersion 历史版本
- 自动或手动解析 Markdown、UTF-8 文本、代码、PDF 文本层和 DOCX
- 查看解析状态、错误摘要、Chunk 正文及页码、章节和代码行引用
- 使用 Qwen3 Embedding 和 Qdrant 建立 Dense 索引并进行可追溯语义检索
- 使用 Qdrant 服务端 BM25 与 RRF 进行关键词、技术标识和语义混合检索
- 使用独立本地 Qwen3 Cross-Encoder 服务进行二阶段重排，并在不可用时安全回退
- 使用带来源引用的单轮流式 RAG 问答
- 运行前后端单元测试、静态检查与构建

文件可导入、解析并建立 Dense Embedding 与服务端 BM25 双向量索引。RAG 默认使用 Hybrid Top 10 → 本地 Reranker → Top 5，Reranker 故障时回退 Hybrid。Dense、Hybrid 和 Reranked API 均保留用于调试对比。当前尚无 Weighted RRF、Reranker 训练或对话历史。首次实际索引或查询会下载 Embedding 模型；BM25 由本地 Qdrant Server 执行。

## 最小启动

复制环境变量示例：

```powershell
Copy-Item .env.example .env
```

启动基础服务：

```powershell
docker compose up -d postgres redis qdrant
```

启动后端：

```powershell
cd backend
uv sync
uv run uvicorn app.main:app --reload
```

启动前端：

```powershell
cd frontend
npm ci
npm run dev
```

知识库管理页面位于 `http://localhost:5173/knowledge-bases`。更完整的开发步骤见 [开发指南](docs/development.md)，索引边界见 [向量索引说明](docs/vector-indexing.md)，Dense + BM25 RRF 设计见 [混合检索说明](docs/hybrid-retrieval.md)，二阶段排序见 [Reranker 说明](docs/reranker.md)，单轮流式问答见 [RAG 说明](docs/rag.md)。
