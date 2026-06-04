# Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
# 未经授权，禁止转售或仿制。

"""
星级 / 配额中枢（工作一 + 工作二）

集中三件事：
  1. 星级默认配额（STAR_QUOTAS，数字按 0.6 节定稿实测反推）。
  2. 取「有效配额」：用户列有覆盖值就用覆盖，否则取星级默认。
  3. token 用量落库 + 累加用户月用量（含跨月惰性重置）。

「按月」窗口：任务数 = 实时 count(meeting_tasks 本月)；token = users.tokens_used_this_month 计数列。
两者都在每月 1 号滚动；token 列靠 quota_period_start 做惰性重置（见 reset_if_new_month）。
"""
from datetime import date, datetime
from typing import Optional, Dict, Any

from models.meeting import _beijing_now


# 星级 → 默认配额。月任务数 / 月 token / 并发 / 队列优先级。
# token 配额 = 单任务实测均值 71k × 1.25 buffer ≈ 90k，再乘月任务数得到月预算。
# priority 列：⚠️ 实测确认（2026-06-02，ECS 生产）本套 Celery+Redis 下「数字越小=优先级越高」。
# 所以高星给小数字：star5→1（最先跑）… star1→9（最后跑）。改这列时务必记住这个方向。
STAR_QUOTAS: Dict[int, Dict[str, int]] = {
    5: {"max_concurrent": 5, "monthly_task_quota": 200, "monthly_token_quota": 18_000_000, "priority": 1},
    4: {"max_concurrent": 4, "monthly_task_quota":  20, "monthly_token_quota":  9_000_000, "priority": 3},
    3: {"max_concurrent": 3, "monthly_task_quota":  50, "monthly_token_quota":  4_500_000, "priority": 5},
    2: {"max_concurrent": 2, "monthly_task_quota":  20, "monthly_token_quota":  1_800_000, "priority": 7},
    1: {"max_concurrent": 1, "monthly_task_quota":  10, "monthly_token_quota":    900_000, "priority": 9},
}
DEFAULT_STAR = 4  # 所有用户默认 4 星（2026-06-02 改）；star4 月任务配额=20


def star_of(user) -> int:
    """取用户星级，落在 1~5 之外或缺失时回落到 DEFAULT_STAR。"""
    s = getattr(user, "star_level", None) or DEFAULT_STAR
    return s if s in STAR_QUOTAS else DEFAULT_STAR


def _eff(user, override_attr: str, default_key: str) -> int:
    """有覆盖列（非 None）就用覆盖，否则取星级默认。"""
    v = getattr(user, override_attr, None)
    if v is not None:
        return v
    return STAR_QUOTAS[star_of(user)][default_key]


def effective_max_concurrent(user) -> int:
    return _eff(user, "max_concurrent", "max_concurrent")


def effective_monthly_task_quota(user) -> int:
    return _eff(user, "monthly_task_quota", "monthly_task_quota")


def effective_monthly_token_quota(user) -> int:
    """≤0 表示不限。"""
    return _eff(user, "monthly_token_quota", "monthly_token_quota")


def star_to_priority(star: Optional[int]) -> int:
    """星级 → Celery 任务优先级。

    ✅ 方向已实测确认（2026-06-02 ECS）：本套 Redis broker「数字越小=优先级越高」，
    故 star5→1（最先被取走）… star1→9（最后）。见 STAR_QUOTAS priority 列。
    """
    return STAR_QUOTAS.get(star or DEFAULT_STAR, STAR_QUOTAS[DEFAULT_STAR])["priority"]


# ---------------- 「按月」时间窗口 ----------------

def current_month_start(today: Optional[date] = None) -> datetime:
    """本月 1 号 0 点（北京时间，naive）——用于 count 本月任务数。"""
    today = today or _beijing_now().date()
    return datetime(today.year, today.month, 1)


def current_month_tokens_used(user, today: Optional[date] = None) -> int:
    """读「本月已用 token」。若计数列还停在上个月（未触发重置），按 0 算。只读，不改库。"""
    today = today or _beijing_now().date()
    start = getattr(user, "quota_period_start", None)
    if start is None or (start.year, start.month) != (today.year, today.month):
        return 0
    return int(getattr(user, "tokens_used_this_month", 0) or 0)


def reset_if_new_month(user, today: Optional[date] = None) -> bool:
    """跨月惰性重置：quota_period_start 不在本月就把月用量清零并把起算日设为本月 1 号。

    在累加用量前调用（写路径），保证计数只统计当月。返回是否发生了重置。
    """
    today = today or _beijing_now().date()
    start = getattr(user, "quota_period_start", None)
    if start is None or (start.year, start.month) != (today.year, today.month):
        user.tokens_used_this_month = 0
        user.quota_period_start = today.replace(day=1)
        return True
    return False


def _top_model(usage: Dict[str, Any]) -> Optional[str]:
    """取本任务消耗 token 最多的模型名（task_usage 单行只存一个）。"""
    by_model = (usage or {}).get("by_model") or {}
    if not by_model:
        return None
    return max(by_model.items(), key=lambda kv: kv[1].get("total", 0))[0]


def record_task_usage(db, *, task_id: int, user_id, usage: Dict[str, Any]) -> None:
    """工作二落库：写一条 task_usage + 行锁累加 users.tokens_used_this_month（跨月先重置）。

    在 worker 的 process_meeting 里，generate_minutes 之后调用。失败由调用方吞掉不阻断主流程。
    """
    from models.usage import TaskUsage
    from models.user import User

    total = int((usage or {}).get("total", 0) or 0)

    db.add(TaskUsage(
        task_id=task_id,
        user_id=user_id,
        model=_top_model(usage),
        prompt_tokens=int((usage or {}).get("prompt", 0) or 0),
        completion_tokens=int((usage or {}).get("completion", 0) or 0),
        total_tokens=total,
        calls=int((usage or {}).get("calls", 0) or 0),
    ))

    # 行锁串行化同用户并发任务的累加，避免丢更新
    user = db.query(User).filter(User.id == user_id).with_for_update().first()
    if user is not None:
        reset_if_new_month(user)
        user.tokens_used_this_month = int(user.tokens_used_this_month or 0) + total

    db.commit()
