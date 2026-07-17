# TraceMind

TraceMind 是一个面向中文开发者的、本地优先、答案可追溯的 AI 个人知识库。

## 当前目标

- 管理技术文档和代码资料
- 支持全文检索与语义检索
- 支持基于知识库的 AI 问答
- 回答可以追溯到文件、页码、章节和代码行
- 沉淀开发问题与解决经验

## 当前状态

- 项目已完成知识库管理的第一个业务垂直功能
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
- 运行前后端单元测试、静态检查与构建

文件导入与解析、Document/Chunk、检索和 RAG 等业务能力尚未实现。

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

知识库管理页面位于 `http://localhost:5173/knowledge-bases`。更完整的 Windows、macOS 和 Linux 开发步骤见 [开发指南](docs/development.md)，当前架构边界见 [架构说明](docs/architecture.md)，数据库结构见 [数据库设计](docs/database-design.md)。
