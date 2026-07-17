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

当前没有注册 Celery 业务任务；知识库 CRUD 直接访问 PostgreSQL。

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

Document migration 往返命令：

```bash
uv run alembic upgrade head
uv run alembic downgrade 20260717_0001
uv run alembic upgrade head
```

## 前端检查

在 `frontend` 目录运行：

```bash
npm run lint
npm run test:unit -- --run
npm run build
```

也可在仓库根目录运行 `scripts/verify.ps1`（Windows PowerShell）或 `scripts/verify.sh`（macOS/Linux）执行完整检查。脚本不会创建或覆盖 `.env`。

## 使用应用容器

```bash
docker compose --profile app up --build
```

该命令启动基础服务、后端与 Celery Worker。Vue 前端默认在本地使用 npm 启动。

容器内 backend 与 celery-worker 共享 `/app/data/uploads`；宿主机目录由 `DOCUMENT_STORAGE_HOST_PATH` 指定。Celery 本阶段不处理文档，只保证未来任务可访问相同文件。

## 停止容器

```bash
docker compose down
```

该命令保留命名 Volume。仅在明确不再需要本地数据时手动决定是否删除 Volume。
