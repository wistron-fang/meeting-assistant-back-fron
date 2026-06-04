# Copyright © 2026 广州金元信息科技有限公司 版权所有
# 未经授权，禁止转售或仿制。

"""会议纪要 Celery 任务"""
import os
import re
import io
import sys
import logging
import traceback
import contextlib


# 输入质量预检：去除常见填充词 / 标点 / 空白后的有效字数阈值
# 任何低于此值的任务直接 failed，避免产出"空纪要还显示已完成"的误导性结果
_FILLER_PATTERN = re.compile(r"[嗯啊对哦呃噢呀哎欸唉嘿哈嘛吗呢吧]+|[，。、；：？！,.!?;:\s\r\n]+")
_MIN_EFFECTIVE_CHARS = 200


def _effective_text_len(text: str) -> int:
    """去除填充词 / 标点 / 空白后的有效字数。"""
    if not text:
        return 0
    return len(_FILLER_PATTERN.sub("", text))

from core.celery_app import celery_app
from core.database import SessionLocal
from models.meeting import MeetingTask, _beijing_now

# 顶层预加载：让 langchain/langgraph 等重型依赖在 worker 启动时一次加载完，
# 避免第一个任务"准备中"卡 ~46 秒。Python 模块缓存 (sys.modules) 保证只 import 一次。
from .minutes_engine import generate_minutes
from .pdf_renderer import md_to_pdf
from .docx_renderer import md_to_docx

logger = logging.getLogger(__name__)

MEETING_DATA_ROOT = os.getenv(
    "MEETING_DATA_ROOT",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../data/meeting"))
)


def _build_output_dir(user_id, task_id: int) -> str:
    path = os.path.join(MEETING_DATA_ROOT, str(user_id), str(task_id))
    os.makedirs(path, exist_ok=True)
    return path


# ============================================================
# 实时进度抓取：用 StringIO 接管 stdout，正则匹配阶段标记
# ============================================================
RE_MAIN_STEP = re.compile(r"^\s*步骤\s*(\d+)/7:\s*(.+?)\s*$", re.M)
RE_CHUNK = re.compile(r"\[ChunkSummarizer\]\s*摘要第\s*(\d+)/(\d+)\s*块", re.M)
RE_TOPIC_EXTRACT = re.compile(r"\[ContentExtractor\]\s*主题\s*(\d+)/(\d+)", re.M)
RE_QUALITY = re.compile(r"^\s*质量检查\s*$", re.M)
RE_RETRY = re.compile(r"重新撰写|未通过", re.M)


class StageCapture(io.StringIO):
    """边写边解析：每次 MReport print 进来，匹配阶段标记，命中就回调更新 DB"""

    STEP_PROGRESS = {1: 10, 2: 20, 3: 35, 4: 50, 5: 60, 6: 75, 7: 85}

    def __init__(self, on_update):
        super().__init__()
        self.on_update = on_update
        self._buf = ""

    def write(self, s):
        # 同时回写到原 stdout，方便 worker 终端继续看日志
        sys.__stdout__.write(s)
        self._buf += s
        if "\n" not in s:
            return len(s)
        lines = self._buf.split("\n")
        self._buf = lines[-1]
        for line in lines[:-1]:
            self._dispatch(line)
        return len(s)

    def flush(self):
        sys.__stdout__.flush()

    def _dispatch(self, line: str):
        m = RE_MAIN_STEP.search(line)
        if m:
            step = int(m.group(1))
            name = m.group(2).strip()
            self.on_update(stage=f"步骤 {step}/7：{name}",
                           progress=self.STEP_PROGRESS.get(step))
            return
        m = RE_CHUNK.search(line)
        if m:
            self.on_update(stage=f"摘要第 {m.group(1)}/{m.group(2)} 块")
            return
        m = RE_TOPIC_EXTRACT.search(line)
        if m:
            self.on_update(stage=f"提取主题 {m.group(1)}/{m.group(2)}")
            return
        if RE_QUALITY.search(line):
            self.on_update(stage="质量检查中", progress=88)
            return
        if RE_RETRY.search(line):
            self.on_update(stage="质量未达标，重新撰写")


AUDIO_CLEANUP_DELAY_SECONDS = 5 * 60  # 任务完成 5 分钟后清理音频源文件


@celery_app.task(bind=True, name="meeting.process")
def process_meeting(self, task_id: int):
    db = SessionLocal()
    task = None
    has_audio_source = False

    def update_state(**kwargs):
        """供 StageCapture 回调用：写 DB 同时不让异常打断主流程"""
        if task is None:
            return
        try:
            for k, v in kwargs.items():
                if v is not None:
                    setattr(task, k, v)
            db.commit()
        except Exception:
            db.rollback()

    try:
        task = db.query(MeetingTask).get(task_id)
        if task is None:
            logger.warning(f"[meeting] task {task_id} not found, skip")
            return

        # [token 计量] 开启本任务 token 累加器（contextvar，仅本执行上下文有效）
        from service.meeting.usage_meter import start_metering
        start_metering()

        task.status = "running"
        task.celery_id = self.request.id
        task.progress = 5
        task.stage = "已加入队列，准备开始"
        task.started_at = _beijing_now()
        db.commit()

        output_dir = task.output_dir or _build_output_dir(task.user_id, task.id)
        task.output_dir = output_dir
        db.commit()

        # ============ 音频源:先 ASR 转写,把文本回填到 source_text ============
        if task.source_type == "audio":
            has_audio_source = True
            from .asr import transcribe

            if not task.audio_path or not os.path.exists(task.audio_path):
                raise RuntimeError(f"音频文件不存在: {task.audio_path}")

            task.stage = "语音识别中"
            task.progress = 10
            db.commit()

            asr_result = transcribe(task.audio_path)
            transcript_text = asr_result["text"]
            transcript_path = os.path.join(output_dir, "transcript.txt")
            with open(transcript_path, "w", encoding="utf-8") as f:
                f.write(transcript_text)

            task.source_text = transcript_text
            task.transcript_path = transcript_path
            task.stage = f"语音识别完成 ({asr_result['engine']}, {asr_result['elapsed']:.1f}s)"
            task.progress = 25
            db.commit()
            logger.info(f"[meeting] task {task_id} ASR 完成 ({asr_result['engine']}), 文本长度 {len(transcript_text)}")

        # 输入质量预检：避免在垃圾输入上跑 LLM 流水线后产出"空纪要"
        eff_len = _effective_text_len(task.source_text or "")
        if eff_len < _MIN_EFFECTIVE_CHARS:
            raise RuntimeError(
                f"有效内容过少（去除填充词后仅 {eff_len} 字，阈值 {_MIN_EFFECTIVE_CHARS}）。"
                f"请检查录音质量或文本完整性"
            )

        # 用 StageCapture 抓 stdout，期间 MReport 的 print 会被解析
        cap = StageCapture(on_update=update_state)
        with contextlib.redirect_stdout(cap):
            result = generate_minutes(
                text=task.source_text or "",
                title=task.title or "",
                output_dir=output_dir,
            )

        # [token 计量] 本任务 LLM 总消耗：先打日志，再写库 + 累加用户月用量（工作二）
        try:
            from service.meeting.usage_meter import collect
            _usage = collect()
            _dur = (_beijing_now() - task.started_at).total_seconds() if task.started_at else None
            logger.info(
                "[TOKEN计量] task=%s 输入字数=%s 总token=%s (prompt=%s, completion=%s) "
                "LLM调用=%s次 by_model=%s 耗时=%ss",
                task_id, len(task.source_text or ""), _usage["total"],
                _usage["prompt"], _usage["completion"], _usage["calls"],
                _usage["by_model"], _dur,
            )
            # 写 task_usage + 行锁累加 users.tokens_used_this_month（跨月惰性重置）。
            # 失败只记日志不阻断主流程——配额是软约束，纪要产物更重要。
            try:
                from service.quota import record_task_usage
                record_task_usage(db, task_id=task.id, user_id=task.user_id, usage=_usage)
            except Exception:
                db.rollback()
                logger.exception("[TOKEN计量] task %s 写库失败（忽略）", task_id)
        except Exception:
            logger.exception("[TOKEN计量] task %s 统计失败（忽略）", task_id)

        task.md_path = result.get("md_path")
        task.rag_json_path = result.get("rag_json_path")
        # 用户没填标题时（占位符 "待生成…"），用引擎产出的最终标题覆盖
        final_title = (result.get("title") or "").strip()
        if final_title and task.title in (None, "", "标题待生成…"):
            task.title = final_title
        task.stage = "渲染 PDF"
        task.progress = 92
        db.commit()

        if task.md_path and os.path.exists(task.md_path):
            pdf_path = os.path.splitext(task.md_path)[0] + ".pdf"
            try:
                md_to_pdf(task.md_path, pdf_path)
                task.pdf_path = pdf_path
            except Exception as pdf_err:
                logger.warning(f"[meeting] task {task_id} pdf render failed: {pdf_err}")
                # PDF 失败不让整体失败，md 已经出来了
                task.stage = f"PDF 渲染失败（md 可下载）"

            # 同步渲染 DOCX：失败不影响整体任务
            docx_path = os.path.splitext(task.md_path)[0] + ".docx"
            try:
                md_to_docx(task.md_path, docx_path)
                task.docx_path = docx_path
            except Exception as docx_err:
                logger.warning(f"[meeting] task {task_id} docx render failed: {docx_err}")

        if task.status != "failed":
            task.status = "done"
            task.progress = 100
            task.stage = "完成"
            task.error = None
            task.finished_at = _beijing_now()
        db.commit()
        logger.info(f"[meeting] task {task_id} done -> {task.md_path}")

    except Exception as e:
        logger.exception(f"[meeting] task {task_id} failed")
        if task is not None:
            task.status = "failed"
            task.stage = "失败"
            task.error = (str(e) + "\n" + traceback.format_exc())[:2000]
            task.finished_at = _beijing_now()
            db.commit()
        raise
    finally:
        db.close()
        # 不论成功失败,音频源任务都要在 5 分钟后清理掉音频文件,避免占盘
        if has_audio_source:
            try:
                from .cleanup import cleanup_audio
                cleanup_audio.apply_async(args=[task_id], countdown=AUDIO_CLEANUP_DELAY_SECONDS)
                logger.info(f"[meeting] task {task_id} 已安排 {AUDIO_CLEANUP_DELAY_SECONDS}s 后清理音频")
            except Exception as e:
                logger.warning(f"[meeting] task {task_id} 安排音频清理失败 (忽略): {e}")