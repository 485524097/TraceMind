# TraceMind 开发指南

## 前置条件

- Git
- Python 3.12
- uv
- Node.js 24 LTS 与 npm
- Docker Desktop，或支持 Docker Compose 的 Docker Engine

安装 uv 可参考 uv 官方安装方式；安装 Node.js 时建议使用版本管理器并选择当前 LTS 版本。

## 准备环境变量

Windows PowerShell：

```powershell
Copy-Item .env.example .env
Copy-Item frontend/.env.example frontend/.env
```

macOS/Linux：

```bash
cp .env.example .env
cp frontend/.env.example frontend/.env
```

示例值只用于本机开发。不要把 `.env` 提交到 Git，按需修改宿主机端口以规避冲突。

## 启动基础设施

在仓库根目录运行：

```bash
docker compose up -d postgres redis qdrant
docker compose ps
```

## 启动后端

Windows PowerShell、macOS 和 Linux 均可在 `backend` 目录运行：

```bash
uv sync
uv run uvicorn app.main:app --reload
```

默认 API 地址为 `http://localhost:8000`，开发环境 Swagger 文档位于 `/docs`。

本地文档默认保存到仓库 `data/uploads`。可通过 `DOCUMENT_STORAGE_ROOT`、`DOCUMENT_MAX_FILE_SIZE_BYTES`、`DOCUMENT_UPLOAD_CHUNK_SIZE_BYTES` 和 `DOCUMENT_ALLOWED_EXTENSIONS` 覆盖。真实上传目录和 `.env` 不得提交。

解析上限通过 `DOCUMENT_PARSE_MAX_EXTRACTED_CHARS`、`DOCUMENT_PARSE_MAX_PDF_PAGES`、`DOCUMENT_PARSE_STALE_AFTER_SECONDS`、`DOCUMENT_CHUNK_MAX_CHARS` 和 `DOCUMENT_CHUNK_OVERLAP_CHARS` 配置。backend 与 celery-worker 必须使用相同值。

Dense 索引配置使用 `QDRANT_COLLECTION_NAME`、`QDRANT_DENSE_VECTOR_NAME`、`QDRANT_OPERATION_TIMEOUT_SECONDS`、`QDRANT_UPSERT_BATCH_SIZE`、`SEMANTIC_SEARCH_SCORE_THRESHOLD`、`EMBEDDING_MODEL_NAME`、`EMBEDDING_DIMENSION`、`EMBEDDING_BATCH_SIZE`、`EMBEDDING_DEVICE` 和 `DOCUMENT_INDEX_STALE_AFTER_SECONDS`。默认相似度阈值 0.50 是当前 Dense Baseline，需结合本地资料评估。Qdrant 健康检查仍由 `HEALTHCHECK_TIMEOUT_SECONDS` 单独限制。backend 与 celery-worker 必须保持一致。首次实际调用 SentenceTransformer 会下载模型；CI 单元测试使用 FakeEmbeddingProvider，不下载模型。

## 启动前端

在 `frontend` 目录运行：

```bash
npm ci
npm run dev
```

默认页面地址为 `http://localhost:5173`。

## 启动 Celery Worker

先确保 Redis 正常，再在 `backend` 目录运行：

```bash
uv run celery -A app.worker.celery_app:celery_app worker --loglevel=INFO
```

Worker 注册 `app.tasks.documents.parse_document_version` 和 `app.tasks.indexing.index_document_version`。任务只接收 DocumentVersion UUID 与 force 标量，并为每次执行创建独立 AsyncEngine/Session。

## 后端检查

在 `backend` 目录运行：

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest -m "not integration"
```

## 数据库迁移

确保根目录 `.env` 指向开发数据库，然后在 `backend` 目录运行：

```bash
uv run alembic upgrade head
uv run alembic current
```

回退 migration 前必须确认目标数据库和数据影响。第一条 migration 可在专用测试数据库中验证：

```bash
uv run alembic downgrade base
uv run alembic upgrade head
```

## PostgreSQL 集成测试

集成测试只接受数据库名以 `_test` 结尾的 `TEST_DATABASE_URL`。Document 存储单元测试使用 pytest `tmp_path`，不读取本机上传目录。

Windows PowerShell：

```powershell
$env:TEST_DATABASE_URL = "postgresql+asyncpg://tracemind:本地测试密码@127.0.0.1:5432/tracemind_test"
./scripts/verify.ps1 -Integration
```

macOS/Linux：

```bash
export TEST_DATABASE_URL="postgresql+asyncpg://tracemind:本地测试密码@127.0.0.1:5432/tracemind_test"
./scripts/verify.sh --integration
```

默认验证脚本不会运行集成测试，也不会创建、清空或删除数据库和 Docker Volume。

Document parsing migration 往返命令：

```bash
uv run alembic upgrade head
uv run alembic downgrade 20260717_0002
uv run alembic upgrade head
```

Dense indexing migration 往返命令：

```bash
uv run alembic upgrade head
uv run alembic downgrade 20260717_0003
uv run alembic upgrade head
```

真实 Qdrant integration test 使用显式 `TEST_QDRANT_URL`，但仍使用固定 Fake Embedding 向量，不下载模型。

## 前端检查

在 `frontend` 目录运行：

```bash
npm run lint
npm run test:unit -- --run
npm run build
```

## 单轮 RAG 配置

在 `.env` 中同时设置 `LLM_BASE_URL` 和 `LLM_MODEL` 可启用 RAG；`LLM_API_KEY` 对不校验
Key 的本地 OpenAI-compatible 服务可以为空。其余参数及 SSE 事件见
[RAG 说明](rag.md)。未配置时应用正常启动，RAG API 返回受控 503。

本机 CUDA 环境只安装新增 SDK，避免同步替换 Torch：

```powershell
cd backend
uv lock
uv pip install --python .venv\Scripts\python.exe "openai>=2.46,<3"
uv run --no-sync pytest -m "not integration"
```

测试使用 Fake Provider，不需要真实 LLM，也不得将 API Key 写入仓库。

也可在仓库根目录运行 `scripts/verify.ps1`（Windows PowerShell）或 `scripts/verify.sh`（macOS/Linux）执行完整检查。脚本不会创建或覆盖 `.env`。

## 使用应用容器

```bash
docker compose --profile app up --build
```

该命令启动基础服务、后端与 Celery Worker。Vue 前端默认在本地使用 npm 启动。

容器内 backend 与 celery-worker 共享 `/app/data/uploads`；宿主机目录由 `DOCUMENT_STORAGE_HOST_PATH` 指定。Worker 从相同安全相对路径读取版本文件并生成数据库 Chunk。

## 停止容器

```bash
docker compose down
```

该命令保留命名 Volume。仅在明确不再需要本地数据时手动决定是否删除 Volume。
