# Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
# 未经授权，禁止转售或仿制。

"""任务 token 用量审计表（每个会议任务一条）"""
from sqlalchemy import Column, Integer, BigInteger, String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID

from core.database import Base
from models.meeting import _beijing_now


class TaskUsage(Base):
    """单个会议任务的 LLM token 消耗审计记录"""
    __tablename__ = "task_usage"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("meeting_tasks.id", ondelete="CASCADE"), index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    model = Column(String(64), comment="消耗最多 token 的模型名（明细在 worker 日志）")
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    calls = Column(Integer, default=0, comment="本任务经 invoke_safe 的 LLM 调用次数")
    created_at = Column(DateTime, default=_beijing_now, index=True)
