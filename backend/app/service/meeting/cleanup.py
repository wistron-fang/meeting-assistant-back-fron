# Copyright © 2026 广州金元信息科技有限公司 版权所有
# 未经授权，禁止转售或仿制。

"""会议纪要定期清理任务

- cleanup_expired_meetings: 每天清理超过 MEETING_RETENTION_DAYS 的任务记录及产物
- cleanup_audio: 单任务音频源 5 分钟后清理 (process_meeting 结束时延时投递)
"""
import os
import shutil
import logging
from datetime import timedelta

from core.celery_app import celery_app
from core.database import SessionLocal
from models.meeting import MeetingTask, _beijing_now

logger = logging.getLogger(__name__)

RETENTION_DAYS = int(os.getenv("MEETING_RETENTION_DAYS", "7"))


@celery_app.task(name="meeting.cleanup_expired")
def cleanup_expired_meetings():
    """删除超过保留期的会议任务（DB 行 + 产物目录）

    时区说明：created_at 写入的是北京时间 naive datetime，因此 cutoff 也用
    北京时间，避免与 utcnow 比较造成 8 小时偏移。
    """
    cutoff = _beijing_now() - timedelta(days=RETENTION_DAYS)
    db = SessionLocal()
    try:
        expired = db.query(MeetingTask).filter(MeetingTask.created_at < cutoff).all()
        deleted = 0
        for t in expired:
            if t.output_dir and os.path.isdir(t.output_dir):
                shutil.rmtree(t.output_dir, ignore_errors=True)
            db.delete(t)
            deleted += 1
        db.commit()
        logger.info(f"[cleanup] 清理过期会议纪要 {deleted} 条（保留期 {RETENTION_DAYS} 天）")
        return deleted
    except Exception:
        db.rollback()
        logger.exception("[cleanup] 清理失败")
        raise
    finally:
        db.close()


@celery_app.task(name="meeting.cleanup_audio")
def cleanup_audio(task_id: int):
    """删除单个任务的音频源文件 (转写文本 / md / pdf 不动)"""
    db = SessionLocal()
    try:
        t = db.query(MeetingTask).get(task_id)
        if t is None:
            logger.info(f"[cleanup_audio] task {task_id} 已删除,跳过")
            return
        path = t.audio_path
        if path and os.path.exists(path):
            try:
                os.remove(path)
                logger.info(f"[cleanup_audio] task {task_id} 已删除音频: {path}")
            except OSError as e:
                logger.warning(f"[cleanup_audio] task {task_id} 删除失败: {e}")
                return
        t.audio_path = None
        db.commit()
    except Exception:
        db.rollback()
        logger.exception(f"[cleanup_audio] task {task_id} 清理异常")
    finally:
        db.close()
