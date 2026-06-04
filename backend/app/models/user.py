# Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
# 未经授权，禁止转售或仿制。

"""用户模型"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, Boolean, DateTime, SmallInteger, Integer, BigInteger, Date
from sqlalchemy.dialects.postgresql import UUID

from core.database import Base


class User(Base):
    """用户模型"""
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String(50), unique=True, nullable=False, index=True)
    email = Column(String(100), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    is_superuser = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # ---- 星级 / 配额（None 的覆盖列表示「取星级默认」，见 service/quota.py）----
    star_level = Column(SmallInteger, nullable=False, default=4, comment="星级 1~5，默认 4，驱动队列优先级与配额默认值")
    max_concurrent = Column(SmallInteger, nullable=True, comment="同时在跑上限覆盖；NULL 取星级默认")
    monthly_task_quota = Column(Integer, nullable=True, comment="每月可创建任务数覆盖；NULL 取星级默认")
    monthly_token_quota = Column(BigInteger, nullable=True, comment="每月 token 预算覆盖；NULL 取星级默认，0/负=不限")
    tokens_used_this_month = Column(BigInteger, nullable=False, default=0, comment="本月已消耗 token（跨月惰性重置）")
    quota_period_start = Column(Date, nullable=True, comment="本月配额起算日（每月 1 号），跨月触发重置")
