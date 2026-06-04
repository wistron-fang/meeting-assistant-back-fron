# Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
# 未经授权，禁止转售或仿制。

"""
LLM token 用量计量器（按任务隔离）

用 contextvars 实现：在 Celery 任务 process_meeting 开头调用 start_metering()，
之后该任务执行上下文内所有经过 LLMClient.invoke_safe 的调用，都会把 token 累加进来；
任务结束时 collect() 取总量。

为什么用 contextvar：对 prefork / solo / threads 三种 worker 池都安全——每个执行上下文
各持一份，并发任务互不串台（不像模块级全局变量在线程池下会串）。

当前阶段：仅用于压测读数（在 tasks.py 里打 [TOKEN计量] 日志）。
后续「工作二」再把 collect() 写入 task_usage 表 + 累加 users.tokens_used_this_month + 配额拦截。
"""
import contextvars
from typing import Optional, Dict, Any

_usage: "contextvars.ContextVar[Optional[Dict[str, Any]]]" = contextvars.ContextVar(
    "llm_usage", default=None
)


def _empty() -> Dict[str, Any]:
    return {"prompt": 0, "completion": 0, "total": 0, "calls": 0, "by_model": {}}


def start_metering() -> None:
    """任务开头调用，重置当前上下文累加器。"""
    _usage.set(_empty())


def add_usage(prompt: int = 0, completion: int = 0, total: int = 0,
              model: Optional[str] = None) -> None:
    """每次 LLM 调用后累加（invoke_safe 钩子调用）。未 start_metering 时静默跳过。"""
    cur = _usage.get()
    if cur is None:
        return
    prompt = prompt or 0
    completion = completion or 0
    total = total or (prompt + completion)
    cur["prompt"] += prompt
    cur["completion"] += completion
    cur["total"] += total
    cur["calls"] += 1
    if model:
        m = cur["by_model"].setdefault(
            model, {"prompt": 0, "completion": 0, "total": 0, "calls": 0}
        )
        m["prompt"] += prompt
        m["completion"] += completion
        m["total"] += total
        m["calls"] += 1


def collect() -> Dict[str, Any]:
    """任务结束时取总量。未 start_metering 返回空结构。"""
    return _usage.get() or _empty()
