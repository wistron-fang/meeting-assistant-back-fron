import os
import sys

# 把 backend/app/ 注入 sys.path，让 celery worker 能用 `from core.xxx`、`from service.xxx` 这种项目惯用的扁平 import
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

# 加载 .env，确保 worker 进程拿到 DASHSCOPE_API_KEY 等密钥
try:
    from dotenv import load_dotenv
    # backend/.env 优先，没有就退到 backend/app/.env
    for env_path in (
        os.path.join(_APP_DIR, "..", ".env"),
        os.path.join(_APP_DIR, ".env"),
    ):
        if os.path.isfile(env_path):
            load_dotenv(env_path)
            break
except ImportError:
    pass

from celery import Celery

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "") or None
CELERY_DB = os.getenv("CELERY_REDIS_DB", "1")

if REDIS_PASSWORD:
    REDIS_URL = f"redis://:{REDIS_PASSWORD}@{REDIS_HOST}:{REDIS_PORT}/{CELERY_DB}"
else:
    REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/{CELERY_DB}"

celery_app = Celery("meeting", broker=REDIS_URL, backend=REDIS_URL)
celery_app.conf.update(
    task_serializer="json", result_serializer="json",
    accept_content=["json"], timezone="Asia/Shanghai",
    task_track_started=True,
    task_time_limit=60 * 40,         # 单任务 30 分钟超时
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    # ---- 工作一：星级优先队列 ----
    # 高星用户任务在队列堆积时先被 worker 取走。priority 由 service.quota.star_to_priority 算。
    # ✅ 方向已实测（2026-06-02 ECS）：本套 Redis「数字越小=优先级越高」→ star5→1、star1→9。
    task_queue_max_priority=9,
    task_default_priority=5,
    broker_transport_options={
        "queue_order_strategy": "priority",
        "priority_steps": list(range(10)),  # 0..9
        "sep": ":",
    },
)
celery_app.autodiscover_tasks(["service.meeting"])

# Beat 周期任务（worker 用 -B 参数内嵌 beat 时生效）
celery_app.conf.beat_schedule = {
    "cleanup-meetings-daily": {
        "task": "meeting.cleanup_expired",
        "schedule": 24 * 3600,  # 每 24 小时执行一次
    },
}
