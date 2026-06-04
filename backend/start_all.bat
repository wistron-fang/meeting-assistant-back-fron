@echo off
REM ============================================================
REM Start backend: uvicorn + celery worker + celery beat (3 windows)
REM Note: On Windows, celery -B option is disabled, so worker and
REM       beat must run as separate processes. This matches the
REM       production deployment described in DEPLOYMENT.md.
REM ============================================================

cd /d %~dp0app
if errorlevel 1 (
    echo [ERROR] Cannot cd to app folder
    pause
    exit /b 1
)
echo [OK] Working dir: %CD%

set PYTHONPATH=%~dp0app
set CONDA_ENV=D:\conda\envs\multiagent
set NO_PROXY=localhost,127.0.0.1,::1,.local,.aliyuncs.com,.aliyun.com,.bochaai.com,.juhe.cn

if not exist "%CONDA_ENV%\Scripts\uvicorn.exe" (
    echo [ERROR] uvicorn.exe not found at %CONDA_ENV%\Scripts\uvicorn.exe
    pause
    exit /b 1
)
if not exist "%CONDA_ENV%\Scripts\celery.exe" (
    echo [ERROR] celery.exe not found at %CONDA_ENV%\Scripts\celery.exe
    pause
    exit /b 1
)
echo [OK] Conda env: %CONDA_ENV%

REM FastAPI on :8100
start "uvicorn FastAPI 8100" cmd /k "%CONDA_ENV%\Scripts\uvicorn.exe app_main:app --reload --host 0.0.0.0 --port 8100"

REM Celery worker (runs tasks)
start "celery worker" cmd /k "%CONDA_ENV%\Scripts\celery.exe -A core.celery_app.celery_app worker -l info -P threads -c 2"

REM Celery beat (triggers scheduled tasks: daily cleanup)
start "celery beat" cmd /k "%CONDA_ENV%\Scripts\celery.exe -A core.celery_app.celery_app beat -l info"

echo.
echo [start_all] Three windows started: uvicorn + celery worker + celery beat
echo [start_all] Close all three windows to stop the backend
echo.
pause
