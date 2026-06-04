# Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
# 未经授权，禁止转售或仿制。

"""会议纪要任务模型"""
from datetime import datetime, timezone, timedelta
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID

from core.database import Base


_BJ_TZ = timezone(timedelta(hours=8))


def _beijing_now() -> datetime:
    """返回去掉 tzinfo 的北京时间（与现有 naive DateTime 列兼容）"""
    return datetime.now(_BJ_TZ).replace(tzinfo=None)


class MeetingTask(Base):
    """会议纪要异步任务表"""
    __tablename__ = "meeting_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), index=True, nullable=False)
    title = Column(String(255), comment="会议标题")
    source_type = Column(String(20), comment="paste | upload | audio")
    source_text = Column(Text, comment="会议原文（音频源时由 ASR 回填）")
    output_dir = Column(String(500), comment="产物目录")
    status = Column(String(20), default="pending", index=True,
                    comment="pending|running|done|failed")
    progress = Column(Integer, default=0, comment="进度 0-100")
    celery_id = Column(String(64), comment="Celery task id，用于撤销")
    audio_path = Column(String(500), comment="本地音频文件路径（任务完成 5 分钟后清理）")
    audio_filename = Column(String(255), comment="音频原始文件名")
    transcript_path = Column(String(500), comment="ASR 转写文本路径")
    md_path = Column(String(500))
    pdf_path = Column(String(500))
    docx_path = Column(String(500))
    rag_json_path = Column(String(500))
    stage = Column(String(120), comment="当前阶段文案，用于前端展示")
    error = Column(Text)
    created_at = Column(DateTime, default=_beijing_now, index=True)
    updated_at = Column(DateTime, default=_beijing_now, onupdate=_beijing_now)
    started_at = Column(DateTime, comment="任务转 running 时刻（北京时间，naive）")
    finished_at = Column(DateTime, comment="任务转 done/failed 时刻，写入后不再变")
