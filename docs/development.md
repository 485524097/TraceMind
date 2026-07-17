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

本阶段没有注册业务任务。

## 后端检查

在 `backend` 目录运行：

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy app
uv run pytest
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

## 停止容器

```bash
docker compose down
```

该命令保留命名 Volume。仅在明确不再需要本地数据时手动决定是否删除 Volume。
