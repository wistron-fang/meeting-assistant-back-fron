# Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
# 未经授权，禁止转售或仿制。

"""
Celery worker 启动入口（一键脚本）

把启动 worker 需要的所有参数烤进来，省得每次都手敲一长串：
  - --pool=solo        Windows 必须，prefork 不可用
  - --without-mingle   单 worker 部署，跳过和其他 worker 的握手（省 60 秒）
  - --without-gossip   不广播状态
  - --without-heartbeat 不发心跳

并且在脚本内自动把 backend/app 注入 sys.path，省得手工设置 PYTHONPATH。

用法：
  cd E:\\meeting_minutes_assistant\\backend\\app
  .\\.venv\\Scripts\\Activate.ps1
  python run_worker.py
"""

import os
import sys

# 把 backend/app 加进 sys.path，确保 service.meeting 等扁平 import 能找到
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

from core.celery_app import celery_app

if __name__ == "__main__":
    celery_app.worker_main(
        argv=[
            "worker",
            "--loglevel=info",
            "--pool=solo",
            "-B",                  # 内嵌 beat：调度 cleanup_expired_meetings 等周期任务
            "--without-mingle",
            "--without-gossip",
            "--without-heartbeat",
        ]
    )
