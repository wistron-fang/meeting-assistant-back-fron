#!/bin/bash
# ============================================================
# Start backend: uvicorn (FastAPI) + celery worker
# Usage: ./start_all.sh           (foreground uvicorn + bg worker)
#        ./start_all.sh stop      (stop both)
# Logs:  logs/uvicorn.log, logs/celery.log
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR/app"
LOG_DIR="$SCRIPT_DIR/logs"
PID_DIR="$SCRIPT_DIR/.pids"

mkdir -p "$LOG_DIR" "$PID_DIR"

UVICORN_PID="$PID_DIR/uvicorn.pid"
CELERY_PID="$PID_DIR/celery.pid"

stop_all() {
    for pidfile in "$UVICORN_PID" "$CELERY_PID"; do
        if [ -f "$pidfile" ]; then
            pid=$(cat "$pidfile")
            if kill -0 "$pid" 2>/dev/null; then
                echo "[stop] killing $(basename "$pidfile" .pid) (pid=$pid)"
                kill "$pid" || true
                # give celery time to drain in-flight tasks
                sleep 2
                kill -9 "$pid" 2>/dev/null || true
            fi
            rm -f "$pidfile"
        fi
    done
    echo "[stop] done"
}

if [ "${1:-}" = "stop" ]; then
    stop_all
    exit 0
fi

# Stop any previous instances first
stop_all

cd "$APP_DIR"
export PYTHONPATH="$APP_DIR"

# ---- Celery worker+beat (开发模式内嵌 beat；生产环境拆开，见 DEPLOYMENT.md) ----
echo "[start] celery worker+beat -> $LOG_DIR/celery.log"
nohup celery -A core.celery_app.celery_app worker -B -l info -c 2 \
    > "$LOG_DIR/celery.log" 2>&1 &
echo $! > "$CELERY_PID"

# ---- FastAPI uvicorn (foreground; logs also tee'd to file) ----
echo "[start] uvicorn :8100 -> $LOG_DIR/uvicorn.log"
echo "[start] press Ctrl+C to stop uvicorn (celery keeps running; use './start_all.sh stop' to stop both)"
exec uvicorn app_main:app --host 0.0.0.0 --port 8100 \
    2>&1 | tee "$LOG_DIR/uvicorn.log"
