# Copyright © 2026 深圳市深维智见教育科技有限公司 版权所有
# 未经授权，禁止转售或仿制。

"""
语音识别服务
============================================

提供两种识别方式:
  - 极速版 (Aliyun NLS Flash): 单文件 ≤ 100MB,毫秒级返回
  - 普通版 (Qwen-ASR via DashScope + OSS): 大文件、长音频

入口函数 ``transcribe(audio_path)``:
  - 文件 ≤ 100MB 优先用极速版
  - 极速版业务失败 (status != 20000000) 或文件 > 100MB,自动回退普通版
  - 返回 ``{"text": str, "sentences": list[dict], "engine": "flash"|"qwen", "elapsed": float}``

所有凭据从环境变量读取:
  ALIYUN_ACCESS_KEY_ID / ALIYUN_ACCESS_KEY_SECRET
  ALIYUN_NLS_APPKEY
  ALIYUN_OSS_ENDPOINT / ALIYUN_OSS_BUCKET
  DASHSCOPE_API_KEY
"""

import os
import time
import json
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)


# ============ 配置 ============
NLS_GATEWAY = "https://nls-gateway-cn-shanghai.aliyuncs.com/stream/v1/FlashRecognizer"
NLS_REGION = "cn-shanghai"
FLASH_SIZE_LIMIT_BYTES = 100 * 1024 * 1024  # 100 MB
DEFAULT_SAMPLE_RATE = 16000

# 扩展名 → 极速版 format 参数 (m4a 是 mp4 容器,直接传 mp4)
_EXT_TO_FLASH_FORMAT = {
    ".m4a": "mp4",
    ".mp4": "mp4",
    ".wav": "wav",
    ".aac": "aac",
    ".mp3": "mp3",
}


def _env(key: str) -> str:
    v = os.getenv(key, "").strip()
    if not v:
        raise RuntimeError(f"环境变量未配置: {key}")
    return v


# ============ 极速版 ============

def _get_nls_token() -> str:
    """获取 NLS Token (有效期 24h,这里每次现取,简单可靠)"""
    from aliyunsdkcore.client import AcsClient
    from aliyunsdkcore.request import CommonRequest

    client = AcsClient(
        _env("ALIYUN_ACCESS_KEY_ID"),
        _env("ALIYUN_ACCESS_KEY_SECRET"),
        NLS_REGION,
    )
    request = CommonRequest()
    request.set_method("POST")
    request.set_domain("nls-meta.cn-shanghai.aliyuncs.com")
    request.set_version("2019-02-28")
    request.set_action_name("CreateToken")
    response = client.do_action_with_exception(request)
    return json.loads(response)["Token"]["Id"]


def _call_flash(audio_path: str, audio_format: str, token: str) -> tuple:
    params = {
        "appkey": _env("ALIYUN_NLS_APPKEY"),
        "token": token,
        "format": audio_format,
        "sample_rate": DEFAULT_SAMPLE_RATE,
    }
    headers = {"Content-Type": "application/octet-stream"}
    with open(audio_path, "rb") as f:
        audio_data = f.read()

    start = time.time()
    response = requests.post(
        NLS_GATEWAY, params=params, headers=headers,
        data=audio_data, timeout=600,
    )
    return response, time.time() - start


def _parse_flash_result(result: dict) -> tuple:
    flash_result = result.get("flash_result", {})
    sentences = flash_result.get("sentences", []) if isinstance(flash_result, dict) else []
    if not sentences and isinstance(flash_result, list):
        for ch in flash_result:
            sentences.extend(ch.get("sentences", []))
    full_text = "".join(s.get("text", "") for s in sentences)
    return full_text, sentences


def flash_recognize(audio_path: str) -> dict:
    """
    极速版识别 (单文件 ≤ 100MB)

    自动按扩展名选 format,失败时尝试 fallback 到 aac。
    返回 {text, sentences, engine, elapsed}。识别失败抛 RuntimeError。
    """
    size = os.path.getsize(audio_path)
    if size > FLASH_SIZE_LIMIT_BYTES:
        raise RuntimeError(f"极速版不支持 >100MB 文件 (当前 {size/1024/1024:.1f}MB)")

    ext = os.path.splitext(audio_path)[1].lower()
    primary_fmt = _EXT_TO_FLASH_FORMAT.get(ext, "mp4")
    # m4a/mp4 失败回退 aac;wav/mp3/aac 没有回退
    fallback_fmt = "aac" if primary_fmt == "mp4" else None

    token = _get_nls_token()
    logger.info(f"[asr.flash] 文件 {size/1024/1024:.2f}MB, format={primary_fmt}")

    for attempt_fmt in [primary_fmt, fallback_fmt]:
        if attempt_fmt is None:
            continue
        response, elapsed = _call_flash(audio_path, attempt_fmt, token)
        if response.status_code != 200:
            logger.warning(f"[asr.flash] format={attempt_fmt} HTTP {response.status_code}: {response.text[:200]}")
            continue
        result = response.json()
        if result.get("status") == 20000000:
            full_text, sentences = _parse_flash_result(result)
            if full_text:
                logger.info(f"[asr.flash] OK, {len(sentences)} 句, 耗时 {elapsed:.1f}s")
                return {
                    "text": full_text,
                    "sentences": sentences,
                    "engine": "flash",
                    "elapsed": elapsed,
                }
            logger.warning(f"[asr.flash] format={attempt_fmt} 成功但文本为空")
        else:
            logger.warning(f"[asr.flash] format={attempt_fmt} 业务失败: status={result.get('status')}, msg={result.get('message')}")

    raise RuntimeError("极速版识别失败 (所有 format 均失败)")


# ============ 普通版 (Qwen-ASR) ============

def qwen_recognize(audio_path: str) -> dict:
    """
    普通版识别 (大文件 / 长音频)

    流程: OSS 上传 → Qwen-ASR async → wait → 拉结果 → 清理 OSS。
    返回 {text, sentences, engine, elapsed}。识别失败抛 RuntimeError。
    """
    import oss2
    import dashscope
    from dashscope.audio.qwen_asr import QwenTranscription
    from http import HTTPStatus

    oss_endpoint = _env("ALIYUN_OSS_ENDPOINT")
    oss_bucket_name = _env("ALIYUN_OSS_BUCKET")
    dashscope.api_key = _env("DASHSCOPE_API_KEY")
    dashscope.base_http_api_url = "https://dashscope.aliyuncs.com/api/v1"

    auth = oss2.Auth(_env("ALIYUN_ACCESS_KEY_ID"), _env("ALIYUN_ACCESS_KEY_SECRET"))
    bucket = oss2.Bucket(auth, oss_endpoint, oss_bucket_name)

    object_key = f"asr-tmp/{int(time.time())}_{os.path.basename(audio_path)}"
    logger.info(f"[asr.qwen] 上传 OSS: {object_key}")
    try:
        bucket.put_object_from_file(object_key, audio_path)
    except oss2.exceptions.OssError as e:
        raise RuntimeError(f"OSS 上传失败: {e}") from e

    file_url = bucket.sign_url("GET", object_key, 7200, slash_safe=True)

    start = time.time()
    try:
        task_response = QwenTranscription.async_call(
            model="qwen3-asr-flash-filetrans",
            file_url=file_url,
            language="zh",
            enable_itn=False,
            enable_words=True,
        )
        if task_response.status_code != HTTPStatus.OK:
            raise RuntimeError(f"Qwen-ASR 提交失败: {task_response.message}")

        task_id = task_response.output.task_id
        logger.info(f"[asr.qwen] task_id={task_id}, 等待中...")

        task_result = QwenTranscription.wait(task=task_id)
        if task_result.status_code != HTTPStatus.OK:
            raise RuntimeError(f"Qwen-ASR 识别失败: {task_result.message}")

        result = task_result.output.get("result")
        if not result or "transcription_url" not in result:
            raise RuntimeError(f"Qwen-ASR 结果异常: {task_result}")

        result_json = requests.get(result["transcription_url"], timeout=60).json()
    finally:
        # 清理 OSS 文件 (识别失败也清,避免残留)
        try:
            bucket.delete_object(object_key)
            logger.info(f"[asr.qwen] OSS 已清理: {object_key}")
        except Exception as e:
            logger.warning(f"[asr.qwen] OSS 清理失败 (忽略): {e}")

    elapsed = time.time() - start
    transcripts = result_json.get("transcripts", [])
    full_text = ""
    sentences = []
    for t in transcripts:
        full_text += t.get("text", "")
        sentences.extend(t.get("sentences", []))

    if not full_text:
        raise RuntimeError("Qwen-ASR 返回文本为空")

    logger.info(f"[asr.qwen] OK, {len(sentences)} 句, 耗时 {elapsed:.1f}s")
    return {
        "text": full_text,
        "sentences": sentences,
        "engine": "qwen",
        "elapsed": elapsed,
    }


# ============ 入口分发器 ============

def transcribe(audio_path: str, prefer: Optional[str] = None) -> dict:
    """
    自动选择引擎识别音频。

    Args:
        audio_path: 本地音频文件路径
        prefer: 强制指定引擎 ("flash" | "qwen"),None 表示自动 (默认)

    Returns:
        {"text": str, "sentences": list, "engine": "flash"|"qwen", "elapsed": float}
    """
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"音频文件不存在: {audio_path}")

    size = os.path.getsize(audio_path)

    if prefer == "qwen":
        return qwen_recognize(audio_path)
    if prefer == "flash":
        return flash_recognize(audio_path)

    # 自动:小文件优先极速版,失败回退普通版
    if size <= FLASH_SIZE_LIMIT_BYTES:
        try:
            return flash_recognize(audio_path)
        except Exception as e:
            logger.warning(f"[asr] 极速版失败,回退普通版: {e}")
    else:
        logger.info(f"[asr] 文件 {size/1024/1024:.1f}MB 超过极速版上限,直接走普通版")

    return qwen_recognize(audio_path)
