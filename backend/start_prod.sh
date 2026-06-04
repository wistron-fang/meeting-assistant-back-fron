#!/bin/bash
# =====================================================================
# 会议纪要 - 生产 ECS 一键启动全栈（幂等：已在跑的服务跳过）
# =====================================================================
# 部署位置：阿里云香港 ECS 4核8G，路径写死为该机环境。
# 用法（重开机 / 新 session / 进程死了之后）：
#   bash /opt/meeting/start.sh      # ECS 上的副本
# 起：Redis(systemd) + uvicorn(:8100, 4 workers) + Celery worker(-c 32)
# 注意：API 的大连接池只内联在 uvicorn 那行（不写 .env，否则 worker 也吃到 ×32 会爆 PG）。
# =====================================================================

source /root/miniconda3/bin/activate meeting
export PYTHONPATH=/opt/meeting/backend/app
cd /opt/meeting/backend/app

# Redis（systemd 开机自启，确保在跑）
systemctl is-active --quiet redis || systemctl start redis
echo "[redis] $(redis-cli ping 2>&1)"

# uvicorn（没在跑才起）
if pgrep -f "uvicorn app_main:app" >/dev/null; then
  echo "[uvicorn] 已在运行"
else
  # API 小池（内联前缀，仅作用于 uvicorn，不污染后面 worker）。PG max_connections=70，预算紧：
  # 4 进程 ×(池3+溢3=6)=24 封顶，轮询是短查询实际只用 ~8。--workers 4 抗 100 人轮询。
  DB_POOL_SIZE=3 DB_MAX_OVERFLOW=3 \
    nohup uvicorn app_main:app --host 0.0.0.0 --port 8100 --workers 4 > /opt/meeting/uvicorn.log 2>&1 &
  echo "[uvicorn] 已启动 (--workers 4, pool 3+3)"
fi

# Celery worker -c 32（没在跑才起）。注意必须 export PYTHONPATH，否则 No module named 'service'
if pgrep -f "celery.*worker" >/dev/null; then
  echo "[worker] 已在运行"
else
  # worker 用 NullPool（用完即关）：否则 -c 32 prefork 每进程留 1 条 idle ×32 会吃光 70 条上限。
  # -B 内嵌 beat：触发 celery_app.beat_schedule 的每日 cleanup_expired（删 7 天前任务+产物）。
  # ⚠️ 单机才可用 -B；将来扩多机 worker 须去掉 -B、改独立 RedBeat（全集群唯一），否则每台都触发会重复清理。
  DB_POOL_MODE=null \
    nohup celery -A core.celery_app.celery_app worker -l info -c 32 -B > /opt/meeting/worker.log 2>&1 &
  echo "[worker] 已启动 (-c 32, NullPool, -B 内嵌 beat)"
fi

sleep 8
echo "[健康检查] $(curl -s http://localhost:8100/hello)"
echo "[worker]   $(grep -m1 ready /opt/meeting/worker.log 2>/dev/null)"

# 备注：
# - 并发/月任务/月token 配额已走星级逻辑（service/quota.py），MAX_CONCURRENT_PER_USER 已删（2026-06-02）
# - celery beat 已随 worker -B 内嵌：每日跑 cleanup_expired，删 MEETING_RETENTION_DAYS(默认7)天前的任务+产物
# - JWT token 14 天过期，过后重新 /auth/login
# - ⚠️ 本脚本 2026-06-02 发现 ECS 上 /opt/meeting/start.sh 丢失，已从本副本重建；
#   换机/重置后记得把本文件 cp 到 /opt/meeting/start.sh
