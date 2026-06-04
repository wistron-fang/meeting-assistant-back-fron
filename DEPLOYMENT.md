# 部署备忘

记录开发环境与生产环境的差异，上生产时按 checklist 调整。

## 开发环境（当前）

- **Windows 强制** worker + beat 拆成两个进程（celery 在 Windows 上禁用了 `-B` 参数，必须独立 beat）
- 因此 `start_all.bat` 启 **3 个 cmd 窗口**：uvicorn + celery worker + celery beat
- Linux/Mac 可以用 `-B` 内嵌（`start_all.sh`），但为了开发/生产一致也建议拆开
- 单机、单 worker 实例

## 重要 Bug 备忘（已修复）

### .env 加载顺序问题（database.py）

**症状**：celery worker 启动后接到任务立即报 `psycopg2.OperationalError: database "xxx" does not exist`，连的端口/库名不对。

**根因**：`backend/app/core/database.py` 顶部 `os.getenv("POSTGRES_PORT", "5432")` 在 celery worker 进程的模块加载序列中**早于** `core/celery_app.py` 的 `load_dotenv()`，导致 `DATABASE_URL` 用了默认值（5432）而不是 .env 里的实际端口（5532）。

**修复**：`database.py` 顶部增加防御性 `load_dotenv()`，并把默认 `POSTGRES_PORT` 从 `5432` 对齐到 `5532`。提交者：上线前如果改 .env 加载方式，**务必**保留 database.py 里的 load_dotenv 逻辑。

### Windows 上 Celery `-B` 参数被禁用

**症状**：`celery worker -B ...` 启动报错 `-B option does not work on Windows`。

**修复**：start_all.bat 已拆为 worker + beat 两个进程。这正好与生产部署要求一致，**生产环境无需再拆**。

## 上生产环境前必做（按顺序）

### 1. Celery worker 去掉 `-B`，beat 独立成进程

**为什么**：多 worker 实例都带 `-B` 会重复触发定时任务（一天清理 N 次而非 1 次）。

**怎么改**：

```bash
# Worker（可多实例，去掉 -B）
celery -A core.celery_app.celery_app worker -l info -c 4

# Beat（全集群只能一个实例！）
celery -A core.celery_app.celery_app beat -l info
```

### 2. 用进程管理工具管两个进程（三选一）

- **Docker Compose**：加 `celery_worker` 和 `celery_beat` 两个 service
- **systemd**：写两个 service unit
- **Supervisor**：写两个 program

### 3. （多机部署时）切换到 RedBeat 调度器

**为什么**：默认 beat 用本地文件存调度状态，多 beat 实例会冲突。RedBeat 把 schedule 存 Redis，多实例自动加分布式锁。

**怎么改**：

```bash
pip install celery-redbeat
```

在 `backend/app/core/celery_app.py` 末尾追加（仅生产环境）：

```python
import os
if os.getenv("APP_ENV") == "prod":
    celery_app.conf.beat_scheduler = "redbeat.RedBeatScheduler"
    celery_app.conf.redbeat_redis_url = REDIS_URL
```

### 4. CORS 改成具体域名

`backend/app/app_main.py` 当前：

```python
allow_origins=["*"]
```

生产改为：

```python
allow_origins=["https://yourdomain.com"]
```

### 5. .env 敏感配置

- `JWT_SECRET_KEY`：必须重新生成（当前是开发用的）
- `POSTGRES_PASSWORD`：从 `postgres123` 改成强密码
- 各 API Key（DASHSCOPE / DOCMIND 等）：用生产专用账号

### 6. 数据保留期（如需调整）

环境变量 `MEETING_RETENTION_DAYS`（默认 7 天）。生产可调长，例如：

```env
MEETING_RETENTION_DAYS=30
```

### 7. 日志

生产建议用 structured logging + 集中化（Loki/ELK），把 `logging.basicConfig` 换成更完整的配置。

## 端口对照（开发用，生产按需调整）

| 服务 | 开发端口 | 容器内端口 |
|---|---|---|
| FastAPI (uvicorn) | 8100 | — |
| Vite (前端 dev) | 5273 | — |
| PostgreSQL | 5532 | 5432 |
| Redis | 6479 | 6379 |
