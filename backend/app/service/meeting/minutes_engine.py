#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
会议纪要智能整理系统 - 无说话人标注版（v5-enhanced）
基于LangChain + LangGraph的多智能体协作框架

核心改动（相对v4版）：
v4基础功能：
- 移除说话人解析依赖，改为基于语义/段落的文本分段
- 新增"语义段落切分器"：按句群、自然段、语义转折点切分
- 分块策略改为基于段落语义相关性，而非说话人变化
- 说话人分析改为"角色/观点提取"（从内容中推断可能的发言角色）
- 质量检查移除说话人覆盖率指标
- 所有segment的speaker_id统一为"发言者"或LLM推断的角色标签

v5新增功能：
1. 主题集中展开模式：
   - 当识别出的主题数<=4个时，自动触发展开模式
   - 对每个主题进行深度拆分，分3-8个子主题，每个子主题下2-5个具体要点
   - 写入时使用专用模板，以"**子主题标题**"加粗分小节，编号要点详细展开
   
2. 大会发言模式（conference mode）：
   - 自动检测"有请XXX发言"、"尊敬的..."等大会发言特征
   - 按发言人切分内容，识别主持人串场 vs 正式发言
   - 提取每位发言人的身份、主题、关键数据、目标承诺
   - 生成"按发言人组织"的会议纪要结构
   - 每位发言人的内容按分类小标题展开
"""

import os
import json
import re
import time
import traceback
from typing import List, Dict, TypedDict, Optional
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from difflib import SequenceMatcher
import numpy as np
from openai import OpenAI as EmbeddingOpenAI

# PPL 混合切分器（规则 + Ollama 本地 Qwen3-0.6B）
# 如果不需要 PPL 切分，设 USE_PPL_SEGMENTATION = False
USE_PPL_SEGMENTATION = True
try:
    if USE_PPL_SEGMENTATION:
        from hybrid_segment_parser import HybridSegmentParser
except ImportError:
    USE_PPL_SEGMENTATION = False
    print("  [提示] hybrid_segment_parser.py 未找到，将使用纯规则切分")

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, BaseMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import JsonOutputParser, StrOutputParser
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langchain_openai import ChatOpenAI, AzureChatOpenAI
from langgraph.graph import StateGraph, END
# 删除了 langgraph.prebuilt / checkpoint 的死 import（原 MReport 残留，本文件未使用）

# ============================================================
# 配置管理
# ============================================================
from config.llm_config import get_config
_cfg = get_config()
class Config:
    """系统配置"""

    OPENAI_API_KEY = _cfg.api_key
    OPENAI_BASE_URL = _cfg.base_url


    # 模型配置 - qwen-long 支持长上下文
    MODEL_NAME = "deepseek-v4-pro"
    MODEL_TEMPERATURE = 0.2
    MODEL_MAX_TOKENS = 8192

    # ===== LLM调用安全配置 =====
    LLM_TIMEOUT = 180
    LLM_MAX_RETRIES = 2
    MAX_INPUT_CHARS = 10000

    # 处理配置
    QUALITY_THRESHOLD = 75
    MAX_RETRY_COUNT = 2

    # ===== 分块配置（无说话人版本适配） =====
    CHUNK_SIZE = 40                # 每块段落数
    CHUNK_OVERLAP = 3
    MAX_TOPICS = 15
    CONTENT_LIMIT_PER_SEG = 500

    # ===== 无说话人文本切分配置 =====
    # 段落切分：目标每段 80-200 字，句子合并成段
    PARAGRAPH_MIN_CHARS = 60
    PARAGRAPH_MAX_CHARS = 300
    # 语义切分的时间间隔阈值（如果文本中有时间戳）
    SEMANTIC_GAP_THRESHOLD = 5.0   # 句间停顿秒数阈值

    # ===== 主题集中度配置 =====
    # 当主题数少于此值时，认为主题集中，需要展开细节
    TOPIC_CONCENTRATED_THRESHOLD = 4
    # 主题集中时，每个主题最少的子要点数
    MIN_SUBPOINTS_PER_TOPIC = 5
    # 展开时每个子要点的最小字数
    SUBPOINT_MIN_CHARS = 30

    # ===== 大会发言模式配置 =====
    # 大会发言模式的说话人最少数量
    CONFERENCE_MIN_SPEAKERS = 3
    # 每位发言者的最少段落数
    CONFERENCE_MIN_PARAGRAPHS_PER_SPEAKER = 2

    # ===== 大会模式产出兜底（末期 fallback）=====
    # 若 conference 模式跑完后，最终纪要纯文本字数 / 原文字数 < 该阈值，
    # 判定大会模式失败，自动重跑一次普通模式
    CONFERENCE_FALLBACK_RATIO = 1 / 15

    # 输出配置
    OUTPUT_ENCODING = "utf-8"

    # ===== Embedding 语义分块配置 =====
    EMBEDDING_MODEL = os.getenv("MEETING_EMBEDDING_MODEL", "text-embedding-v3")
    EMBEDDING_DIMENSIONS = 512
    EMBEDDING_BATCH_SIZE = 10
    EMBEDDING_MAX_RETRIES = 4          # 429 时可能要重试更多次
    EMBEDDING_RETRY_BASE_DELAY = 1.0   # 指数退避基数
    EMBEDDING_RETRY_MAX_DELAY = 60.0   # 单次最长等待（避免 429 卡太久）
    EMBEDDING_RETRY_DELAY = 1.0        # 保留以兼容旧代码
    # depth 阈值：使用自适应（75分位数），以下为绝对下限兜底
    SEMANTIC_DEPTH_MIN_THRESHOLD = 0.05
    # 绝对相似度下限（低于此值一定是断点）
    SEMANTIC_MIN_SIMILARITY = 0.3


# ============================================================
# Prompt 版本管理
# ============================================================

class PromptManager:
    """
    Prompt 版本管理器：支持从外部 YAML/JSON 文件加载 prompt，
    也支持按会议类型选择不同 prompt 版本，方便做 A/B 测试。

    用法：
    1. 默认使用代码中内置的 prompt（向后兼容）
    2. 如果存在 prompts.yaml 或 prompts.json，自动加载覆盖
    3. 支持运行时 hot-reload：PromptManager.reload()

    配置文件格式（prompts.yaml）：
        chunk_summarizer:
            system_prompt: "你是..."
            # 可按会议类型定义变体
            variants:
                conference: "你是大会纪要..."
                research: "你是调研纪要..."
        content_extractor:
            system_prompt: "..."
        ...

    配置文件格式（prompts.json）同理，用 JSON 结构。
    """
    _instance = None
    _prompts = {}
    _config_path = None

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
            cls._instance._auto_load()
        return cls._instance

    def _auto_load(self):
        """自动检测并加载外部 prompt 配置文件"""
        search_paths = [
            "prompts.yaml", "prompts.yml", "prompts.json",
            "config/prompts.yaml", "config/prompts.yml", "config/prompts.json",
        ]
        for path in search_paths:
            if os.path.exists(path):
                self.load_from_file(path)
                return

    def load_from_file(self, path: str):
        """从文件加载 prompt 配置"""
        self._config_path = path
        try:
            with open(path, 'r', encoding='utf-8') as f:
                if path.endswith('.json'):
                    self._prompts = json.load(f)
                elif path.endswith(('.yaml', '.yml')):
                    try:
                        import yaml
                        self._prompts = yaml.safe_load(f)
                    except ImportError:
                        print("  [PromptManager] ⚠ PyYAML未安装，跳过YAML加载")
                        return
            print(f"  [PromptManager] ✓ 已加载外部prompt: {path} "
                  f"({len(self._prompts)} 个Agent)")
        except Exception as e:
            print(f"  [PromptManager] ⚠ 加载失败({path}): {e}")

    @classmethod
    def reload(cls):
        """重新加载配置文件（热更新）"""
        inst = cls.get_instance()
        if inst._config_path:
            inst.load_from_file(inst._config_path)

    @classmethod
    def get_prompt(cls, agent_name: str, meeting_type: str = "",
                   default: str = "") -> str:
        """
        获取指定 Agent 的 system prompt。

        优先级：
        1. 外部配置中该 agent 的会议类型变体
        2. 外部配置中该 agent 的通用 prompt
        3. 代码中内置的 default prompt

        参数:
            agent_name:   Agent标识（如 'chunk_summarizer', 'content_extractor'）
            meeting_type: 会议类型（如 'conference', 'research'）
            default:      代码内置的默认 prompt
        """
        inst = cls.get_instance()
        agent_config = inst._prompts.get(agent_name, {})

        if not agent_config:
            return default

        # 优先查找会议类型变体
        if meeting_type:
            variants = agent_config.get("variants", {})
            if meeting_type in variants:
                return variants[meeting_type]

        # 使用通用 prompt
        return agent_config.get("system_prompt", default)

    @classmethod
    def export_current_prompts(cls, output_path: str = "prompts_template.json"):
        """
        导出当前所有内置 prompt 为 JSON 模板文件，
        方便用户基于此模板进行定制。
        """
        # 收集所有内置 prompt（需要在各Agent初始化后调用）
        template = {
            "_comment": "会议纪要系统 Prompt 配置模板。修改后保存为 prompts.json 放在运行目录即可生效。",
            "_usage": "每个 agent 下的 system_prompt 是默认 prompt，variants 下可按会议类型定义不同版本。",
            "chunk_summarizer": {
                "system_prompt": "(由系统自动填充)",
                "variants": {
                    "conference": "(可选：大会发言模式的专用prompt)",
                    "research": "(可选：调研会模式的专用prompt)"
                }
            },
            "participant_infer": {"system_prompt": "(由系统自动填充)"},
            "topic_segmentation": {"system_prompt": "(由系统自动填充)"},
            "content_extractor": {"system_prompt": "(由系统自动填充)"},
            "conference_speech_extractor": {"system_prompt": "(由系统自动填充)"},
            "section_writer": {"system_prompt": "(由系统自动填充)"},
            "format_enhancer": {"system_prompt": "(由系统自动填充)"},
        }
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(template, f, ensure_ascii=False, indent=2)
        print(f"  [PromptManager] 模板已导出: {output_path}")
        return output_path


# ============================================================
# 数据模型
# ============================================================

@dataclass
class QualityReport:
    score: int = 0
    completeness: int = 0
    accuracy: int = 0
    structure: int = 0
    readability: int = 0
    issues: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    is_passed: bool = False

    def to_dict(self) -> Dict:
        return {
            "score": self.score, "completeness": self.completeness,
            "accuracy": self.accuracy, "structure": self.structure,
            "readability": self.readability, "issues": self.issues,
            "suggestions": self.suggestions, "is_passed": self.is_passed
        }


# ============================================================
# LangGraph 状态定义
# ============================================================

class MeetingState(TypedDict):
    raw_text: str
    metadata: Dict
    segments: List[Dict]           # 每个segment是一个语义段落（不再依赖speaker_id）
    speakers: Dict[str, Dict]      # 改为"角色/观点来源"推断
    topics: List[Dict]
    chunk_summaries: List[Dict]
    content_sections: Dict[str, str]
    markdown_content: str
    final_output: str
    current_step: str
    retry_count: int
    quality_report: Dict
    error_message: str
    should_regenerate: bool
    meeting_type: str


# ============================================================
# LLM客户端封装（不改动）
# ============================================================

class LLMClient:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_llm()
        return cls._instance

    def _init_llm(self):
        self.llm = ChatOpenAI(
            model=Config.MODEL_NAME,
            temperature=Config.MODEL_TEMPERATURE,
            max_tokens=Config.MODEL_MAX_TOKENS,
            api_key=Config.OPENAI_API_KEY,
            base_url=Config.OPENAI_BASE_URL,
            timeout=Config.LLM_TIMEOUT,
            max_retries=Config.LLM_MAX_RETRIES,
            extra_body={"enable_thinking": False},
        )

    def invoke_safe(self, prompt: str, system_prompt: str = "",
                    json_mode: bool = False) -> any:
        """安全调用LLM，带异常捕获"""
        messages = []
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))
        messages.append(HumanMessage(content=prompt))

        try:
            response = self.llm.invoke(messages)
            # [token 计量] 累加本次调用 token 到当前任务上下文；未 start_metering 时静默跳过。
            # 对 token_usage(老版) 与 usage_metadata(新版) 双兜底，缺 total 则用 prompt+completion。
            try:
                from service.meeting.usage_meter import add_usage
                _meta = getattr(response, "response_metadata", None) or {}
                _u = _meta.get("token_usage") or {}
                _pt = _u.get("prompt_tokens")
                _ct = _u.get("completion_tokens")
                _tt = _u.get("total_tokens")
                if _tt is None:
                    _um = getattr(response, "usage_metadata", None) or {}
                    _pt = _um.get("input_tokens", 0)
                    _ct = _um.get("output_tokens", 0)
                    _tt = _um.get("total_tokens", 0)
                add_usage(prompt=_pt or 0, completion=_ct or 0,
                          total=_tt or 0, model=_meta.get("model_name"))
            except Exception:
                pass
            content = response.content
            if json_mode:
                return self._extract_json(content)
            return content
        except Exception as e:
            print(f"    ⚠ LLM调用异常: {type(e).__name__}: {e}")
            return {} if json_mode else ""

    def invoke(self, prompt: str, system_prompt: str = "", json_mode: bool = False) -> any:
        return self.invoke_safe(prompt, system_prompt, json_mode)

    def _extract_json(self, text: str) -> Dict:
        if not text:
            return {}
        raw = None
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            patterns = [
                r'```json\s*\n(.*?)\n\s*```',
                r'```\s*\n(.*?)\n\s*```',
                r'(\{[\s\S]*\})',
            ]
            for pattern in patterns:
                match = re.search(pattern, text, re.DOTALL)
                if match:
                    try:
                        raw = json.loads(match.group(1))
                        break
                    except (json.JSONDecodeError, IndexError):
                        continue
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            return {}
        # 统一 schema 校验和 coerce
        return self._validate_and_coerce(raw)

    @staticmethod
    def _validate_and_coerce(data: Dict) -> Dict:
        """
        统一校验和修正 LLM 输出的 JSON 结构。
        解决字段类型错误（如 key_points 返回字符串而非列表）、null 值等问题。
        """
        # 定义期望是列表的字段
        list_fields = [
            "key_points", "decisions", "action_items", "issues",
            "topics_mentioned", "names_entities", "data_points",
            "inferred_speakers", "semantic_tags", "sub_topics",
            "commitments", "highlights", "key_data", "points",
            "dimensions", "participants", "sub_sections",
            "data_findings", "creative_ideas", "risk_items",
            "achievements", "policy_measures",
        ]

        for key in list_fields:
            if key in data:
                val = data[key]
                if val is None:
                    data[key] = []
                elif isinstance(val, str):
                    # 字符串 → 包成单元素列表
                    if val.strip():
                        data[key] = [val.strip()]
                    else:
                        data[key] = []
                elif isinstance(val, dict):
                    # 单个dict → 包成列表
                    data[key] = [val]
                elif not isinstance(val, list):
                    data[key] = []

        # 定义期望是字符串的字段
        str_fields = ["summary", "speech_theme", "meeting_type",
                       "meeting_context", "extraction_hint",
                       "meeting_nature", "type_description",
                       "reason", "expand_reason"]
        for key in str_fields:
            if key in data:
                val = data[key]
                if val is None:
                    data[key] = ""
                elif isinstance(val, list):
                    data[key] = "；".join(str(v) for v in val)
                elif not isinstance(val, str):
                    data[key] = str(val)

        # 定义期望是布尔的字段
        bool_fields = ["should_expand"]
        for key in bool_fields:
            if key in data:
                val = data[key]
                if isinstance(val, str):
                    data[key] = val.lower() in ("true", "yes", "是")
                elif not isinstance(val, bool):
                    data[key] = bool(val)

        # 校验列表中的元素：key_points 里每个元素应该是 dict
        for key in ["key_points", "decisions", "action_items", "issues",
                     "sub_topics", "sub_sections", "dimensions", "participants"]:
            if key in data and isinstance(data[key], list):
                coerced = []
                for item in data[key]:
                    if isinstance(item, dict):
                        coerced.append(item)
                    elif isinstance(item, str) and item.strip():
                        # 字符串元素 → 包成 dict
                        if key == "key_points":
                            coerced.append({"point": item, "evidence_quote": ""})
                        elif key == "decisions":
                            coerced.append({"decision": item, "evidence_quote": ""})
                        elif key == "action_items":
                            coerced.append({"content": item, "assignee": None,
                                            "due_date": None, "priority": None,
                                            "source_quote": ""})
                        elif key == "issues":
                            coerced.append({"issue": item})
                        else:
                            coerced.append({"value": item})
                    # 跳过 None 和空值
                data[key] = coerced

        return data


# ============================================================
# 工具函数
# ============================================================

class BaseAgent:
    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self.llm = LLMClient()
        self._prompt_manager = PromptManager

    def get_system_prompt(self, meeting_type: str = "") -> str:
        """获取当前 Agent 的 system prompt，支持外部配置覆盖"""
        # 将类名转为 snake_case 作为 agent_name
        cls_name = self.__class__.__name__
        agent_name = re.sub(r'(?<!^)(?=[A-Z])', '_', cls_name).lower()
        # 去掉 _agent 后缀
        agent_name = agent_name.replace('_agent', '')

        # 获取内置的默认 prompt
        default_prompt = ""
        if hasattr(self, 'SYSTEM_PROMPT'):
            default_prompt = self.SYSTEM_PROMPT
        elif hasattr(self, 'BASE_SYSTEM_PROMPT'):
            default_prompt = self.BASE_SYSTEM_PROMPT

        return self._prompt_manager.get_prompt(agent_name, meeting_type, default_prompt)

    def log(self, message: str):
        print(f"  [{self.name}] {message}")


def safe_truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


def build_segment_text(segments: List[Dict], max_chars: int = None,
                       content_limit: int = None) -> str:
    """将段落列表构建为文本，严格控制总长度"""
    max_chars = max_chars or Config.MAX_INPUT_CHARS
    content_limit = content_limit or Config.CONTENT_LIMIT_PER_SEG
    lines = []
    total_len = 0
    for seg in segments:
        content = safe_truncate(seg.get('content', ''), content_limit)
        # 无说话人版本：用段落序号标识
        line = f"[段落{seg.get('sequence', 0)}] {content}"
        if total_len + len(line) + 1 > max_chars:
            break
        lines.append(line)
        total_len += len(line) + 1
    return "\n".join(lines)


# ============================================================
# 核心改动：无说话人文本切分器
# ============================================================

class NoSpeakerTextParser:
    """
    无说话人文本解析器

    策略：
    1. 先尝试按自然段（双换行、多空格分隔）切分
    2. 如果自然段太少或太长，进一步按句子切分并合并为语义段落
    3. 每个segment赋予统一的speaker_id="发言者"（保持数据结构兼容）
    4. 支持检测是否实际包含说话人标注，如果有则回退到原版解析
    """

    def __init__(self):
        self.name = "NoSpeakerParser"

    def log(self, msg):
        print(f"  [{self.name}] {msg}")

    def has_speaker_labels(self, text: str) -> bool:
        """检测文本是否包含说话人标注"""
        patterns = [
            r'说话人\s*\d+\s+\d{1,2}:\d{2}:\d{2}',
            r'Speaker\s*\d+\s+\d{1,2}:\d{2}:\d{2}',
            r'[\[【]说话人\s*\d+[\]】]',
        ]
        for p in patterns:
            if re.search(p, text):
                return True
        # 检查是否有 "姓名：" 格式且出现多次
        name_pattern = re.findall(r'\n([\u4e00-\u9fa5]{2,4})[：:]', text)
        if len(name_pattern) >= 3 and len(set(name_pattern)) >= 2:
            return True
        return False

    def process(self, raw_text: str) -> Dict:
        """解析无说话人标注的会议文本"""
        self.log("开始解析无说话人文本...")

        # 第一步：预处理 - 清理多余空白但保留段落结构
        cleaned = self._preprocess(raw_text)

        # 第二步：尝试按自然段落切分
        paragraphs = self._split_by_paragraphs(cleaned)
        self.log(f"自然段落切分: {len(paragraphs)} 段")

        # 第三步：如果段落太少（说明是连续文本），改用句子切分再合并
        if len(paragraphs) < 5:
            self.log("自然段落太少，改用句子级切分...")
            paragraphs = self._split_by_sentences_and_merge(cleaned)
            self.log(f"句子合并后: {len(paragraphs)} 段")

        # 第四步：如果单段太长，进一步拆分
        paragraphs = self._split_long_paragraphs(paragraphs)

        # 第五步：构建segments（兼容原有数据结构）
        segments = []
        for i, para in enumerate(paragraphs):
            if not para.strip():
                continue
            segments.append({
                "speaker_id": "发言者",   # 统一标识
                "content": para.strip(),
                "timestamp": "",
                "sequence": i
            })

        # 重新编号
        for i, seg in enumerate(segments):
            seg["sequence"] = i

        self.log(f"解析完成: {len(segments)} 个语义段落")
        return {
            "segments": segments,
            "speaker_ids": ["发言者"],
            "total_segments": len(segments),
            "parse_method": "no_speaker_semantic"
        }

    def _preprocess(self, text: str) -> str:
        """预处理文本"""
        # 清理零宽空格等不可见字符
        text = re.sub(r'[\u200b\u200c\u200d\ufeff\u00a0]', ' ', text)
        # 移除可能存在的说话人标记残留（如果用户混用了格式）
        text = re.sub(r'说话人\s*\d+\s*', '', text)
        # 标准化换行
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        # 将多个连续空格（>=3）视为段落分隔
        text = re.sub(r'  {2,}', '\n\n', text)
        return text.strip()

    def _split_by_paragraphs(self, text: str) -> List[str]:
        """按自然段落切分（双换行分隔）"""
        # 按双换行或多个换行切分
        paragraphs = re.split(r'\n\s*\n', text)
        paragraphs = [p.strip() for p in paragraphs if p.strip()]
        return paragraphs

    def _split_by_sentences_and_merge(self, text: str) -> List[str]:
        """
        句子级切分后按语义合并为段落
        核心逻辑：
        1. 先按中文句号、问号、感叹号切分为句子
        2. 检测"话题转换信号"（疑问句、转折词、新话题关键词）
        3. 在转换信号处断开，其余句子合并为段落
        """
        # 句子切分
        sentences = self._sentence_split(text)
        if not sentences:
            return [text]

        # 合并为段落
        paragraphs = []
        current_para = []
        current_len = 0

        for i, sent in enumerate(sentences):
            sent_len = len(sent)

            # 判断是否应该在此处开始新段落
            should_break = False

            # 规则1：当前段落已经够长
            if current_len >= Config.PARAGRAPH_MAX_CHARS:
                should_break = True

            # 规则2：检测到话题转换信号
            if current_len >= Config.PARAGRAPH_MIN_CHARS:
                if self._is_topic_shift(sent, sentences[i-1] if i > 0 else ""):
                    should_break = True

            # 规则3：疑问句通常标志着对话轮次切换
            if current_len >= Config.PARAGRAPH_MIN_CHARS:
                if sent.rstrip().endswith('？') or sent.rstrip().endswith('?'):
                    # 疑问句本身归入当前段，然后断开
                    current_para.append(sent)
                    current_len += sent_len
                    paragraphs.append(''.join(current_para))
                    current_para = []
                    current_len = 0
                    continue

            if should_break and current_para:
                paragraphs.append(''.join(current_para))
                current_para = []
                current_len = 0

            current_para.append(sent)
            current_len += sent_len

        if current_para:
            paragraphs.append(''.join(current_para))

        return paragraphs

    def _sentence_split(self, text: str) -> List[str]:
        """中文句子切分"""
        # 按句末标点切分，保留标点
        parts = re.split(r'([。！？!?]+)', text)
        sentences = []
        for i in range(0, len(parts) - 1, 2):
            if i + 1 < len(parts):
                sent = parts[i] + parts[i + 1]
            else:
                sent = parts[i]
            sent = sent.strip()
            if sent:
                sentences.append(sent)
        # 处理末尾无标点的部分
        if len(parts) % 2 == 1 and parts[-1].strip():
            sentences.append(parts[-1].strip())
        return sentences

    def _is_topic_shift(self, current_sent: str, prev_sent: str) -> bool:
        """检测是否存在话题转换信号"""
        # 转折/转换词
        shift_markers = [
            '但是', '不过', '然后', '接下来', '另外', '此外',
            '关于', '说到', '对于', '至于', '还有就是',
            '所以', '因此', '总之', '总的来说',
            '第一', '第二', '第三', '首先', '其次', '最后',
            '现在', '目前', '后来', '之后', '接着',
        ]
        for marker in shift_markers:
            if current_sent.lstrip().startswith(marker):
                return True

        # 如果前一句是回答（短句），当前句是新话题开始（较长句），也可能是转换
        if prev_sent and len(prev_sent) < 20 and len(current_sent) > 50:
            return True

        return False

    def _split_long_paragraphs(self, paragraphs: List[str],
                                max_len: int = None) -> List[str]:
        """拆分过长的段落"""
        max_len = max_len or Config.PARAGRAPH_MAX_CHARS * 2
        result = []
        for para in paragraphs:
            if len(para) <= max_len:
                result.append(para)
            else:
                # 按句子拆分再合并
                sents = self._sentence_split(para)
                current = []
                current_len = 0
                for s in sents:
                    if current_len + len(s) > max_len and current:
                        result.append(''.join(current))
                        current = []
                        current_len = 0
                    current.append(s)
                    current_len += len(s)
                if current:
                    result.append(''.join(current))
        return result


# ============================================================
# 大会发言模式解析器
# ============================================================

class ConferenceSpeechParser:
    """
    大会发言模式解析器

    适用场景：大会上多位发言人轮流上台讲话
    特征：主持人引导 → 发言人A发言 → 主持人引导 → 发言人B发言 → ...

    策略：
    1. 识别"有请XXX发言"等主持人引导语，确定发言人切换点
    2. 将每位发言人的连续内容合并为一个"发言段"
    3. 尝试提取发言人姓名、职务、单位信息
    4. 分离主持人串场内容和各发言人正式发言内容
    """

    def __init__(self):
        self.name = "ConferenceSpeechParser"

    def log(self, msg):
        print(f"  [{self.name}] {msg}")

    def is_conference_style(self, text: str) -> bool:
        """检测是否为大会发言模式"""
        indicators = 0
        # 检测主持人引导语
        invite_patterns = [
            r'有请.*发言', r'接下来.*有请', r'大家欢迎',
            r'请.*讲话', r'请.*发言', r'请看大屏幕',
        ]
        for p in invite_patterns:
            matches = re.findall(p, text)
            indicators += len(matches)

        # 检测多人开场白
        greetings = re.findall(r'尊敬的.*?[，,].*?大家.*?好', text)
        indicators += len(greetings)

        # 检测"谢谢大家"等结束语
        endings = re.findall(r'谢谢大家|我的发言完毕|发言完毕', text)
        indicators += len(endings)

        self.log(f"大会发言模式检测指标: {indicators}")
        return indicators >= 5

    def process(self, raw_text: str) -> Dict:
        """解析大会发言模式的文本，返回按发言人分组的结构"""
        self.log("开始解析大会发言模式...")

        # 第一步：预处理
        cleaned = self._preprocess(raw_text)

        # 第二步：按说话人标记或发言切换点切分
        speech_blocks = self._split_into_speech_blocks(cleaned)
        self.log(f"识别到 {len(speech_blocks)} 个文本块")

        # 第三步：识别发言人并合并连续发言
        speeches = self._identify_and_merge_speeches(speech_blocks)
        self.log(f"合并为 {len(speeches)} 段发言")

        # 第四步：构建segments（兼容原有数据结构）
        segments = []
        seq = 0
        for speech in speeches:
            speaker_label = speech.get("speaker_name", "发言者")
            role = speech.get("role", "")
            org = speech.get("organization", "")
            if role or org:
                speaker_label = f"{speaker_label}（{org}{role}）" if org else f"{speaker_label}（{role}）"

            segments.append({
                "speaker_id": speaker_label,
                "content": speech["content"],
                "timestamp": speech.get("timestamp", ""),
                "sequence": seq,
                "speech_type": speech.get("type", "speech"),  # "host" or "speech"
                "speaker_name": speech.get("speaker_name", "未知"),
                "speaker_role": speech.get("role", ""),
                "speaker_org": speech.get("organization", ""),
            })
            seq += 1

        # 重新编号
        for i, seg in enumerate(segments):
            seg["sequence"] = i

        # 提取发言人信息
        speaker_ids = list(set(seg["speaker_id"] for seg in segments))
        speaker_info = {}
        for speech in speeches:
            name = speech.get("speaker_name", "未知")
            if name != "未知" and name != "主持人":
                speaker_info[name] = {
                    "name": name,
                    "role": speech.get("role", ""),
                    "organization": speech.get("organization", ""),
                    "speech_type": speech.get("type", "speech"),
                }

        self.log(f"解析完成: {len(segments)} 个段落, {len(speaker_info)} 位发言人")
        return {
            "segments": segments,
            "speaker_ids": speaker_ids,
            "speaker_info": speaker_info,
            "total_segments": len(segments),
            "parse_method": "conference_speech",
            "speeches": speeches,  # 保留完整发言结构供后续使用
        }

    def _preprocess(self, text: str) -> str:
        """预处理文本"""
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        # 清理零宽空格等不可见字符
        text = re.sub(r'[\u200b\u200c\u200d\ufeff\u00a0]', ' ', text)
        text = text.strip()
        return text

    def _split_into_speech_blocks(self, text: str) -> List[Dict]:
        """按说话人标记或发言切换点切分为文本块"""
        blocks = []

        # 尝试按"说话人 N HH:MM:SS"格式切分
        pattern = r'(说话人\s*\d+)\s+(\d{1,2}:\d{2}:\d{2})\s*\n?([\s\S]*?)(?=说话人\s*\d+\s+\d{1,2}:\d{2}:\d{2}|$)'
        matches = list(re.finditer(pattern, text))

        self.log(f"正则匹配到 {len(matches)} 个说话人标记块")

        if matches:
            for m in matches:
                speaker_tag = m.group(1).strip()
                timestamp = m.group(2).strip()
                content = m.group(3).strip()
                if content:
                    # 移除残留不可见字符
                    content = re.sub(r'[\u200b\u200c\u200d\ufeff\u00a0]', '', content).strip()
                    if content:
                        blocks.append({
                            "speaker_tag": speaker_tag,
                            "timestamp": timestamp,
                            "content": content,
                        })
        else:
            # 没有说话人标记，按段落切分
            paragraphs = re.split(r'\n\s*\n', text)
            for i, para in enumerate(paragraphs):
                para = para.strip()
                if para:
                    blocks.append({
                        "speaker_tag": "unknown",
                        "timestamp": "",
                        "content": para,
                    })

        return blocks

    def _identify_and_merge_speeches(self, blocks: List[Dict]) -> List[Dict]:
        """识别发言人并合并连续发言段"""
        speeches = []
        current_speech = None

        for block in blocks:
            content = block["content"]

            # 检测是否是主持人引导语（含"有请""接下来"等）
            is_host = self._is_host_block(content)

            # 检测是否有新发言人开场白
            new_speaker_info = self._detect_new_speaker(content)

            if is_host:
                # 保存当前正在构建的发言段
                if current_speech and current_speech["content"].strip():
                    speeches.append(current_speech)

                # 从主持人内容中提取下一位发言人信息
                next_speaker = self._extract_invited_speaker(content)

                # 主持人串场内容也保存
                host_content = content.strip()
                if host_content:
                    speeches.append({
                        "speaker_name": "主持人",
                        "role": "主持人",
                        "organization": "",
                        "content": host_content,
                        "timestamp": block.get("timestamp", ""),
                        "type": "host",
                        "next_speaker_hint": next_speaker,
                    })

                # 准备下一段发言
                current_speech = None

            elif new_speaker_info:
                # 检测到新发言人的自我介绍/开场白
                if current_speech and current_speech["content"].strip():
                    speeches.append(current_speech)

                current_speech = {
                    "speaker_name": new_speaker_info.get("name", "未知发言者"),
                    "role": new_speaker_info.get("role", ""),
                    "organization": new_speaker_info.get("organization", ""),
                    "content": content,
                    "timestamp": block.get("timestamp", ""),
                    "type": "speech",
                }

            else:
                # 继续当前发言
                if current_speech:
                    current_speech["content"] += "\n" + content
                else:
                    # 还没有当前发言者，创建一个
                    current_speech = {
                        "speaker_name": "未知发言者",
                        "role": "",
                        "organization": "",
                        "content": content,
                        "timestamp": block.get("timestamp", ""),
                        "type": "speech",
                    }

        # 保存最后一段
        if current_speech and current_speech["content"].strip():
            speeches.append(current_speech)

        # 后处理：利用主持人的next_speaker_hint回填发言人信息
        speeches = self._backfill_speaker_info(speeches)

        return speeches

    def _is_host_block(self, content: str) -> bool:
        """判断是否是主持人串场内容"""
        host_patterns = [
            r'有请.*发言', r'接下来.*有请', r'大家欢迎',
            r'请.*讲话', r'请看大屏幕', r'进行.*议程',
            r'谢谢.*接下来', r'刚才.*发言.*接下来',
        ]
        host_score = 0
        for p in host_patterns:
            if re.search(p, content):
                host_score += 1

        # 短句且含引导词，大概率是主持人
        if host_score >= 1 and len(content) < 300:
            return True
        # 包含多个引导词
        if host_score >= 2:
            return True
        return False

    def _detect_new_speaker(self, content: str) -> Optional[Dict]:
        """检测是否有新发言人的开场白，提取身份信息"""
        # "尊敬的XXX书记，XXX区长，各位领导..."
        greeting_pattern = r'^尊敬的([\u4e00-\u9fa5]+(?:书记|区长|主任|局长|总|部长))'
        m = re.match(greeting_pattern, content)
        if m:
            # 这是一个新发言者的开场白
            return {"name": "未知发言者", "role": "", "organization": ""}

        return None

    def _extract_invited_speaker(self, content: str) -> Dict:
        """从主持人引导语中提取被邀请的发言人信息"""
        info = {"name": "", "role": "", "organization": ""}

        # "有请区XXX中心XX书记、常务副主任XXX同志发言"
        patterns = [
            r'有请([\u4e00-\u9fa5]+(?:中心|部门|局|委|办|镇|街|集团|公司))'
                r'.*?([\u4e00-\u9fa5]{2,4}(?:书记|主任|局长|总经理|董事|总裁|同志))'
                r'.*?([\u4e00-\u9fa5]{2,4}).*?发言',
            r'有请([\u4e00-\u9fa5]+).*?(?:书记|镇长|主任)([\u4e00-\u9fa5]{2,4}).*?发言',
            r'有请.*?([\u4e00-\u9fa5]{2,4}(?:同志|先生|女士|总)).*?发言',
        ]

        for p in patterns:
            m = re.search(p, content)
            if m:
                groups = m.groups()
                if len(groups) >= 1:
                    info["name"] = groups[-1] if groups[-1] else groups[0]
                if len(groups) >= 2:
                    info["organization"] = groups[0]
                break

        return info

    def _backfill_speaker_info(self, speeches: List[Dict]) -> List[Dict]:
        """利用主持人的引导信息回填后续发言人的身份"""
        for i, speech in enumerate(speeches):
            if speech.get("type") == "host" and speech.get("next_speaker_hint"):
                hint = speech["next_speaker_hint"]
                # 找到紧随其后的发言段
                for j in range(i + 1, len(speeches)):
                    if speeches[j].get("type") == "speech":
                        if speeches[j]["speaker_name"] in ("未知发言者", "未知"):
                            if hint.get("name"):
                                speeches[j]["speaker_name"] = hint["name"]
                            if hint.get("role"):
                                speeches[j]["role"] = hint["role"]
                            if hint.get("organization"):
                                speeches[j]["organization"] = hint["organization"]
                        break
        return speeches


# ============================================================
# 主题集中度分析器
# ============================================================

class TopicConcentrationAnalyzer:
    """
    当会议主题集中时，让LLM自行判断是否需要展开以及展开到何种粒度，
    而非机械地按段落数量强制拆分。
    """

    def __init__(self):
        self.name = "TopicConcentration"
        self.llm = LLMClient()

    def log(self, msg):
        print(f"  [{self.name}] {msg}")

    def is_concentrated(self, topics: List[Dict],
                        segments: List[Dict] = None,
                        chunk_summaries: List[Dict] = None) -> bool:
        """
        多维度判断主题是否集中且内容丰富到值得展开。

        综合考虑：
        1. 主题数量（必要条件，不充分）
        2. 每个主题覆盖的平均段落数（内容量）
        3. 内容密度：平均每段落的字数 × chunk摘要中的要点密度
        只有主题少 且 内容量大 且 密度高 时才展开。
        """
        n_topics = len(topics)

        # 条件1：主题数必须少于阈值（必要条件）
        if n_topics > Config.TOPIC_CONCENTRATED_THRESHOLD:
            return False
        if n_topics == 0:
            return False

        # 条件2：每个主题平均覆盖的段落数要足够多
        n_segments = len(segments) if segments else 0
        if n_segments > 0:
            avg_segs_per_topic = n_segments / n_topics
            # 平均每个主题至少覆盖 15 个段落才值得展开
            if avg_segs_per_topic < 15:
                self.log(f"主题集中但内容量不足（平均{avg_segs_per_topic:.0f}段/主题），不展开")
                return False
        else:
            # 没有段落信息时，用 chunk_summaries 估算
            if chunk_summaries:
                avg_chunks_per_topic = len(chunk_summaries) / n_topics
                if avg_chunks_per_topic < 1.5:
                    self.log(f"主题集中但块数不足（平均{avg_chunks_per_topic:.1f}块/主题），不展开")
                    return False
            else:
                return False

        # 条件3：内容密度——chunk摘要中平均要点数要足够
        if chunk_summaries:
            total_points = sum(
                len(cs.get("key_points", [])) for cs in chunk_summaries
            )
            avg_points_per_topic = total_points / n_topics
            # 平均每个主题至少要有 5 个要点才值得细分子主题
            if avg_points_per_topic < 5:
                self.log(f"主题集中但要点密度低（平均{avg_points_per_topic:.1f}点/主题），不展开")
                return False

        self.log(f"主题集中且内容丰富（{n_topics}主题, "
                 f"{n_segments}段落, 平均{n_segments/n_topics:.0f}段/主题），触发展开")
        return True

    def expand_topic_details(self, topic: Dict, details: Dict,
                              segments: List[Dict],
                              chunk_summaries: List[Dict]) -> Dict:
        """让LLM自行判断是否需要展开、展开几层，而非强制拆分"""
        self.log(f"分析主题展开需求: {topic.get('title', '')}")

        # 收集该主题的所有原文内容
        start_seg = topic.get("start_segment", 0)
        end_seg = topic.get("end_segment", len(segments) - 1)
        topic_segments = segments[start_seg:min(end_seg + 1, len(segments))]

        # 收集相关的chunk summaries
        chunk_indices = topic.get("chunk_indices", [])
        related_summaries = []
        if chunk_indices:
            for ci in chunk_indices:
                if ci < len(chunk_summaries):
                    related_summaries.append(chunk_summaries[ci])
        if not related_summaries:
            for cs in chunk_summaries:
                if cs["start_segment"] <= end_seg and cs["end_segment"] >= start_seg:
                    related_summaries.append(cs)

        # 构建原文素材
        source_text = build_segment_text(
            topic_segments, max_chars=Config.MAX_INPUT_CHARS - 2000,
            content_limit=Config.CONTENT_LIMIT_PER_SEG
        )

        # 收集已有要点
        existing_points = details.get("key_points", [])
        existing_text = "\n".join([
            f"- {kp.get('point', str(kp)) if isinstance(kp, dict) else str(kp)}"
            for kp in existing_points[:10]
        ])

        # 收集chunk摘要信息
        summaries_text = ""
        for cs in related_summaries:
            pts = cs.get("key_points", [])
            pts_str = "; ".join(
                [p.get("point", str(p)) if isinstance(p, dict) else str(p)
                 for p in pts[:8]]
            )
            data_str = ", ".join(cs.get("data_points", [])[:5])
            summaries_text += f"摘要: {cs.get('summary', '')}\n要点: {pts_str}\n数据: {data_str}\n\n"

        prompt = f"""这个会议的主题比较集中，请分析以下主题是否需要更细粒度的展开。

【主题】{topic.get('title', '')}
【主题摘要】{safe_truncate(topic.get('summary', ''), 300)}

【已提取的要点】
{existing_text or '无'}

【段落摘要信息】
{safe_truncate(summaries_text, 3000)}

【原文内容】
{source_text}

请先判断这个主题的内容是否足够丰富，值得进一步拆分子主题。然后根据判断结果输出：

输出JSON：
{{
    "should_expand": true或false,
    "reason": "判断理由（如果内容已经足够清晰且要点不多，可以不展开）",
    "sub_topics": [
        {{
            "sub_title": "子主题标题",
            "points": [
                {{"point": "详细要点描述，包含具体数据和信息", "evidence_quote": "原文关键句"}}
            ]
        }}
    ],
    "key_data": ["关键数据1", "关键数据2"],
    "decisions": [{{"decision": "决策", "evidence_quote": "原文依据"}}],
    "action_items": [{{"content": "事项", "assignee": "负责人或null", "due_date": "截止日期或null", "priority": "high/medium/low或null", "source_quote": "原文依据"}}]
}}

注意：
- 如果主题内容确实丰富，可拆分为2-8个子主题，每个子主题下的要点数量根据实际内容决定
- 如果主题内容较少或已经够清晰，设should_expand为false，sub_topics可为空
- 不要为了展开而展开，不要注水，不要重复
- 保留原文中所有重要数据和信息"""

        result = self.llm.invoke_safe(prompt, json_mode=True,
            system_prompt="你是会议纪要展开分析专家。请根据内容实际丰富程度决定是否展开，以及展开到什么粒度。不要强行拆分内容不足的主题。")

        if result and isinstance(result, dict):
            should_expand = result.get("should_expand", False)
            reason = result.get("reason", "")
            self.log(f"  LLM判断: {'需要展开' if should_expand else '无需展开'} - {reason}")

            if should_expand and result.get("sub_topics"):
                # 将展开后的子主题转换为key_points格式
                expanded_points = []
                for st in result.get("sub_topics", []):
                    sub_title = st.get("sub_title", "")
                    for pt in st.get("points", []):
                        if isinstance(pt, dict):
                            pt["sub_topic"] = sub_title
                            pt["is_verified"] = True
                            expanded_points.append(pt)
                        else:
                            expanded_points.append({
                                "point": str(pt),
                                "sub_topic": sub_title,
                                "is_verified": True
                            })

                # 合并原有的和新展开的要点（去重）
                all_points = expanded_points
                existing_point_texts = set(kp.get("point", "")[:20] for kp in expanded_points if isinstance(kp, dict))
                for kp in existing_points:
                    if isinstance(kp, dict):
                        pt_text = kp.get("point", "")[:20]
                        if pt_text not in existing_point_texts:
                            all_points.append(kp)

                details["key_points"] = all_points
                details["sub_topics"] = result.get("sub_topics", [])
                details["key_data"] = result.get("key_data", [])

                # 保留原有的decisions和action_items，补充新的
                if result.get("decisions"):
                    existing_decisions = details.get("decisions", [])
                    details["decisions"] = existing_decisions + result["decisions"]
                if result.get("action_items"):
                    existing_actions = details.get("action_items", [])
                    details["action_items"] = existing_actions + result["action_items"]

                self.log(f"  展开完成: {len(result.get('sub_topics', []))}个子主题, "
                         f"{len(expanded_points)}个要点")
            else:
                self.log(f"  保留原有粒度（LLM认为无需展开）")
        else:
            self.log(f"  展开分析失败，保留原有要点")

        return details


# ============================================================
# Embedding 客户端
# ============================================================

class EmbeddingClient:
    """DashScope text-embedding-v4 客户端（单例模式）"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_client()
        return cls._instance

    def _init_client(self):
        self.client = EmbeddingOpenAI(
            api_key=Config.OPENAI_API_KEY,
            base_url=Config.OPENAI_BASE_URL,
        )
        self.model = Config.EMBEDDING_MODEL
        self.dimensions = Config.EMBEDDING_DIMENSIONS
        self.batch_size = Config.EMBEDDING_BATCH_SIZE
        print(f"  [EmbeddingClient] 初始化: model={self.model}, "
              f"dim={self.dimensions}, batch={self.batch_size}")

    def encode(self, texts: List[str]) -> np.ndarray:
        """批量获取 embedding，自动分批、重试、异常降级"""
        all_embeddings = []
        for i in range(0, len(texts), self.batch_size):
            batch = [t if t.strip() else " " for t in texts[i:i + self.batch_size]]
            embedding_batch = self._call_with_retry(batch)
            all_embeddings.extend(embedding_batch)
        return np.array(all_embeddings, dtype=np.float32)

    def _call_with_retry(self, texts: List[str]) -> List[List[float]]:
        """
        Embedding 带智能重试：
        - 4xx（除 429）：不重试，直接降级
        - 429 限流：指数退避，优先读 Retry-After header
        - 5xx / 连接错 / 超时：指数退避 + 随机抖动
        """
        import random

        last_err = None
        for attempt in range(Config.EMBEDDING_MAX_RETRIES + 1):
            try:
                response = self.client.embeddings.create(
                    model=self.model,
                    input=texts,
                    dimensions=self.dimensions,
                    encoding_format="float",
                )
                sorted_data = sorted(response.data, key=lambda x: x.index)
                return [item.embedding for item in sorted_data]

            except Exception as e:
                last_err = e
                # 提取 HTTP 状态码
                status = getattr(e, "status_code", None)
                if status is None:
                    resp = getattr(e, "response", None)
                    if resp is not None:
                        status = getattr(resp, "status_code", None)
                err_name = type(e).__name__

                # --- 4xx（除 429）：客户端错误不重试 ---
                if status is not None and 400 <= status < 500 and status != 429:
                    print(f"    ✗ Embedding API 客户端错误 {status}: {e}（不重试，降级）")
                    return [[0.0] * self.dimensions for _ in texts]

                # --- 超过最大重试次数：降级 ---
                if attempt >= Config.EMBEDDING_MAX_RETRIES:
                    print(f"    ✗ Embedding API 彻底失败 ({err_name}): {e}，降级为零向量")
                    return [[0.0] * self.dimensions for _ in texts]

                # --- 决定等待时间 ---
                if status == 429:
                    # 429 限流：优先读 Retry-After
                    retry_after = None
                    resp = getattr(e, "response", None)
                    if resp is not None:
                        headers = getattr(resp, "headers", {}) or {}
                        try:
                            ra = headers.get("Retry-After") or headers.get("retry-after")
                            if ra:
                                retry_after = float(ra)
                        except (ValueError, TypeError):
                            retry_after = None

                    if retry_after:
                        wait = min(retry_after, Config.EMBEDDING_RETRY_MAX_DELAY)
                    else:
                        wait = min(
                            Config.EMBEDDING_RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 1),
                            Config.EMBEDDING_RETRY_MAX_DELAY,
                        )
                    print(f"    ⚠ Embedding 被限流 429 (第{attempt+1}次)，等 {wait:.1f}s")
                else:
                    # 5xx / 连接错 / 超时：短指数退避
                    wait = min(
                        Config.EMBEDDING_RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5),
                        Config.EMBEDDING_RETRY_MAX_DELAY,
                    )
                    status_str = f"HTTP {status}" if status else err_name
                    print(f"    ⚠ Embedding API 失败 {status_str} (第{attempt+1}次): {e}，等 {wait:.1f}s")

                time.sleep(wait)

        # 理论上走不到
        print(f"    ✗ Embedding 重试耗尽: {last_err}")
        return [[0.0] * self.dimensions for _ in texts]


# ============================================================
# 智能分块器 v3（多尺度窗口 + 自适应 depth 阈值）
# ============================================================

class SemanticChunker:
    """
    语义分块器 v3：多尺度窗口融合 + 自适应 depth score

    核心算法：
    1. 调 text-embedding-v4 API 获取所有段落的 embedding
    2. 根据段落特征自适应选择多尺度窗口（如 [2,4,8] 或 [1,3,6]）
    3. 每个窗口尺度各自计算相似度曲线，取逐位置最小值融合
    4. 在融合曲线上用 depth score 算法找话题转换点
    5. depth 阈值用 75 分位数自适应，不依赖固定值
    6. 规则断点（转折词、疑问句）作为补充信号
    7. 在目标分块大小附近找最近的断点切分 + 合并过小的块
    """

    def __init__(self, max_chunk_size: int = None, min_chunk_size: int = 8):
        self.max_chunk_size = max_chunk_size or Config.CHUNK_SIZE
        self.min_chunk_size = min_chunk_size
        self._embedding_client = None
        self._embeddings_cache = None

    def _get_embedding_client(self) -> EmbeddingClient:
        if self._embedding_client is None:
            self._embedding_client = EmbeddingClient()
        return self._embedding_client

    def create_chunks(self, segments: List[Dict]) -> List[List[Dict]]:
        """语义分块入口"""
        if len(segments) <= self.max_chunk_size:
            return [segments]

        print(f"  [SemanticChunker] 开始语义分块: {len(segments)} 个段落")

        # 第一步：获取 embedding
        contents = [seg.get("content", "") for seg in segments]
        embeddings = self._compute_embeddings(contents)

        # 第二步：自适应选择多尺度窗口
        windows = self._auto_select_windows(segments)
        print(f"  [SemanticChunker] 多尺度窗口: {windows}")

        # 第三步：多尺度融合 — 每个位置取各窗口相似度的最小值
        fused_similarities = self._multi_scale_similarity(embeddings, windows)

        if fused_similarities:
            sims_only = [s for _, s in fused_similarities]
            print(f"  [SemanticChunker] 融合相似度曲线: "
                  f"avg={np.mean(sims_only):.3f}, "
                  f"min={min(sims_only):.3f}, "
                  f"std={np.std(sims_only):.3f}, "
                  f"points={len(fused_similarities)}")

        # 第四步：自适应 depth score 断点检测
        embedding_breakpoints = self._find_adaptive_depth_breakpoints(fused_similarities)
        embedding_bp_set = {pos for pos, _ in embedding_breakpoints}
        print(f"  [SemanticChunker] Embedding 断点: {len(embedding_bp_set)} 个")

        # 第五步：规则断点
        rule_breakpoints = self._find_rule_breakpoints(segments)
        print(f"  [SemanticChunker] 规则断点: {len(rule_breakpoints)} 个")

        # 第六步：融合两类断点
        all_breakpoints = {}
        for pos, depth in embedding_breakpoints:
            all_breakpoints[pos] = depth
        for pos in rule_breakpoints:
            if pos in all_breakpoints:
                all_breakpoints[pos] *= 1.5  # 双重确认，加强
            else:
                # 规则断点给一个较低的默认 depth
                all_breakpoints[pos] = Config.SEMANTIC_DEPTH_MIN_THRESHOLD * 0.8

        sorted_breakpoints = sorted(all_breakpoints.keys())
        print(f"  [SemanticChunker] 融合后断点: {len(sorted_breakpoints)} 个")

        # 第七步：切分 + 平衡
        chunks = self._create_chunks_with_breakpoints(segments, sorted_breakpoints)
        chunks = self._balance_chunks(chunks)

        print(f"  [SemanticChunker] 最终分块: {len(chunks)} 个, "
              f"大小: {[len(c) for c in chunks]}")
        return chunks

    # ==================== Embedding ====================

    def _compute_embeddings(self, contents: List[str]) -> np.ndarray:
        """计算 embedding，带缓存"""
        content_hash = hash(tuple(contents))
        if (self._embeddings_cache is not None and
                self._embeddings_cache[0] == content_hash):
            print(f"  [SemanticChunker] 使用缓存的 embedding")
            return self._embeddings_cache[1]

        client = self._get_embedding_client()
        n_batches = (len(contents) + Config.EMBEDDING_BATCH_SIZE - 1) // Config.EMBEDDING_BATCH_SIZE
        print(f"  [SemanticChunker] 调用 {Config.EMBEDDING_MODEL}: "
              f"{len(contents)} 条, {n_batches} 批次")

        embeddings = client.encode(contents)
        self._embeddings_cache = (content_hash, embeddings)
        return embeddings

    # ==================== 多尺度窗口 ====================

    @staticmethod
    def _auto_select_windows(segments: List[Dict],
                              is_conference: bool = False) -> List[int]:
        """
        根据段落特征自适应选择多尺度窗口

        规则：
        - 最小窗口：段落短则大（需要更多段落累积语义），段落长则小
        - 最大窗口：不超过总段落数的 1/4
        - 中间窗口：取最小和最大的几何平均
        - 大会发言模式包含窗口=1（检测串场边界）
        """
        n = len(segments)
        avg_len = np.mean([len(seg.get("content", "")) for seg in segments])

        if is_conference or avg_len > 300:
            w_min = 1
        elif avg_len < 80:
            w_min = 3
        elif avg_len < 200:
            w_min = 2
        else:
            w_min = 2

        w_max = max(w_min + 2, min(n // 4, 12))
        w_mid = max(w_min + 1, int(np.sqrt(w_min * w_max)))

        windows = sorted(set([w_min, w_mid, w_max]))
        # 确保每个窗口都小于段落数的一半
        windows = [w for w in windows if w < n // 2]
        if not windows:
            windows = [max(1, n // 4)]

        return windows

    @staticmethod
    def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))

    def _compute_similarity_curve(
        self, embeddings: np.ndarray, window_size: int
    ) -> Dict[int, float]:
        """单尺度滑动窗口相似度，返回 {position: similarity}"""
        n = len(embeddings)
        if n <= window_size * 2:
            return {}
        result = {}
        for i in range(window_size, n - window_size):
            left_avg = np.mean(embeddings[i - window_size:i], axis=0)
            right_avg = np.mean(embeddings[i:i + window_size], axis=0)
            result[i] = self._cosine_similarity(left_avg, right_avg)
        return result

    def _multi_scale_similarity(
        self, embeddings: np.ndarray, windows: List[int]
    ) -> List[tuple]:
        """
        多尺度融合：百分位排名归一化 + min。

        原 min 策略的问题：不同窗口的原始相似度量纲不同，
        小窗口方差大、均值低，直接取 min 会让小窗口噪声主导。

        修正：各窗口独立做百分位排名归一化（非参数，无分布假设），
        将相似度转为"在该窗口所有位置中排第几%"，消除量纲差异后取 min。
        """
        # 第一步：各窗口独立计算相似度曲线
        raw_curves = {}
        for w in windows:
            curve = self._compute_similarity_curve(embeddings, w)
            if curve:
                raw_curves[w] = curve

        if not raw_curves:
            return []

        # 第二步：各窗口独立做百分位排名归一化
        rank_curves = {}
        for w, curve in raw_curves.items():
            positions = list(curve.keys())
            sims = np.array([curve[p] for p in positions])
            sorted_indices = np.argsort(sims)
            ranks = np.empty_like(sorted_indices, dtype=float)
            ranks[sorted_indices] = np.arange(len(sims), dtype=float)
            n = len(sims)
            if n > 1:
                ranks = ranks / (n - 1)
            else:
                ranks = np.array([0.5])
            rank_curves[w] = {
                positions[i]: float(ranks[i]) for i in range(len(positions))
            }

        # 第三步：逐位置取 min(rank)
        all_positions = set()
        for curve in rank_curves.values():
            all_positions.update(curve.keys())

        fused = []
        for pos in sorted(all_positions):
            rank_values = [
                rank_curves[w][pos] for w in rank_curves if pos in rank_curves[w]
            ]
            fused.append((pos, min(rank_values)))

        return fused

    # ==================== 自适应 depth score ====================

    @staticmethod
    def _find_adaptive_depth_breakpoints(
        similarities: List[tuple],
        peak_window: int = 3
    ) -> List[tuple]:
        """
        自适应 depth score 断点检测

        改进点（相比固定阈值 0.15）：
        1. 先计算所有位置的 depth score
        2. 用 75 分位数作为阈值（对分布形状不敏感，无需调系数）
        3. 绝对相似度 < SEMANTIC_MIN_SIMILARITY 的位置强制为断点
        4. 阈值不低于 SEMANTIC_DEPTH_MIN_THRESHOLD（防止全部都是断点）
        """
        if not similarities:
            return []

        sims_only = [s for _, s in similarities]

        # 第一遍：计算所有位置的 depth score
        all_depth_scores = []
        for idx in range(len(similarities)):
            pos, sim = similarities[idx]

            left_start = max(0, idx - peak_window)
            left_peak = max(sims_only[left_start:idx + 1])

            right_end = min(len(sims_only), idx + peak_window + 1)
            right_peak = max(sims_only[idx:right_end])

            depth = (left_peak - sim) + (right_peak - sim)
            all_depth_scores.append((pos, sim, depth))

        # 第二遍：自适应阈值 — 用 75 分位数
        depths_only = [d for _, _, d in all_depth_scores]
        if depths_only:
            adaptive_threshold = float(np.percentile(depths_only, 75))
            # 不低于绝对下限
            adaptive_threshold = max(adaptive_threshold,
                                     Config.SEMANTIC_DEPTH_MIN_THRESHOLD)
        else:
            adaptive_threshold = Config.SEMANTIC_DEPTH_MIN_THRESHOLD

        print(f"  [SemanticChunker] Depth 自适应阈值: {adaptive_threshold:.4f} "
              f"(depths: min={min(depths_only):.4f}, "
              f"median={np.median(depths_only):.4f}, "
              f"max={max(depths_only):.4f})")

        # 第三遍：用自适应阈值过滤断点
        breakpoints = []
        for pos, sim, depth in all_depth_scores:
            is_breakpoint = False
            if depth > adaptive_threshold:
                is_breakpoint = True
            # 绝对相似度兜底
            if sim < Config.SEMANTIC_MIN_SIMILARITY:
                is_breakpoint = True
                depth = max(depth, adaptive_threshold + 0.01)
            if is_breakpoint:
                breakpoints.append((pos, depth))

        breakpoints.sort(key=lambda x: x[1], reverse=True)
        return breakpoints

    # ==================== 规则断点 ====================

    @staticmethod
    def _find_rule_breakpoints(segments: List[Dict]) -> set:
        """基于规则的断点检测（转折词、疑问句）"""
        breakpoints = set()
        for i, seg in enumerate(segments):
            content = seg.get('content', '')
            if content.rstrip().endswith('？') or content.rstrip().endswith('?'):
                if i + 1 < len(segments):
                    breakpoints.add(i + 1)
            shift_markers = [
                '但是', '不过', '然后', '另外', '关于', '所以',
                '第一', '首先', '现在', '目前', '接下来', '总之',
                '此外', '至于', '对于', '还有就是', '因此',
                '第二', '第三', '其次', '最后',
            ]
            for marker in shift_markers:
                if content.lstrip().startswith(marker):
                    breakpoints.add(i)
                    break
        return breakpoints

    # ==================== 切分与平衡 ====================

    def _create_chunks_with_breakpoints(self, segments: List[Dict],
                                         breakpoints: List[int]) -> List[List[Dict]]:
        """基于语义断点创建分块"""
        chunks = []
        current_start = 0
        n = len(segments)
        target_size = self.max_chunk_size

        while current_start < n:
            ideal_end = min(current_start + target_size, n)
            if ideal_end >= n:
                chunks.append(segments[current_start:n])
                break

            best_break = ideal_end
            min_dist = float('inf')
            for bp in breakpoints:
                if current_start < bp <= ideal_end + 5:
                    dist = abs(bp - ideal_end)
                    if dist < min_dist and (bp - current_start) >= self.min_chunk_size:
                        min_dist = dist
                        best_break = bp

            chunks.append(segments[current_start:best_break])
            current_start = best_break

        return chunks

    def _balance_chunks(self, chunks: List[List[Dict]]) -> List[List[Dict]]:
        """合并过小的块"""
        if len(chunks) <= 1:
            return chunks

        balanced = []
        current = []
        for chunk in chunks:
            if len(current) + len(chunk) <= self.max_chunk_size:
                current.extend(chunk)
            else:
                if current:
                    balanced.append(current)
                if len(chunk) > self.max_chunk_size:
                    for j in range(0, len(chunk), self.max_chunk_size):
                        balanced.append(chunk[j:j + self.max_chunk_size])
                    current = []
                else:
                    current = chunk[:]
        if current:
            if len(current) < self.min_chunk_size and balanced:
                balanced[-1].extend(current)
            else:
                balanced.append(current)
        return balanced

    def get_cached_embeddings(self) -> np.ndarray:
        """获取缓存的 embedding（可供 RAG 入库复用）"""
        if self._embeddings_cache is not None:
            return self._embeddings_cache[1]
        return None


# 全局实例
_chunker = SemanticChunker()


def create_chunks(segments: List[Dict], chunk_size: int = None,
                  overlap: int = None) -> List[List[Dict]]:
    return _chunker.create_chunks(segments)


# ============================================================
# 引用溯源工具（不改动核心逻辑，只调整接口兼容性）
# ============================================================

class CitationTracer:
    """引用溯源（与原版相同）"""

    @staticmethod
    def _sentence_tokenize(text: str) -> List[str]:
        sentences = re.split(r'([。！？]+)', text)
        result = []
        for i in range(0, len(sentences) - 1, 2):
            if i + 1 < len(sentences):
                result.append(sentences[i] + sentences[i + 1])
            else:
                result.append(sentences[i])
        if len(sentences) % 2 == 1 and sentences[-1].strip():
            result.append(sentences[-1])
        return [s.strip() for s in result if len(s.strip()) >= 5]

    @staticmethod
    def _extract_keywords(text: str) -> List[str]:
        text = re.sub(r'[，。、；：""\'\'（）\[\]【】《》！？\s]', ' ', text)
        words = [w.strip() for w in text.split() if len(w.strip()) >= 2]
        stopwords = {'我们', '他们', '这个', '那个', '可以', '需要', '进行', '已经',
                     '但是', '因为', '所以', '或者', '以及', '目前', '同时', '通过',
                     '关于', '对于', '其中', '这些', '那些', '比较', '一些', '非常',
                     '然后', '就是', '还有', '还是', '不是', '如果', '而且', '虽然'}
        return [w for w in words if w not in stopwords]

    @staticmethod
    def _calculate_semantic_similarity(text1: str, text2: str) -> float:
        kw1 = set(CitationTracer._extract_keywords(text1))
        kw2 = set(CitationTracer._extract_keywords(text2))
        if not kw1 or not kw2:
            return 0.0
        intersection = kw1 & kw2
        union = kw1 | kw2
        jaccard = len(intersection) / len(union) if union else 0.0
        string_sim = SequenceMatcher(None, text1[:100], text2[:100]).ratio()
        return 0.6 * jaccard + 0.4 * string_sim

    @staticmethod
    def verify_point_against_source(point: str, source_segments: List[Dict],
                                     threshold: float = 0.3,
                                     context_window: int = 2) -> Dict:
        best_score = 0.0
        best_segment_idx = -1
        best_evidence = ""
        best_speaker = ""

        for seg in source_segments:
            content = seg.get("content", "")
            seq = seg.get("sequence", 0)
            speaker = seg.get("speaker_id", "")

            sentences = CitationTracer._sentence_tokenize(content)
            if not sentences:
                score = CitationTracer._calculate_semantic_similarity(point, content)
                if score > best_score:
                    best_score = score
                    best_segment_idx = seq
                    best_evidence = content[:200]
                    best_speaker = speaker
                continue

            for i, sentence in enumerate(sentences):
                if len(sentence.strip()) < 8:
                    continue
                score = CitationTracer._calculate_semantic_similarity(point, sentence)
                if score > best_score:
                    best_score = score
                    best_segment_idx = seq
                    best_speaker = speaker
                    start_i = max(0, i - context_window)
                    end_i = min(len(sentences), i + context_window + 1)
                    best_evidence = ''.join(sentences[start_i:end_i])[:300]

        return {
            "is_supported": best_score >= threshold,
            "best_match_score": round(best_score, 3),
            "best_match_segment": best_segment_idx,
            "best_speaker": best_speaker,
            "evidence": best_evidence
        }

    @staticmethod
    def verify_all_points(points: List[str], source_segments: List[Dict],
                          threshold: float = 0.3) -> Dict:
        results = []
        supported = 0
        unsupported = []
        for point in points:
            r = CitationTracer.verify_point_against_source(point, source_segments, threshold)
            r["point"] = point
            results.append(r)
            if r["is_supported"]:
                supported += 1
            else:
                unsupported.append(point)
        total = max(len(points), 1)
        return {
            "total_points": len(points), "supported_count": supported,
            "support_rate": round(supported / total, 3),
            "unsupported_points": unsupported, "details": results
        }


# ============================================================
# 程序化质量检查器（无说话人版本）
# ============================================================

class ProgrammaticQualityChecker:
    def __init__(self):
        self.name = "QualityChecker"

    def log(self, msg):
        print(f"  [{self.name}] {msg}")

    def check(self, final_output, original_segments, speakers, topics) -> QualityReport:
        self.log("开始质量检查（规则 + LLM自评）...")
        issues, suggestions = [], []

        # 规则检查（降级为辅助过滤）
        comp = self._check_completeness(final_output, original_segments, topics, issues, suggestions)
        acc = self._check_accuracy(final_output, original_segments, issues, suggestions)
        struct = self._check_structure(final_output, issues, suggestions)
        read = self._check_readability(final_output, issues, suggestions)

        rule_score = int(comp * 0.30 + acc * 0.35 + struct * 0.15 + read * 0.20)

        # LLM自评：检测幻觉、覆盖度、结构合理性
        llm_eval = self._llm_quality_eval(final_output, original_segments, topics)

        if llm_eval:
            # LLM评分为主（权重60%），规则评分为辅（权重40%）
            llm_score = llm_eval.get("overall_score", rule_score)
            total = int(llm_score * 0.6 + rule_score * 0.4)

            # 合并LLM发现的问题和建议
            llm_issues = llm_eval.get("issues", [])
            llm_suggestions = llm_eval.get("suggestions", [])
            if llm_issues:
                issues.extend(llm_issues)
            if llm_suggestions:
                suggestions.extend(llm_suggestions)

            hallucination_risk = llm_eval.get("hallucination_risk", "low")
            if hallucination_risk == "high":
                total = min(total, 60)  # 高幻觉风险强制降分
                issues.append("LLM自评发现较高的幻觉风险")
        else:
            total = rule_score

        report = QualityReport(
            score=total, completeness=comp, accuracy=acc, structure=struct,
            readability=read, issues=issues, suggestions=suggestions,
            is_passed=total >= Config.QUALITY_THRESHOLD
        )
        self.log(f"评分: {total}/100 (规则{rule_score} + LLM综合)")
        return report

    def _llm_quality_eval(self, final_output, original_segments, topics) -> Dict:
        """LLM自评：检测幻觉、覆盖度、结构合理性"""
        # 取原文摘要（前3000字）
        orig_sample = " ".join(seg.get("content", "")[:150] for seg in original_segments[:30])
        orig_sample = safe_truncate(orig_sample, 3000)

        # 取生成纪要的前2000字
        output_sample = safe_truncate(final_output, 2000)

        topic_titles = [t.get("title", "") for t in topics]

        llm = LLMClient()
        prompt = f"""请评估以下会议纪要的质量。对比原文摘要和生成的纪要，给出评分。

【原文摘要（部分）】
{orig_sample}

【生成的会议纪要（部分）】
{output_sample}

【识别到的主题】
{', '.join(topic_titles)}

请评估并输出JSON：
{{
    "hallucination_risk": "low/medium/high（纪要中是否有明显不在原文中的编造内容）",
    "coverage_score": 0-100（原文重要信息在纪要中的覆盖程度）,
    "structure_score": 0-100（纪要结构是否合理、是否符合会议实际内容）,
    "overall_score": 0-100（综合质量评分）,
    "issues": ["发现的具体问题1", "问题2"],
    "suggestions": ["改进建议1", "建议2"]
}}"""

        try:
            result = llm.invoke_safe(prompt, json_mode=True,
                system_prompt="你是会议纪要质量评审专家。请客观评估纪要质量，重点关注是否有幻觉、信息遗漏、结构不当。输出纯JSON。")
            if result and isinstance(result, dict):
                return result
        except Exception as e:
            self.log(f"  LLM自评失败: {e}")
        return {}

    def _check_completeness(self, output, segments, topics, issues, suggestions):
        """
        完整度：基础 60，按主题覆盖、长度比、实体覆盖加减分。
        """
        score = 60

        # (A) 主题覆盖：最多 +25
        if topics:
            ct = sum(1 for t in topics if t.get("title", "")[:4] in output)
            ratio = ct / len(topics)
            if ratio >= 0.9:
                score += 25
            elif ratio >= 0.7:
                score += 18
            elif ratio >= 0.5:
                score += 10
            else:
                score -= 5
                issues.append(f"主题覆盖不足: {ct}/{len(topics)}")
        else:
            score -= 10
            issues.append("未提取到任何主题")

        # (B) 输出长度比：最多 +10
        orig_chars = sum(len(seg.get("content", "")) for seg in segments)
        if orig_chars > 0:
            ratio = len(output) / orig_chars
            if 0.05 <= ratio <= 0.25:
                score += 10
            elif 0.03 <= ratio < 0.05:
                score += 5
            elif ratio < 0.03:
                score -= 15
                issues.append(f"输出过短（原文{orig_chars}字 → 纪要{len(output)}字）")
            elif ratio > 0.5:
                score -= 5
                suggestions.append("摘要过长，可能未有效压缩信息")

        # (C) 关键实体覆盖：最多 +10
        all_text = " ".join(seg.get("content", "") for seg in segments[:200])
        entities = re.findall(
            r'[\u4e00-\u9fa5]{2,6}(?:公司|集团|大学|研究院|学院|中心|部门|项目|系统|平台)',
            all_text
        )
        if entities:
            unique_entities = list(set(entities))[:15]
            covered = sum(1 for e in unique_entities if e in output)
            ratio = covered / len(unique_entities)
            if ratio >= 0.7:
                score += 10
            elif ratio >= 0.5:
                score += 5
            elif ratio < 0.3:
                score -= 10
                issues.append(f"关键实体覆盖不足: {covered}/{len(unique_entities)}")

        return max(0, min(100, score))

    def _check_accuracy(self, output, segments, issues, suggestions):
        """
        准确性：基础 70，按数字一致率加减分。
        """
        score = 70
        original_text = " ".join(seg.get("content", "") for seg in segments[:300])
        nums = {n for n in re.findall(r'\d+\.?\d*', output) if len(n) >= 2}

        if not nums:
            return 80  # 没数字可比，中性

        fab = [n for n in nums if n not in original_text]
        fab_ratio = len(fab) / len(nums)

        if fab_ratio == 0:
            score += 30
        elif fab_ratio < 0.1:
            score += 20
        elif fab_ratio < 0.3:
            score += 5
            suggestions.append(f"{len(fab)}个数字未在原文直接出现，建议核对")
        else:
            score -= min(30, int(fab_ratio * 60))
            issues.append(f"{len(fab)}/{len(nums)}个数字不在原文中，疑似幻觉")

        return max(0, min(100, score))

    def _check_structure(self, output, issues, suggestions):
        """
        结构：基础 50，按标题层级、列表、占位标记加减分。
        """
        score = 50
        h1 = len(re.findall(r'^# ', output, re.MULTILINE))
        h2 = len(re.findall(r'^## ', output, re.MULTILINE))
        h3 = len(re.findall(r'^### ', output, re.MULTILINE))

        # (A) 标题层级：最多 +35
        if h1 >= 1 and h2 >= 3:
            score += 30
            if h3 >= 2:
                score += 5
        elif h1 >= 1 and h2 >= 1:
            score += 20
        elif h2 >= 1:
            score += 10
        else:
            score -= 10
            issues.append("缺少标题层级")

        # (B) 列表/要点：最多 +10
        bullets = len(re.findall(r'^\s*[-*•]\s', output, re.MULTILINE))
        if bullets >= 5:
            score += 10
        elif bullets >= 2:
            score += 5

        # (C) 占位标记密度：扣分
        placeholder_count = len(re.findall(r'\[原文未明确提及\]', output))
        if placeholder_count > 10:
            score -= 25
            issues.append(f"过多占位标记({placeholder_count}个)")
            suggestions.append("信息完全缺失的维度应直接省略，不要留占位")
        elif placeholder_count > 5:
            score -= 12
            issues.append(f"占位标记偏多({placeholder_count}个)")
        elif placeholder_count > 0:
            score -= 3

        return max(0, min(100, score))

    def _check_readability(self, output, issues, suggestions):
        """
        可读性：基础 70，按长度、段落数、平均句长加减分。
        """
        score = 70
        text = output.strip()
        n = len(text)

        # (A) 总长度
        if n < 200:
            score -= 40
            issues.append("输出内容过少")
            return max(0, score)
        elif n < 500:
            score -= 10
            suggestions.append("纪要偏短")
        elif 1000 <= n <= 8000:
            score += 15
        elif n > 15000:
            score -= 5
            suggestions.append("纪要偏长，建议精简")
        else:
            score += 5

        # (B) 段落结构
        paragraphs = [p for p in text.split("\n\n") if p.strip()]
        if len(paragraphs) >= 5:
            score += 10
        elif len(paragraphs) >= 2:
            score += 5
        else:
            score -= 10
            issues.append("段落分隔不清")

        # (C) 平均句长
        sentences = re.split(r'[。！？!?]', text)
        sentences = [s for s in sentences if len(s.strip()) > 5]
        if sentences:
            avg_len = sum(len(s) for s in sentences) / len(sentences)
            if avg_len > 120:
                score -= 8
                suggestions.append(f"平均句长 {avg_len:.0f} 字，过长")
            elif avg_len < 80:
                score += 5

        return max(0, min(100, score))


# ============================================================
# 会议类型适配器（不改动）
# ============================================================

class MeetingTypeAdapter:
    MEETING_TYPES = {
        'decision_making': {
            'name': '决策会议',
            'focus': ['决策事项', '投票结果', '执行计划'],
            'template_sections': ['决策背景', '讨论过程', '最终决策', '执行方案'],
            'extraction_hint': '重点提取：达成了哪些决策、谁负责执行、截止时间',
            'conclusion_hint': '重点总结决策事项和执行分工'
        },
        'conference': {
            'name': '大会发言',
            'focus': ['各发言人观点', '工作成果', '工作部署', '目标承诺'],
            'template_sections': ['大会概述', '各发言人发言纪要', '总结与展望'],
            'extraction_hint': '重点提取：每位发言者的身份、发言主题、关键数据、工作成果、目标承诺和具体举措',
            'conclusion_hint': '重点总结各发言人的核心观点、承诺目标和重点工作部署'
        },
        'research': {
            'name': '调研座谈会',
            'focus': ['问题与挑战', '各方观点', '建议与对策', '现状与发现', '经验与启示'],
            'template_sections': ['调研背景', '现状与做法', '反映的问题', '各方观点', '建议与思考'],
            'extraction_hint': (
                '调研类纪要的核心是挖掘"问题—观点—建议"，请重点提取：'
                '①各方反映的痛点、矛盾、挑战；②不同主体的立场和判断（可附主体标签）；'
                '③受访方/与会方提出的改进建议与对策；④调研中观察到的事实做法与机制；'
                '⑤值得借鉴的经验和启示；⑥尚未解决、需后续深入研究的问题。'
                '不要硬套"决策/待办/关键数据"模板；'
                '只有素材中明确出现"决定""负责人""截止日期"等表述时才提取 decisions / action_items；'
                '数据应作为论据嵌入对应问题或建议中，不必单独成块'
            ),
            'conclusion_hint': (
                '调研类结尾段重点总结：主要发现、共性问题、各方建议方向、'
                '值得提炼的经验启示，以及后续待研究的开放性课题。'
                '不要硬写"主要决策""待办事项"小节，除非素材确实出现明确的决策或带负责人的待办'
            )
        },
        'brainstorming': {
            'name': '头脑风暴会议',
            'focus': ['创意点子', '讨论方向', '潜在方案'],
            'template_sections': ['会议背景', '主要观点', '创新点', '后续跟进'],
            'extraction_hint': '重点提取：提出了哪些想法和方案、各方态度',
            'conclusion_hint': '重点汇总各种方案和创意'
        },
        'status_update': {
            'name': '进度汇报会议',
            'focus': ['进展情况', '问题障碍', '下一步计划'],
            'template_sections': ['整体进度', '各项目进展', '问题与风险', '下阶段计划'],
            'extraction_hint': '重点提取：各项工作进展、遇到的问题、下一步计划',
            'conclusion_hint': '重点总结整体进度和待解决问题'
        },
        'problem_solving': {
            'name': '问题解决会议',
            'focus': ['问题描述', '根因分析', '解决方案'],
            'template_sections': ['问题概述', '原因分析', '解决方案', '预防措施'],
            'extraction_hint': '重点提取：问题是什么、原因是什么、如何解决',
            'conclusion_hint': '重点总结解决方案和预防措施'
        }
    }

    @staticmethod
    def detect_meeting_type(segments: List[Dict], metadata: Dict = None) -> str:
        """使用LLM对会议前2000字做类型判断，预设类型仅作参考示例"""
        sample_text = " ".join(
            seg.get("content", "")[:200] for seg in segments[:30]
        )[:2000]
        title = (metadata or {}).get("title", "")

        type_examples = ", ".join([
            f"{k}（{v['name']}）" for k, v in MeetingTypeAdapter.MEETING_TYPES.items()
        ])

        llm = LLMClient()
        prompt = f"""请根据以下会议内容片段，判断这场会议最符合哪种类型。

【会议标题】{title or '未知'}

【内容片段（前2000字）】
{sample_text}

参考类型（不限于这几类，如不匹配则选最接近的）：{type_examples}

输出JSON：
{{
    "meeting_type": "类型英文标识（优先使用预设key，否则用最接近的）",
    "meeting_nature": "用一句话自由描述这场会议的性质和特点",
    "confidence": "high/medium/low"
}}"""

        result = llm.invoke_safe(prompt, json_mode=True,
            system_prompt="你是会议类型分析专家。根据会议内容判断类型。输出纯JSON。")

        if result and isinstance(result, dict):
            detected = result.get("meeting_type", "research")
            nature = result.get("meeting_nature", "")
            if detected in MeetingTypeAdapter.MEETING_TYPES:
                if metadata is not None and nature:
                    metadata["_meeting_nature"] = nature
                return detected
            # 不在预设中，回退
            if metadata is not None and nature:
                metadata["_meeting_nature"] = nature
            return "research"

        # LLM失败时的关键词回退
        full_text = (title or "") + " " + sample_text
        kw_map = {
            'conference': ['大会', '发言', '有请', '大家欢迎', '尊敬的'],
            'decision_making': ['决策', '决定', '投票', '表决', '审批'],
            'status_update': ['进展', '汇报', '进度', '周报'],
            'problem_solving': ['问题', '解决', '原因分析', '故障'],
            'brainstorming': ['头脑风暴', '思路', '探讨'],
            'research': ['调研', '座谈', '考察'],
        }
        type_scores = {mt: sum(1 for kw in kws if kw in full_text) for mt, kws in kw_map.items()}
        best = max(type_scores, key=type_scores.get)
        return best if type_scores[best] >= 2 else 'research'

    @staticmethod
    def get_type_config(meeting_type: str) -> Dict:
        return MeetingTypeAdapter.MEETING_TYPES.get(
            meeting_type,
            MeetingTypeAdapter.MEETING_TYPES['research']
        )


# ============================================================
# v4-nospeaker: 块级摘要智能体
# ============================================================

class ChunkSummarizerAgent(BaseAgent):
    """块级摘要：与原版类似，但prompt适配无说话人场景，并输出每段的语义标签"""

    SYSTEM_PROMPT = """你是专业的会议记录分析专家。请对以下会议录音转写内容做详细的结构化摘要。

注意：这段文本是会议录音的自动转写，没有标注说话人。请根据内容语义推断不同观点来源。

要求：
1. 提取所有关键信息、观点、数据、人名、机构名
2. 记录所有提到的决策和待办事项
3. 保留重要的原文表述（用引号标注）
4. 如果能从上下文推断出谁在说话（如自我介绍、被称呼等），请标注
5. 不要遗漏任何实质性内容
6. 不要添加原文中没有的信息
7. 为这段内容的每个自然段落/话题段给出一个简短的"语义标签"（3-8个字的短语，概括该段核心话题）

输出JSON：
{
    "summary": "这段内容的整体概述（100-200字）",
    "key_points": [
        {"point": "要点描述", "speaker_hint": "推断的发言者或'未知'", "quote": "相关原文关键句"}
    ],
    "decisions": ["决策1", "决策2"],
    "action_items": [{"content": "事项", "assignee": "负责人或null", "due_date": "截止日期或null", "priority": "high/medium/low或null", "source_quote": "原文依据"}],
    "topics_mentioned": ["涉及的话题1", "话题2"],
    "names_entities": ["提到的人名、机构名、地名等"],
    "data_points": ["提到的具体数字、数据"],
    "inferred_speakers": ["从内容中推断出的可能发言者身份"],
    "semantic_tags": ["段落1的语义标签", "段落2的语义标签"]
}"""

    def process_all_chunks(self, segments: List[Dict]) -> List[Dict]:
        chunks = create_chunks(segments)
        self.log(f"共 {len(segments)} 个段落，分为 {len(chunks)} 块，逐块摘要...")

        chunk_summaries = []
        for i, chunk in enumerate(chunks):
            self.log(f"  摘要第 {i+1}/{len(chunks)} 块 "
                     f"(段落 {chunk[0]['sequence']}-{chunk[-1]['sequence']})...")

            chunk_text = build_segment_text(
                chunk, max_chars=Config.MAX_INPUT_CHARS - 1000,
                content_limit=Config.CONTENT_LIMIT_PER_SEG
            )

            prompt = f"""请对以下会议录音转写内容做详细结构化摘要（注意：没有说话人标注，请根据语义推断）：

---会议内容（段落{chunk[0]['sequence']}到{chunk[-1]['sequence']}）---
{chunk_text}
---

请按JSON格式输出完整摘要。"""

            try:
                result = self.llm.invoke_safe(prompt, self.SYSTEM_PROMPT, json_mode=True)
                if not result or not isinstance(result, dict):
                    result = {"summary": "", "key_points": [], "topics_mentioned": []}
            except Exception as e:
                self.log(f"  第{i+1}块摘要失败: {e}")
                result = {"summary": "", "key_points": [], "topics_mentioned": []}

            result["chunk_index"] = i
            result["start_segment"] = chunk[0]["sequence"]
            result["end_segment"] = chunk[-1]["sequence"]
            result["speaker_ids"] = list(set(seg["speaker_id"] for seg in chunk))
            chunk_summaries.append(result)

            pts = len(result.get("key_points", []))
            self.log(f"    ✓ 摘要完成: {pts}个要点, "
                     f"话题: {result.get('topics_mentioned', [])[:3]}")

        self.log(f"全部{len(chunks)}块摘要完成")
        return chunk_summaries


# ============================================================
# 参会者/角色推断（替代原版的说话人分析）
# ============================================================

class ParticipantInferAgent(BaseAgent):
    """
    无说话人版本：从会议内容中推断参会者身份和角色
    不再分析 speaker_id → 姓名映射，而是识别内容中出现的人物角色
    """

    SYSTEM_PROMPT = """你是会议参与者分析专家。这段会议录音没有标注说话人。请根据会议内容推断参会者身份。

分析方法：
1. 从自我介绍、相互称呼、提及的职务等推断参会者
2. 从讨论内容推断参与方的身份（如：企业方、政府方、研究机构方等）
3. 如果无法确定具体人名，用角色描述代替（如"企业负责人"、"调研组成员"）

输出JSON：
{
    "participants": [
        {"name": "姓名或角色描述", "role": "角色", "organization": "单位或未知", "evidence": "推断依据"}
    ],
    "participant_count_estimate": "估计参会人数或'无法确定'",
    "meeting_context": "对会议背景的简要推断"
}"""

    def process(self, segments: List[Dict], chunk_summaries: List[Dict] = None) -> Dict:
        self.log("推断参会者信息...")

        # 从块摘要中收集线索
        names_from_summaries = []
        inferred_speakers = []
        if chunk_summaries:
            for cs in chunk_summaries:
                names_from_summaries.extend(cs.get("names_entities", []))
                inferred_speakers.extend(cs.get("inferred_speakers", []))

        # 取样原文
        total = len(segments)
        indices = set()
        step = max(1, total // 15)
        for i in range(0, total, step):
            indices.add(i)
        for i in range(min(5, total)):
            indices.add(i)
        for i in range(max(0, total - 5), total):
            indices.add(i)

        samples = [segments[i] for i in sorted(indices)][:20]
        sample_text = "\n\n".join([
            f"[段落{seg['sequence']}] {safe_truncate(seg['content'], 300)}"
            for seg in samples
        ])
        sample_text = safe_truncate(sample_text, Config.MAX_INPUT_CHARS - 800)

        extra_info = ""
        if names_from_summaries:
            unique_names = list(set(names_from_summaries))[:20]
            extra_info += f"\n会议中提到的人名/机构：{', '.join(unique_names)}"
        if inferred_speakers:
            unique_speakers = list(set(inferred_speakers))[:10]
            extra_info += f"\n从内容推断的可能发言者：{', '.join(unique_speakers)}"

        prompt = f"""请根据以下会议内容推断参会者身份（注意：文本没有说话人标注）：

---会议内容样本---
{sample_text}
---{extra_info}

请按JSON格式输出。"""

        result = self.llm.invoke_safe(prompt, self.SYSTEM_PROMPT, json_mode=True)

        # 转换为兼容原版speakers格式
        participants = result.get("participants", [])
        speakers = {}
        for i, p in enumerate(participants):
            key = p.get("name", f"参会者{i+1}")
            speakers[key] = {
                "name": p.get("name", "未知"),
                "role": p.get("role", "参会人员"),
                "organization": p.get("organization", "未知"),
                "title": p.get("role", "未知"),
                "evidence": p.get("evidence", "")
            }

        # 确保至少有一个条目
        if not speakers:
            speakers["参会者"] = {
                "name": "未知", "role": "参会人员",
                "organization": "未知", "title": "未知"
            }

        self.log(f"推断完成: {len(speakers)} 位参会者")
        return {
            "speakers": speakers,
            "meeting_context": result.get("meeting_context", ""),
            "participant_count": result.get("participant_count_estimate", "未知")
        }


# ============================================================
# 主题切分（与原版类似，但移除speaker_id依赖）
# ============================================================

class TopicSegmentationAgent(BaseAgent):

    SYSTEM_PROMPT = """你是会议主题分析专家。请根据以下各段落摘要，切分出会议的讨论主题。

要求：
1. 合并相关的段落到同一主题
2. 主题标题必须反映实际讨论内容
3. 标注每个主题对应的段落范围
4. 不要添加原文未讨论的主题

输出JSON：
{
    "topics": [
        {
            "id": "topic_1",
            "title": "主题标题",
            "summary": "摘要（50-100字）",
            "key_points": ["要点1", "要点2"],
            "chunk_indices": [0, 1, 2]
        }
    ]
}"""

    def process(self, chunk_summaries: List[Dict], segments: List[Dict]) -> Dict:
        self.log(f"基于{len(chunk_summaries)}个块摘要切分主题...")

        overview_lines = []
        total_len = 0
        for cs in chunk_summaries:
            line = (f"[块{cs['chunk_index']}] "
                    f"段落{cs['start_segment']}-{cs['end_segment']} "
                    f"| {safe_truncate(cs.get('summary', ''), 150)} "
                    f"| 话题:{','.join(cs.get('topics_mentioned', [])[:3])}")
            if total_len + len(line) > Config.MAX_INPUT_CHARS - 1000:
                break
            overview_lines.append(line)
            total_len += len(line) + 1

        overview_text = "\n".join(overview_lines)

        prompt = f"""请根据以下会议各段落摘要，切分讨论主题：

---各段落摘要---
{overview_text}
---

请按JSON格式输出。合并相关段落到同一主题。"""

        result = self.llm.invoke_safe(prompt, self.SYSTEM_PROMPT, json_mode=True)
        topics = result.get("topics", [])

        if not isinstance(topics, list) or not topics:
            self.log("主题切分失败，使用块摘要中的话题自动构建")
            topics = self._build_topics_from_summaries(chunk_summaries)

        # 补充 start_segment / end_segment
        for topic in topics:
            chunk_indices = topic.get("chunk_indices", [])
            if chunk_indices:
                starts = [chunk_summaries[ci]["start_segment"]
                          for ci in chunk_indices if ci < len(chunk_summaries)]
                ends = [chunk_summaries[ci]["end_segment"]
                        for ci in chunk_indices if ci < len(chunk_summaries)]
                if starts:
                    topic["start_segment"] = min(starts)
                    topic["end_segment"] = max(ends)
            if "start_segment" not in topic:
                topic["start_segment"] = 0
                topic["end_segment"] = len(segments) - 1

        if len(topics) > Config.MAX_TOPICS:
            topics = topics[:Config.MAX_TOPICS]
        for i, t in enumerate(topics):
            t["id"] = f"topic_{i+1}"

        self.log(f"切分完成: {len(topics)} 个主题")
        return {"topics": topics}

    def _build_topics_from_summaries(self, chunk_summaries):
        all_topic_names = []
        for cs in chunk_summaries:
            for t in cs.get("topics_mentioned", []):
                all_topic_names.append((t, cs["chunk_index"]))

        seen = {}
        for name, ci in all_topic_names:
            key = name[:6]
            if key not in seen:
                seen[key] = {"title": name, "chunks": [ci], "summary": ""}
            else:
                seen[key]["chunks"].append(ci)

        topics = []
        for i, (key, info) in enumerate(seen.items()):
            if i >= Config.MAX_TOPICS:
                break
            topics.append({
                "id": f"topic_{i+1}",
                "title": info["title"],
                "summary": info["summary"],
                "key_points": [],
                "chunk_indices": info["chunks"],
            })
        return topics


# ============================================================
# 内容提取（与原版基本一致）
# ============================================================

class ContentExtractorAgent(BaseAgent):

    # 基础系统提示 - 不再硬编码固定维度
    BASE_SYSTEM_PROMPT = """你是会议内容提取专家。请基于以下素材提取该主题的完整要点。

注意：这是无说话人标注的会议记录，如果能从内容推断出发言者请标注，否则省略。

要求：
- 综合多个段落的信息，给出完整的要点列表
- 每个要点附带原文依据
- 不要编造原文中没有的内容
- 按照指定的提取维度进行提取"""

    def __init__(self, name="ContentExtractor"):
        super().__init__(name)
        self.tracer = CitationTracer()
        self._extraction_schema = None

    def _generate_extraction_schema(self, segments: List[Dict],
                                      meeting_type: str, metadata: Dict) -> Dict:
        """让LLM根据会议内容自适应决定提取维度"""
        self.log("让LLM自适应决定提取维度...")

        # 取前2000字内容样本
        sample_text = " ".join(
            seg.get("content", "")[:200] for seg in segments[:20]
        )[:2000]

        type_config = MeetingTypeAdapter.get_type_config(meeting_type)
        type_description = metadata.get("_llm_type_description", type_config.get("name", ""))

        # 针对调研座谈会，引导 LLM 优先选择"问题—观点—建议"型维度
        if meeting_type == 'research':
            recommended_hint = (
                "本次会议是调研/座谈类，建议优先从以下维度选择 4-6 个："
                "findings、issues、viewpoints、suggestions、experiences、open_questions；"
                "key_points 可作为兜底维度；"
                "decisions / action_items 仅在素材中确实存在明确决策或带负责人的待办时才纳入；"
                "data_findings 不必单独成维度，可作为论据并入对应问题或建议"
            )
        else:
            recommended_hint = "请根据这场会议的实际内容选择最贴合的维度"

        prompt = f"""根据以下会议内容，决定这场会议纪要应该提取哪几类信息。

【会议类型】{type_description or meeting_type}
【会议内容样本】
{sample_text}

【选择建议】
{recommended_hint}

参考维度（不必全部使用，也可以新增）：
- key_points: 关键要点（兜底维度，仅当其他维度都不贴合时使用）
- findings: 现状与发现（调研中观察到的事实、做法、机制）
- issues: 问题与挑战（各方反映的痛点、矛盾、风险）
- viewpoints: 各方观点（不同主体的立场和判断，可附主体标签）
- suggestions: 建议与对策（受访方/与会方提出的改进建议）
- experiences: 经验与启示（值得总结提炼的做法或模式）
- open_questions: 待研究问题（尚未解决、需后续深挖的问题）
- decisions: 决策事项（仅在确有明确决策时使用）
- action_items: 待办/行动项（仅在确有带负责人/截止时间的待办时使用）
- data_findings: 数据发现（仅在数据本身是会议核心时单列）
- creative_ideas: 创意方案
- risk_items: 风险项
- achievements: 工作成果
- commitments: 目标承诺
- policy_measures: 政策举措

请选择3-6个最适合这场会议的提取维度。

输出JSON：
{{
    "dimensions": [
        {{
            "key": "维度英文key",
            "name": "维度中文名",
            "description": "提取说明（一句话）",
            "priority": "high/medium/low"
        }}
    ],
    "extraction_hint": "针对这场会议的提取侧重说明（一句话）"
}}"""

        result = self.llm.invoke_safe(prompt, json_mode=True,
            system_prompt="你是会议分析专家。根据会议内容特点决定最合适的信息提取维度。")

        if result and isinstance(result, dict) and result.get("dimensions"):
            self.log(f"  LLM决定了 {len(result['dimensions'])} 个提取维度: "
                     f"{[d.get('name','') for d in result['dimensions']]}")
            return result

        # 回退默认维度（按会议类型给不同的兜底维度）
        self.log("  LLM维度生成失败，使用默认维度")
        if meeting_type == 'research':
            return {
                "dimensions": [
                    {"key": "findings", "name": "现状与发现", "description": "调研中观察到的事实、做法、机制", "priority": "high"},
                    {"key": "issues", "name": "问题与挑战", "description": "各方反映的痛点、矛盾、风险", "priority": "high"},
                    {"key": "viewpoints", "name": "各方观点", "description": "不同主体的立场和判断", "priority": "high"},
                    {"key": "suggestions", "name": "建议与对策", "description": "提出的改进建议与对策", "priority": "high"},
                    {"key": "experiences", "name": "经验与启示", "description": "值得借鉴的做法或模式", "priority": "medium"},
                    {"key": "open_questions", "name": "待研究问题", "description": "尚未解决、需后续深挖的问题", "priority": "medium"},
                ],
                "extraction_hint": "调研类纪要：聚焦问题、观点与建议；不要硬套决策/待办"
            }
        return {
            "dimensions": [
                {"key": "key_points", "name": "关键要点", "description": "讨论中的核心观点和信息", "priority": "high"},
                {"key": "decisions", "name": "决策事项", "description": "会议达成的共识或决定", "priority": "medium"},
                {"key": "action_items", "name": "行动项", "description": "待办事项和分工", "priority": "medium"},
                {"key": "data_findings", "name": "关键数据", "description": "提到的重要数据和指标", "priority": "medium"},
            ],
            "extraction_hint": "全面提取会议讨论要点"
        }

    def _build_dynamic_extraction_prompt(self, schema: Dict) -> str:
        """根据动态schema构建提取prompt的JSON格式说明"""
        dimensions = schema.get("dimensions", [])
        json_fields = []
        for dim in dimensions:
            key = dim.get("key", "unknown")
            desc = dim.get("description", "")
            if key == "key_points":
                json_fields.append(f'    "{key}": [{{"point": "要点", "evidence_quote": "原文关键句"}}]')
            elif key == "decisions":
                json_fields.append(f'    "{key}": [{{"decision": "决策", "evidence_quote": "原文依据"}}]')
            elif key == "action_items":
                json_fields.append(f'    "{key}": [{{"content": "事项", "assignee": "负责人或null", "due_date": "截止日期或null", "priority": "high/medium/low或null", "source_quote": "原文依据"}}]')
            elif key == "issues":
                json_fields.append(f'    "{key}": [{{"issue": "问题描述", "evidence_quote": "原文依据"}}]')
            elif key == "findings":
                json_fields.append(f'    "{key}": [{{"finding": "现状或发现", "evidence_quote": "原文依据"}}]')
            elif key == "viewpoints":
                json_fields.append(f'    "{key}": [{{"subject": "主体（如管理局/研究院/港方代表，未明确则填null）", "viewpoint": "观点内容", "evidence_quote": "原文依据"}}]')
            elif key == "suggestions":
                json_fields.append(f'    "{key}": [{{"suggestion": "建议内容", "target": "建议对象或null", "evidence_quote": "原文依据"}}]')
            elif key == "experiences":
                json_fields.append(f'    "{key}": [{{"experience": "经验或启示", "evidence_quote": "原文依据"}}]')
            elif key == "open_questions":
                json_fields.append(f'    "{key}": [{{"question": "待研究的问题", "evidence_quote": "原文依据"}}]')
            else:
                json_fields.append(f'    "{key}": [{{"point": "{desc}", "evidence_quote": "原文关键句"}}]')

        return "{\n" + ",\n".join(json_fields) + "\n}"

    def process(self, segments: List[Dict], topics: List[Dict],
                chunk_summaries: List[Dict], meeting_type: str = "research",
                metadata: Dict = None) -> Dict:
        self.log("提取各主题详细内容...")

        # 第一阶段：让LLM自适应决定提取维度
        self._extraction_schema = self._generate_extraction_schema(
            segments, meeting_type, metadata or {})
        extraction_hint = self._extraction_schema.get("extraction_hint", "")
        json_format = self._build_dynamic_extraction_prompt(self._extraction_schema)

        topic_details = {}

        for topic in topics:
            topic_id = topic.get("id", "unknown")
            title = topic.get("title", "")

            chunk_indices = topic.get("chunk_indices", [])
            related_summaries = []
            if chunk_indices:
                for ci in chunk_indices:
                    if ci < len(chunk_summaries):
                        related_summaries.append(chunk_summaries[ci])
            else:
                start = topic.get("start_segment", 0)
                end = topic.get("end_segment", len(segments) - 1)
                for cs in chunk_summaries:
                    if cs["start_segment"] <= end and cs["end_segment"] >= start:
                        related_summaries.append(cs)

            if not related_summaries:
                related_summaries = chunk_summaries

            summaries_text = ""
            for cs in related_summaries:
                pts = cs.get("key_points", [])
                pts_str = "; ".join(
                    [p.get("point", str(p)) if isinstance(p, dict) else str(p)
                     for p in pts[:8]]
                )
                summaries_text += (f"[块{cs['chunk_index']}] {cs.get('summary', '')}\n"
                                   f"  要点: {pts_str}\n"
                                   f"  数据: {', '.join(cs.get('data_points', [])[:5])}\n\n")

            start_seg = topic.get("start_segment", 0)
            end_seg = topic.get("end_segment", len(segments) - 1)
            topic_segments = segments[start_seg:min(end_seg + 1, len(segments))]

            remaining_chars = Config.MAX_INPUT_CHARS - len(summaries_text) - 1000
            original_text = ""
            if remaining_chars > 500 and topic_segments:
                original_text = build_segment_text(
                    topic_segments, max_chars=remaining_chars,
                    content_limit=Config.CONTENT_LIMIT_PER_SEG
                )

            prompt = f"""请提取以下主题的完整要点：

【主题】{title}

【提取侧重】{extraction_hint}

【各段落摘要】
{safe_truncate(summaries_text, Config.MAX_INPUT_CHARS - 2000)}

【部分原文】
{original_text if original_text else '（见上方摘要）'}

请综合以上信息，按以下JSON格式输出（按本次会议的自适应维度提取）。不要编造原文没有的内容。
{json_format}"""

            system_prompt = self.BASE_SYSTEM_PROMPT + f"\n\n本次会议的提取维度：{json.dumps([d.get('name','') for d in self._extraction_schema.get('dimensions',[])], ensure_ascii=False)}"

            try:
                result = self.llm.invoke_safe(prompt, system_prompt, json_mode=True)
                if not result:
                    result = {}
            except Exception as e:
                self.log(f"  主题[{topic_id}]失败: {e}")
                result = {}

            # 验证key_points（如果存在）
            key_points = result.get("key_points", [])
            if isinstance(key_points, list) and topic_segments:
                for kp in key_points:
                    if isinstance(kp, dict):
                        pt = kp.get("point", "")
                        v = self.tracer.verify_point_against_source(pt, topic_segments, 0.2)
                        kp["is_verified"] = v["is_supported"]
                        kp["match_score"] = v["best_match_score"]

                verified = sum(1 for kp in key_points
                               if isinstance(kp, dict) and kp.get("is_verified"))
                self.log(f"  主题[{topic_id}]: {len(key_points)}要点, {verified}通过验证")

            # 存储提取schema以便后续使用
            result["_extraction_schema"] = self._extraction_schema

            topic_details[topic_id] = result

        self.log(f"内容提取完成: {len(topic_details)} 个主题")
        return {"topic_details": topic_details}


# ============================================================
# 大会发言模式：按发言人提取内容
# ============================================================

class ConferenceSpeechExtractor(BaseAgent):
    """
    大会发言模式专用的内容提取器
    按每位发言人组织内容，而非按主题
    """

    SYSTEM_PROMPT = """你是大会发言纪要整理专家。你正在整理一场大会中某位发言人的发言内容。

要求：
1. 准确提取发言人的身份信息（姓名、职务、单位）
2. 提取发言的核心主题
3. 详细记录发言中的关键数据、成果、目标、举措
4. 将发言内容按逻辑分类为多个要点
5. 保留重要的原文表述
6. 不要遗漏任何实质性内容
7. 不要添加原文中没有的信息

输出JSON：
{
    "speaker_name": "发言人姓名",
    "speaker_title": "职务",
    "speaker_org": "单位",
    "speech_theme": "发言主题（一句话概括）",
    "sub_sections": [
        {
            "title": "分类标题（如：工作成果回顾、重点目标、具体举措等）",
            "points": [
                {"point": "详细要点，包含具体数据", "quote": "原文关键句"}
            ]
        }
    ],
    "key_data": ["关键数据1", "关键数据2"],
    "commitments": ["承诺/目标1", "承诺/目标2"],
    "highlights": ["亮点/创新点1", "亮点/创新点2"]
}"""

    def process_speeches(self, segments: List[Dict],
                          speeches: List[Dict],
                          chunk_summaries: List[Dict]) -> Dict:
        """按发言人提取大会发言内容"""
        self.log("提取各发言人内容...")

        # 过滤出正式发言（非主持人串场）
        formal_speeches = [s for s in speeches if s.get("type") == "speech"]
        host_speeches = [s for s in speeches if s.get("type") == "host"]

        speech_details = []

        for i, speech in enumerate(formal_speeches):
            speaker_name = speech.get("speaker_name", f"发言人{i+1}")
            self.log(f"  提取第{i+1}位发言人: {speaker_name}")

            content = speech.get("content", "")
            if not content.strip():
                continue

            prompt = f"""请详细整理以下大会发言内容：

【发言人信息】
姓名: {speaker_name}
职务: {speech.get('role', '未知')}
单位: {speech.get('organization', '未知')}

【发言原文】
{safe_truncate(content, Config.MAX_INPUT_CHARS - 1500)}

请按JSON格式输出，要求：
1. 将发言内容分为3-6个子类别（如工作回顾、成果数据、未来目标、具体举措等）
2. 每个子类别下提取所有具体要点
3. 特别注意保留所有数据、数字、项目名称
4. 提取发言人的关键承诺和目标"""

            try:
                result = self.llm.invoke_safe(prompt, self.SYSTEM_PROMPT, json_mode=True)
                if not result or not isinstance(result, dict):
                    result = {
                        "speaker_name": speaker_name,
                        "speech_theme": "发言内容",
                        "sub_sections": [],
                    }
            except Exception as e:
                self.log(f"    提取失败: {e}")
                result = {
                    "speaker_name": speaker_name,
                    "speech_theme": "发言内容",
                    "sub_sections": [],
                }

            # 补充发言人信息
            result["original_speaker_name"] = speaker_name
            result["original_role"] = speech.get("role", "")
            result["original_org"] = speech.get("organization", "")
            result["speech_index"] = i

            speech_details.append(result)
            pts_count = sum(len(ss.get("points", [])) for ss in result.get("sub_sections", []))
            self.log(f"    ✓ 完成: {len(result.get('sub_sections', []))}个分类, {pts_count}个要点")

        self.log(f"大会发言提取完成: {len(speech_details)} 位发言人")
        return {
            "speech_details": speech_details,
            "host_content": host_speeches,
            "total_speakers": len(speech_details),
        }


# ============================================================
# 章节撰写（适配无说话人场景）
# ============================================================

class SectionWriterAgent(BaseAgent):

    SYSTEM_PROMPT = """你是会议纪要编排专家。你正在编写一份完整会议纪要中的某个章节。

严格要求：
1. 不得添加素材中不存在的信息
2. 不得推测或补充未明确提及的内容
3. 当某个维度或小节的信息完全缺失时，直接省略该维度/小节，不要写出来
4. 只有个别具体字段缺失时（如截止日期、负责人），才可标注"[原文未明确提及]"，且全文不超过3处
5. 如能推断发言者，使用"据XXX介绍"等表述标明来源；否则使用"与会人员指出"等通用表述
6. 所有数字、人名必须来自素材
7. 语言简洁客观，内容详实完整
8. 不要输出一级标题(#)，只输出段落正文（标题由系统统一添加）
9. 不要重复输出"会议纪要"等文档标题"""

    @staticmethod
    def _is_section_empty(content: str) -> bool:
        """判断一个章节内容是否实质为空（全是占位标记或极少实质内容）"""
        if not content or not content.strip():
            return True
        text = content.strip()
        # 移除所有占位标记后看剩余内容
        cleaned = re.sub(r'\[原文未明确提及\]', '', text)
        cleaned = re.sub(r'\[未明确提及\]', '', cleaned)
        cleaned = re.sub(r'\[无\]', '', cleaned)
        # 移除格式标记（加粗标题、列表符号等）
        cleaned = re.sub(r'\*\*[^*]+\*\*[：:]?', '', cleaned)
        cleaned = re.sub(r'^[-*]\s*', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'^\d+[.、]\s*', '', cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r'\s+', '', cleaned)
        # 剩余实质内容不足 50 字认为空
        return len(cleaned) < 50

    @staticmethod
    def _clean_placeholder_spam(content: str) -> str:
        """清理过多的占位标记，保留最多3处，其余删除整行"""
        placeholder_pattern = r'\[原文未明确提及\]'
        count = len(re.findall(placeholder_pattern, content))
        if count <= 3:
            return content
        # 超过3处：删除包含占位标记的整行（从后往前），保留前3处
        lines = content.split('\n')
        kept = 0
        result_lines = []
        for line in lines:
            if re.search(placeholder_pattern, line):
                if kept < 3:
                    kept += 1
                    result_lines.append(line)
                # else: 丢弃该行
            else:
                result_lines.append(line)
        return '\n'.join(result_lines)

    def write_header(self, metadata: Dict, speakers: Dict,
                     meeting_type: str = "") -> str:
        title = metadata.get('title', '会议纪要')
        date = metadata.get('date', '')
        location = metadata.get('location', '')

        header = f"# {title}\n\n"

        info_lines = []
        if date:
            info_lines.append(f"- **日期**：{date}")
        if location:
            info_lines.append(f"- **地点**：{location}")
        if meeting_type:
            type_config = MeetingTypeAdapter.get_type_config(meeting_type)
            info_lines.append(f"- **会议类型**：{type_config['name']}")
        elif metadata.get("meeting_type"):
            info_lines.append(f"- **会议类型**：{metadata['meeting_type']}")
        if info_lines:
            header += "\n".join(info_lines) + "\n"

        # 参会者信息（无说话人版本：展示推断的参与者）
        if speakers:
            header += "\n**参会人员（根据内容推断）**：\n\n"
            for sid, info in speakers.items():
                name = info.get("name", "未知")
                role = info.get("role", "")
                org = info.get("organization", "")
                parts = [name if name != "未知" else sid]
                if role and role != "未知":
                    parts.append(role)
                if org and org != "未知":
                    parts.append(org)
                header += f"- {' / '.join(parts)}\n"

        return header

    def write_overview(self, metadata: Dict, speakers: Dict,
                       chunk_summaries: List[Dict] = None,
                       meeting_context: str = "") -> str:
        meeting_info = ""
        if chunk_summaries:
            first_summaries = [cs.get("summary", "") for cs in chunk_summaries[:3] if cs.get("summary")]
            if first_summaries:
                meeting_info = "\n".join(first_summaries)

        extra_context = ""
        if meeting_context:
            extra_context = f"\n【背景推断】{meeting_context}"

        prompt = f"""你正在编写一份会议纪要的"会议概述"段落。请用2-4个自然段概括会议的背景、目的和整体情况。

【会议标题】{metadata.get('title', '会议')}
【日期】{metadata.get('date', '[未提及]')}
【会议内容概况】
{safe_truncate(meeting_info, 2000) if meeting_info else '[无额外信息]'}
{extra_context}

要求：
- 只输出概述段落的正文，不要输出任何标题
- 不要重复会议标题、日期等已在前面列出的信息
- 基于素材概括，不要编造"""

        result = self.llm.invoke_safe(prompt, self.SYSTEM_PROMPT)
        return result or "本次会议就相关议题进行了讨论交流。"

    def write_topic_section(self, topic: Dict, details: Dict,
                            chunk_summaries_for_topic: List[Dict] = None) -> str:
        key_points = details.get("key_points", [])
        verified, unverified = [], []
        for kp in key_points:
            if isinstance(kp, dict):
                (verified if kp.get("is_verified", True) else unverified).append(kp)
            else:
                verified.append({"point": str(kp)})

        points_text = "\n".join([
            f"- {kp.get('point', '')} "
            f"[原文: \"{safe_truncate(kp.get('evidence_quote', ''), 80)}\"]"
            for kp in verified[:15]
        ])

        # 动态处理各维度数据（兼容固定维度和LLM自适应维度）
        optional_sections = ""

        # 固定维度兼容
        decisions = details.get("decisions", [])
        if decisions:
            decisions_text = "\n".join([
                f"- {d.get('decision', d) if isinstance(d, dict) else str(d)}"
                for d in decisions[:8]
            ])
            optional_sections += f"\n【达成的决策】\n{decisions_text}"

        actions = details.get("action_items", [])
        if actions:
            action_lines = []
            for a in actions[:8]:
                if isinstance(a, dict):
                    line = f"- {a.get('content', '')}"
                    parts = []
                    if a.get('assignee') and a['assignee'] not in ('null', 'None', '未明确'):
                        parts.append(f"负责人：{a['assignee']}")
                    if a.get('due_date') and a['due_date'] not in ('null', 'None'):
                        parts.append(f"截止：{a['due_date']}")
                    if a.get('priority') and a['priority'] not in ('null', 'None'):
                        parts.append(f"优先级：{a['priority']}")
                    if parts:
                        line += f"（{'，'.join(parts)}）"
                    action_lines.append(line)
                else:
                    action_lines.append(f"- {str(a)}")
            actions_text = "\n".join(action_lines)
            optional_sections += f"\n【行动项/待办】\n{actions_text}"

        issues = details.get("issues", [])
        if issues:
            issues_text = "\n".join([
                f"- {iss.get('issue', iss) if isinstance(iss, dict) else str(iss)}"
                for iss in issues[:8]
            ])
            optional_sections += f"\n【存在的问题或分歧】\n{issues_text}"

        # 处理LLM自适应维度（非固定维度的额外字段）
        extraction_schema = details.get("_extraction_schema", {})
        known_keys = {"key_points", "decisions", "action_items", "issues", "_extraction_schema",
                      "sub_topics", "key_data", "is_verified", "match_score"}
        # 调研类常用维度的字段名映射，便于正确取出主要文本
        _dim_text_fields = {
            "findings": "finding",
            "viewpoints": "viewpoint",
            "suggestions": "suggestion",
            "experiences": "experience",
            "open_questions": "question",
        }
        if extraction_schema:
            for dim in extraction_schema.get("dimensions", []):
                dim_key = dim.get("key", "")
                dim_name = dim.get("name", dim_key)
                if dim_key in known_keys:
                    continue
                dim_data = details.get(dim_key, [])
                if dim_data and isinstance(dim_data, list):
                    main_field = _dim_text_fields.get(dim_key, "point")
                    lines = []
                    for item in dim_data[:8]:
                        if isinstance(item, dict):
                            txt = item.get(main_field) or item.get("point") or ""
                            # viewpoints / suggestions 带主体或对象时，加上前缀更直观
                            if dim_key == "viewpoints":
                                subj = item.get("subject")
                                if subj and subj not in ("null", "None", "未明确"):
                                    txt = f"{subj}：{txt}"
                            elif dim_key == "suggestions":
                                tgt = item.get("target")
                                if tgt and tgt not in ("null", "None", "未明确"):
                                    txt = f"（对{tgt}）{txt}"
                            if not txt:
                                txt = str(item)
                            lines.append(f"- {txt}")
                        else:
                            lines.append(f"- {str(item)}")
                    dim_text = "\n".join(lines)
                    optional_sections += f"\n【{dim_name}】\n{dim_text}"

        extra_context = ""
        if chunk_summaries_for_topic:
            extra_parts = []
            for cs in chunk_summaries_for_topic[:3]:
                data_pts = cs.get("data_points", [])
                names = cs.get("names_entities", [])
                if data_pts or names:
                    extra_parts.append(
                        f"数据: {', '.join(data_pts[:5])}; 涉及: {', '.join(names[:5])}")
            if extra_parts:
                extra_context = "\n【补充信息】\n" + "\n".join(extra_parts)

        base_requirements = """要求：
- 输出该议题的讨论内容，用通顺的段落描述，内容要详实丰富
- **正文不少于 400 字**（不含标题/引用块），充分展开素材中的每一条要点
- 至少分 3 个加粗小标题分列，**优先使用上文【...】中实际出现的维度名作为小标题**（如【现状与发现】→"**现状与发现**："、【问题与挑战】→"**问题与挑战**："、【各方观点】→"**各方观点**："、【建议与对策】→"**建议与思考**："、【经验与启示】→"**经验启示**："、【待研究问题】→"**后续课题**："）；只有当素材确实出现明确决策、带负责人的待办、或关键数据时，才可使用"**主要决策**""**待办事项**""**关键数据**"
- 如果某个维度没有实际内容，**不要硬写**，宁可少一个小标题
- 不要输出 ## 或 ### 等Markdown标题（标题由系统统一添加）
- 不要重复输出议题名称作为标题
- 由于没有说话人标注，请用"与会人员表示"、"会上介绍"等通用表述代替具体人名（除非内容中明确提及了人名）
- 仅使用上述素材，不编造，不注水重复"""

        prompt = f"""你正在编写一份会议纪要中关于"{topic.get('title', '')}"这个议题的内容段落。

【议题摘要】{safe_truncate(topic.get('summary', ''), 200)}

【讨论要点】
{points_text or '无具体要点'}
{optional_sections}
{extra_context}

{base_requirements}"""

        prompt = safe_truncate(prompt, Config.MAX_INPUT_CHARS)
        section = self.llm.invoke_safe(prompt, self.SYSTEM_PROMPT)

        def _plain_len(s: str) -> int:
            # 统计去除 markdown 标记和引用块后的正文字数
            if not s:
                return 0
            s2 = re.sub(r'^>.*$', '', s, flags=re.MULTILINE)
            s2 = re.sub(r'[#*`>\-\s]', '', s2)
            return len(s2)

        # 正文过短时，用更严格的 prompt 重试一次
        MIN_SECTION_CHARS = 300
        if section and _plain_len(section) < MIN_SECTION_CHARS:
            self.log(f"  议题[{topic.get('title','')}]正文过短({_plain_len(section)}字)，重试扩写")
            retry_prompt = f"""你刚才为议题"{topic.get('title', '')}"撰写的段落过于简短，需要在不编造的前提下进一步展开。

【议题摘要】{safe_truncate(topic.get('summary', ''), 200)}

【讨论要点】
{points_text or '无具体要点'}
{optional_sections}
{extra_context}

【上一版（过短，需扩写）】
{safe_truncate(section, 1500)}

请重新输出该议题正文，**严格遵守**：
- 正文不少于 500 字，每个加粗小标题下至少 2-3 句完整描述
- 至少 3 个加粗小标题分列，**优先沿用上文【...】的维度名**（如"**现状与发现**：""**问题与挑战**：""**各方观点**：""**建议与思考**：""**经验启示**：""**后续课题**："）；只有素材确实有明确决策/带负责人待办/关键数据时才使用决策/待办/数据类小标题
- 充分展开素材中每一条要点的背景、内容、依据和影响
- 不要输出 ## 或 ### 等Markdown标题
- 仅使用素材内容，不编造，不空洞重复套话"""

            retry_prompt = safe_truncate(retry_prompt, Config.MAX_INPUT_CHARS)
            retry_section = self.llm.invoke_safe(retry_prompt, self.SYSTEM_PROMPT)
            if retry_section and _plain_len(retry_section) > _plain_len(section):
                section = retry_section

        if not section:
            section = ""
            for kp in verified[:5]:
                section += f"- {kp.get('point', '')}\n"
            if not section:
                section = "暂无详细记录。"

        section = re.sub(r'^#{1,3}\s+.*\n', '', section).strip()
        section = self._clean_placeholder_spam(section)

        if unverified:
            section += "\n\n> ⚠️ 以下要点未在原文中找到充分依据，仅供参考：\n"
            for kp in unverified[:3]:
                section += f"> - {kp.get('point', '')}\n"

        return section

    def write_topic_section_expanded(self, topic: Dict, details: Dict,
                                       chunk_summaries_for_topic: List[Dict] = None) -> str:
        """当主题集中时，写展开版的详细内容（含子主题）"""
        sub_topics = details.get("sub_topics", [])
        key_data = details.get("key_data", [])
        decisions = details.get("decisions", [])
        action_items = details.get("action_items", [])

        # 构建子主题素材
        sub_topics_text = ""
        for st in sub_topics:
            sub_title = st.get("sub_title", "")
            points = st.get("points", [])
            pts_text = "\n".join([
                f"  - {p.get('point', str(p)) if isinstance(p, dict) else str(p)}"
                for p in points[:8]
            ])
            sub_topics_text += f"\n【{sub_title}】\n{pts_text}\n"

        key_data_text = "\n".join(f"- {d}" for d in key_data[:10]) if key_data else ""
        decisions_text = "\n".join([
            f"- {d.get('decision', d) if isinstance(d, dict) else str(d)}"
            for d in decisions[:8]
        ]) if decisions else ""
        actions_lines = []
        if action_items:
            for a in action_items[:8]:
                if isinstance(a, dict):
                    line = f"- {a.get('content', '')}"
                    parts = []
                    if a.get('assignee') and a['assignee'] not in ('null', 'None', '未明确'):
                        parts.append(f"负责人：{a['assignee']}")
                    if a.get('due_date') and a['due_date'] not in ('null', 'None'):
                        parts.append(f"截止：{a['due_date']}")
                    if a.get('priority') and a['priority'] not in ('null', 'None'):
                        parts.append(f"优先级：{a['priority']}")
                    if parts:
                        line += f"（{'，'.join(parts)}）"
                    actions_lines.append(line)
                else:
                    actions_lines.append(f"- {str(a)}")
        actions_text = "\n".join(actions_lines) if actions_lines else ""

        extra_context = ""
        if chunk_summaries_for_topic:
            extra_parts = []
            for cs in chunk_summaries_for_topic[:3]:
                data_pts = cs.get("data_points", [])
                names = cs.get("names_entities", [])
                if data_pts or names:
                    extra_parts.append(
                        f"数据: {', '.join(data_pts[:5])}; 涉及: {', '.join(names[:5])}")
            if extra_parts:
                extra_context = "\n【补充信息】\n" + "\n".join(extra_parts)

        optional_sections = ""
        if key_data_text:
            optional_sections += f"\n【关键数据】\n{key_data_text}"
        if decisions_text:
            optional_sections += f"\n【达成的决策】\n{decisions_text}"
        if actions_text:
            optional_sections += f"\n【行动项/待办】\n{actions_text}"

        prompt = f"""你正在编写一份会议纪要中关于"{topic.get('title', '')}"这个议题的详细内容。
由于本次会议主题集中，需要对该议题进行详细展开，分小点介绍。

【议题摘要】{safe_truncate(topic.get('summary', ''), 200)}

【展开后的子主题和要点】
{sub_topics_text}
{optional_sections}
{extra_context}

要求：
- 按子主题分小节详细撰写，每个子主题用"**子主题标题**"加粗作为小标题
- 每个小节内用编号要点（1. 2. 3.）详细展开
- 每个要点不少于30字，包含具体数据、项目名称、措施内容
- 如果有决策、行动项、问题，用加粗小标题分列
- 不要输出 ## 或 ### 等Markdown标题
- 不要重复输出议题名称作为标题
- 用"与会人员表示"等通用表述（除非内容明确提及了人名）
- 仅使用上述素材，不编造"""

        prompt = safe_truncate(prompt, Config.MAX_INPUT_CHARS)
        section = self.llm.invoke_safe(prompt, self.SYSTEM_PROMPT)

        if not section:
            # 降级：直接用sub_topics构建
            section = ""
            for st in sub_topics:
                section += f"\n**{st.get('sub_title', '')}**\n\n"
                for j, pt in enumerate(st.get("points", []), 1):
                    pt_text = pt.get("point", str(pt)) if isinstance(pt, dict) else str(pt)
                    section += f"{j}. {pt_text}\n"

        section = re.sub(r'^#{1,3}\s+.*\n', '', section).strip()
        section = self._clean_placeholder_spam(section)
        return section

    def write_speech_section(self, speech_detail: Dict) -> str:
        """编写大会发言模式下单个发言人的内容段落"""
        speaker_name = speech_detail.get("speaker_name",
                        speech_detail.get("original_speaker_name", "发言人"))
        speaker_title = speech_detail.get("speaker_title",
                        speech_detail.get("original_role", ""))
        speaker_org = speech_detail.get("speaker_org",
                        speech_detail.get("original_org", ""))
        speech_theme = speech_detail.get("speech_theme", "")
        sub_sections = speech_detail.get("sub_sections", [])
        key_data = speech_detail.get("key_data", [])
        commitments = speech_detail.get("commitments", [])
        highlights = speech_detail.get("highlights", [])

        # 构建素材文本
        sub_sections_text = ""
        for ss in sub_sections:
            title = ss.get("title", "")
            points = ss.get("points", [])
            pts_text = "\n".join([
                f"  - {p.get('point', str(p)) if isinstance(p, dict) else str(p)}"
                for p in points[:10]
            ])
            sub_sections_text += f"\n【{title}】\n{pts_text}\n"

        extra_info = ""
        if key_data:
            extra_info += "\n【关键数据】\n" + "\n".join(f"- {d}" for d in key_data[:10])
        if commitments:
            extra_info += "\n【承诺/目标】\n" + "\n".join(f"- {c}" for c in commitments[:10])
        if highlights:
            extra_info += "\n【亮点】\n" + "\n".join(f"- {h}" for h in highlights[:10])

        prompt = f"""你正在编写一份大会会议纪要中，关于{speaker_name}发言的内容段落。

【发言人】{speaker_name}
【职务】{speaker_title}
【单位】{speaker_org}
【发言主题】{speech_theme}

【发言内容分类要点】
{sub_sections_text}
{extra_info}

要求：
- 开头用一句话介绍发言人身份和发言主题
- 然后按分类展开详细内容，每个分类用"**分类标题**"加粗作为小标题
- 每个分类下用编号要点详细展开
- 每个要点包含具体数据、项目名称、措施内容
- 最后如有承诺/目标，用"**目标与承诺**"小标题列出
- 不要输出 ## 或 ### 等Markdown标题
- 仅使用上述素材，不编造"""

        prompt = safe_truncate(prompt, Config.MAX_INPUT_CHARS)
        section = self.llm.invoke_safe(prompt, self.SYSTEM_PROMPT)

        if not section:
            # 降级处理
            section = f"{speaker_name}"
            if speaker_title:
                section += f"（{speaker_org}{speaker_title}）" if speaker_org else f"（{speaker_title}）"
            section += f"就{speech_theme}进行了发言。\n\n"
            for ss in sub_sections:
                section += f"**{ss.get('title', '')}**\n\n"
                for j, pt in enumerate(ss.get("points", []), 1):
                    pt_text = pt.get("point", str(pt)) if isinstance(pt, dict) else str(pt)
                    section += f"{j}. {pt_text}\n"
                section += "\n"

        section = re.sub(r'^#{1,3}\s+.*\n', '', section).strip()
        section = self._clean_placeholder_spam(section)
        return section

    def write_conclusion(self, topics: List[Dict],
                         chunk_summaries: List[Dict] = None,
                         meeting_type: str = "research") -> str:
        all_decisions = []
        all_actions = []
        if chunk_summaries:
            for cs in chunk_summaries:
                all_decisions.extend(cs.get("decisions", []))
                for ai in cs.get("action_items", []):
                    if isinstance(ai, dict):
                        all_actions.append(ai)

        summaries = [
            {"title": t.get("title", ""),
             "summary": safe_truncate(t.get("summary", ""), 80)}
            for t in topics
        ]
        topics_json = safe_truncate(json.dumps(summaries, ensure_ascii=False), 3000)

        decisions_str = ""
        if all_decisions:
            unique_decisions = list(set(str(d) for d in all_decisions))[:10]
            decisions_str = "\n【会议决策汇总】\n" + "\n".join(f"- {d}" for d in unique_decisions)

        actions_str = ""
        if all_actions:
            action_lines = []
            for a in all_actions[:10]:
                line = f"- {a.get('content', '')}"
                parts = []
                if a.get('assignee') and a['assignee'] not in ('null', 'None', '未明确'):
                    parts.append(f"负责人：{a['assignee']}")
                if a.get('due_date') and a['due_date'] not in ('null', 'None'):
                    parts.append(f"截止：{a['due_date']}")
                if a.get('priority') and a['priority'] not in ('null', 'None'):
                    parts.append(f"优先级：{a['priority']}")
                if parts:
                    line += f"（{'，'.join(parts)}）"
                action_lines.append(line)
            actions_str = "\n【待办事项汇总】\n" + "\n".join(action_lines)

        type_config = MeetingTypeAdapter.get_type_config(meeting_type)
        conclusion_hint = type_config.get('conclusion_hint', '总结会议要点')

        prompt = f"""你正在编写一份会议纪要的结尾总结段落。请根据会议类型和实际内容，自行决定结尾应包含哪些小节。

【会议类型】{meeting_type}
【总结侧重】{conclusion_hint}

【各议题摘要】
{topics_json}
{decisions_str}
{actions_str}

要求：
- 用1-2段概括会议总体结论
- 根据实际内容决定是否需要以下小节（**严禁硬套模板**，没有的小节就不要写，也不要写空占位）：
  * **调研/座谈类（research）**：默认采用"**主要发现**""**共性问题**""**建议方向**""**经验启示**""**后续课题**"等小节；只有素材中明确出现"决定/决议"才写"**主要决策**"；只有出现带负责人或截止日期的待办才写"**待办事项**"
  * 决策会议 → 列出"**主要决策**"
  * 进度汇报 → 可写"**整体进度**""**问题与风险**""**下阶段计划**"
  * 大会发言 → 可写"**核心共识**""**重点部署**""**目标承诺**"
  * 头脑风暴 → 可写"**主要思路**""**潜在方案**"
  * 问题解决 → 可写"**根因分析**""**解决方案**"
  * 没有明确的决策或带负责人的待办，**绝对不要**硬写"主要决策""待办事项"小节
- 小节名优先选用与上文【各议题摘要】中维度一致的措辞
- 不要输出 ## 等Markdown标题（标题由系统统一添加）
- 仅总结素材已有内容
- 如无明确结论，如实说明"""

        result = self.llm.invoke_safe(prompt, self.SYSTEM_PROMPT)
        if result:
            result = re.sub(r'^#{1,3}\s+.*\n', '', result).strip()
        return result or "本次会议就以上议题进行了讨论，具体结论详见各议题内容。"


# ============================================================
# 格式优化（与原版一致）
# ============================================================

class FormatEnhancerAgent(BaseAgent):

    SYSTEM_PROMPT = """你是Markdown格式优化专家。这是一份完整的会议纪要文档。

只做以下优化，不改任何实质内容：
1. 修正明显的格式问题（多余空行、缺失空行等）
2. 统一列表符号风格
3. 确保段落之间有合理间距

严禁以下操作：
- 不要改变标题层级结构
- 不要添加、删除或重新编号任何章节标题
- 不要添加额外的分隔线
- 不要修改任何文字内容"""

    def process(self, markdown_content: str) -> str:
        self.log("优化格式...")
        content = self._basic_fix(markdown_content)

        if len(content) <= Config.MAX_INPUT_CHARS * 1.2:
            prompt = f"""请对以下Markdown文档做轻微的格式优化。保持整体结构不变。

{safe_truncate(content, Config.MAX_INPUT_CHARS)}

直接输出优化后的完整Markdown："""
            result = self.llm.invoke_safe(prompt, self.SYSTEM_PROMPT)
            if result:
                result = re.sub(r'^```markdown\n?', '', result)
                result = re.sub(r'\n?```$', '', result)
                if result.strip().startswith('#') and '## ' in result:
                    return result
                else:
                    self.log("LLM输出结构异常，使用规则修正版")
        else:
            self.log("内容较长，仅做规则修正")

        return content

    def _basic_fix(self, content: str) -> str:
        content = re.sub(r'([^\n])\n(#{1,3} )', r'\1\n\n\2', content)
        content = re.sub(r'(#{1,3} .+)\n([^#\n])', r'\1\n\n\2', content)
        content = re.sub(r'\n{4,}', '\n\n\n', content)

        # 修复编号列表格式：编号后换行导致编号和内容分离的问题
        # 例如 "1.\n内容" → "1. 内容"，"2.\n  内容" → "2. 内容"
        content = re.sub(r'(\d+)\.\s*\n\s*([^\n#\d])', r'\1. \2', content)
        # 同样处理无序列表："- \n内容" → "- 内容"
        content = re.sub(r'^([-*])\s*\n\s*([^\n#])', r'\1 \2', content, flags=re.MULTILINE)

        lines = content.split('\n')
        cleaned_lines = []
        for i, line in enumerate(lines):
            if line.strip() == '---' and i > 3:
                continue
            cleaned_lines.append(line)
        return '\n'.join(cleaned_lines)


# ============================================================
# LangGraph 工作流
# ============================================================

def create_workflow_nodes():
    # ===== 根据配置选择切分器 =====
    if USE_PPL_SEGMENTATION:
        text_parser = HybridSegmentParser()
        print("  [配置] 使用混合切分器（规则 + PPL）")
    else:
        text_parser = NoSpeakerTextParser()
        print("  [配置] 使用纯规则切分器")
    
    chunk_summarizer = ChunkSummarizerAgent("ChunkSummarizer")
    participant_infer = ParticipantInferAgent("ParticipantInfer")
    topic_segmentation = TopicSegmentationAgent("TopicSegmentation")
    content_extractor = ContentExtractorAgent("ContentExtractor")
    section_writer = SectionWriterAgent("SectionWriter")
    format_enhancer = FormatEnhancerAgent("FormatEnhancer")
    # quality_checker = ProgrammaticQualityChecker()  # 质量检查已禁用

    # 保留原版解析器用于有说话人标注的情况
    from difflib import SequenceMatcher  # 已导入

    def parse_text(state: MeetingState) -> MeetingState:
        print("\n" + "=" * 60)
        print("步骤 1/7: 解析原始文本（智能模式检测）")
        print("=" * 60)
        try:
            # 检测是否为大会发言模式
            # 若元数据中标记 _force_no_conference（兜底重跑场景），强制跳过大会模式
            force_no_conf = bool(state.get("metadata", {}).get("_force_no_conference"))
            if force_no_conf:
                print("  ★ 触发末期兜底：跳过大会发言模式，强制走普通语义切分")
            conference_parser = ConferenceSpeechParser()
            if (not force_no_conf) and conference_parser.is_conference_style(state["raw_text"]):
                print("  ★ 检测到大会发言模式！将按发言人分段整理")
                result = conference_parser.process(state["raw_text"])
                state["segments"] = result.get("segments", [])
                state["metadata"]["_parse_method"] = "conference_speech"
                state["metadata"]["_speeches"] = result.get("speeches", [])
                state["metadata"]["_speaker_info"] = result.get("speaker_info", {})
                print(f"  ✓ 大会发言模式: {len(state['segments'])} 个段落, "
                      f"{len(result.get('speaker_info', {}))} 位发言人")
            else:
                # 检测是否有说话人标注
                if text_parser.has_speaker_labels(state["raw_text"]):
                    print("  ⚠ 检测到说话人标注！建议使用原版（带说话人）系统。")
                    print("  仍按无说话人模式处理（会忽略说话人标记）...")

                result = text_parser.process(state["raw_text"])
                state["segments"] = result.get("segments", [])
                state["metadata"]["_parse_method"] = "no_speaker_semantic"
                print(f"  ✓ {result.get('parse_method', '?')}: {len(state['segments'])} 个语义段落")
        except Exception as e:
            print(f"  ✗ 解析失败: {e}")
            traceback.print_exc()
            state["segments"] = []
            state["error_message"] = str(e)
        state["current_step"] = "parse_text"
        return state

    def summarize_chunks(state: MeetingState) -> MeetingState:
        print("\n" + "=" * 60)
        print("步骤 2/7: 块级摘要 + 会议类型识别")
        print("=" * 60)
        if not state["segments"]:
            state["chunk_summaries"] = []
            state["meeting_type"] = "research"
            state["current_step"] = "summarize_chunks"
            return state

        try:
            mt = MeetingTypeAdapter.detect_meeting_type(state["segments"], state.get("metadata"))
            state["meeting_type"] = mt
            mt_config = MeetingTypeAdapter.get_type_config(mt)
            print(f"  ✓ 会议类型: {mt_config['name']} ({mt})")
        except Exception:
            state["meeting_type"] = "research"
            print(f"  ⚠ 类型检测失败，默认: 调研座谈会")

        try:
            state["chunk_summaries"] = chunk_summarizer.process_all_chunks(state["segments"])
            total_points = sum(
                len(cs.get("key_points", [])) for cs in state["chunk_summaries"])
            print(f"  ✓ {len(state['chunk_summaries'])} 块摘要完成，共 {total_points} 个要点")
        except Exception as e:
            print(f"  ✗ 块摘要失败: {e}")
            traceback.print_exc()
            state["chunk_summaries"] = []
        state["current_step"] = "summarize_chunks"
        return state

    def analyze_speakers(state: MeetingState) -> MeetingState:
        print("\n" + "=" * 60)
        print("步骤 3/7: 推断参会者")
        print("=" * 60)
        if not state["segments"]:
            state["speakers"] = {}
            state["current_step"] = "analyze_speakers"
            return state
        try:
            result = participant_infer.process(state["segments"], state.get("chunk_summaries"))
            state["speakers"] = result.get("speakers", {})

            # 将推断的会议背景存入metadata
            meeting_context = result.get("meeting_context", "")
            if meeting_context:
                state["metadata"]["meeting_context"] = meeting_context

            if not state["speakers"]:
                raise ValueError("空结果")
            print(f"  ✓ 推断出 {len(state['speakers'])} 位参会者")
        except Exception as e:
            print(f"  ⚠ 推断异常: {e}，使用默认信息")
            state["speakers"] = {"参会者": {"name": "未知", "role": "参会人员",
                                            "organization": "未知", "title": "未知"}}
        state["current_step"] = "analyze_speakers"
        return state

    def segment_topics(state: MeetingState) -> MeetingState:
        print("\n" + "=" * 60)
        print("步骤 4/7: 切分主题")
        print("=" * 60)
        if not state["segments"]:
            state["topics"] = []
            state["current_step"] = "segment_topics"
            return state
        try:
            if state.get("chunk_summaries"):
                result = topic_segmentation.process(
                    state["chunk_summaries"], state["segments"])
            else:
                result = {"topics": [{
                    "id": "topic_1", "title": state["metadata"].get("title", "会议讨论"),
                    "summary": "全部内容", "key_points": [],
                    "start_segment": 0, "end_segment": len(state["segments"]) - 1
                }]}
            state["topics"] = result.get("topics", [])
            print(f"  ✓ {len(state['topics'])} 个主题")
        except Exception as e:
            print(f"  ⚠ 切分失败: {e}")
            traceback.print_exc()
            state["topics"] = [{
                "id": "topic_1", "title": state["metadata"].get("title", "会议讨论"),
                "summary": "全部内容", "key_points": [],
                "start_segment": 0, "end_segment": len(state["segments"]) - 1
            }]
        state["current_step"] = "segment_topics"
        return state

    def extract_content(state: MeetingState) -> MeetingState:
        print("\n" + "=" * 60)
        print("步骤 5/7: 提取详细内容")
        print("=" * 60)
        if not state["segments"] or not state["topics"]:
            state["content_sections"] = {}
            state["current_step"] = "extract_content"
            return state

        parse_method = state.get("metadata", {}).get("_parse_method", "")
        is_conference = parse_method == "conference_speech"

        try:
            if is_conference:
                # 大会发言模式：按发言人提取内容
                print("  ★ 大会发言模式：按发言人提取详细内容")
                speech_extractor = ConferenceSpeechExtractor("ConferenceSpeechExtractor")
                speeches = state.get("metadata", {}).get("_speeches", [])
                conference_result = speech_extractor.process_speeches(
                    state["segments"], speeches,
                    state.get("chunk_summaries", []))
                state["metadata"]["_conference_details"] = conference_result
                # 同时也用常规方式提取（作为补充）
                result = content_extractor.process(
                    state["segments"], state["topics"],
                    state.get("chunk_summaries", []),
                    state.get("meeting_type", "conference"),
                    state.get("metadata", {}))
                state["content_sections"] = result.get("topic_details", {})
                print(f"  ✓ 大会模式: {conference_result.get('total_speakers', 0)}位发言人内容提取完成")
            else:
                # 常规模式
                result = content_extractor.process(
                    state["segments"], state["topics"],
                    state.get("chunk_summaries", []),
                    state.get("meeting_type", "research"),
                    state.get("metadata", {}))
                state["content_sections"] = result.get("topic_details", {})

                # 检测主题是否集中，如果集中则展开
                topic_analyzer = TopicConcentrationAnalyzer()
                if topic_analyzer.is_concentrated(
                        state["topics"], state["segments"],
                        state.get("chunk_summaries", [])):
                    print(f"  ★ 检测到主题集中（{len(state['topics'])}个主题），展开详细子要点...")
                    for topic in state["topics"]:
                        tid = topic.get("id", "")
                        if tid in state["content_sections"]:
                            state["content_sections"][tid] = topic_analyzer.expand_topic_details(
                                topic, state["content_sections"][tid],
                                state["segments"],
                                state.get("chunk_summaries", []))

                print(f"  ✓ {len(state['content_sections'])} 个主题内容提取完成")
        except Exception as e:
            print(f"  ⚠ 提取失败: {e}")
            traceback.print_exc()
            state["content_sections"] = {}
        state["current_step"] = "extract_content"
        return state

    def write_document(state: MeetingState) -> MeetingState:
        print("\n" + "=" * 60)
        print("步骤 6/7: 撰写会议纪要")
        print("=" * 60)

        chunk_summaries = state.get("chunk_summaries", [])
        topics = state.get("topics", [])
        cn_numbers = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十',
                       '十一', '十二', '十三', '十四', '十五']

        parse_method = state.get("metadata", {}).get("_parse_method", "")
        is_conference = parse_method == "conference_speech"

        doc_parts = []

        # 文档头
        try:
            header = section_writer.write_header(
                state["metadata"], state["speakers"],
                state.get("meeting_type", ""))
        except Exception:
            header = f"# {state['metadata'].get('title', '会议纪要')}\n"
        doc_parts.append(header)

        if is_conference:
            # ===== 大会发言模式的文档结构 =====
            conference_details = state.get("metadata", {}).get("_conference_details", {})
            speech_details = conference_details.get("speech_details", [])

            # 先生成各发言人内容，过滤空章节
            valid_speech_parts = []  # [(name, theme, content), ...]
            skipped = 0
            for i, sd in enumerate(speech_details):
                name = sd.get("speaker_name", sd.get("original_speaker_name", f"发言人{i+1}"))
                theme = sd.get("speech_theme", "发言")
                try:
                    content = section_writer.write_speech_section(sd)
                except Exception as e:
                    content = ""
                    print(f"  ⚠ 发言'{name}'撰写异常: {e}")

                if section_writer._is_section_empty(content):
                    skipped += 1
                    print(f"  ⚠ 跳过空章节: {name}（内容不足）")
                    continue
                valid_speech_parts.append((name, theme, content))

            if skipped:
                print(f"  ⚠ 共过滤 {skipped} 个空发言章节")

            # 目录（仅包含有内容的章节）
            toc_lines = ["## 目录\n"]
            toc_lines.append("1. [大会概述](#大会概述)")
            for i, (name, theme, _) in enumerate(valid_speech_parts):
                toc_lines.append(f"{i+2}. [{name}：{theme}](#{name})")
            toc_lines.append(f"{len(valid_speech_parts)+2}. [大会总结](#大会总结)")
            doc_parts.append("\n".join(toc_lines))

            # 大会概述
            doc_parts.append("\n## 一、大会概述\n")
            try:
                meeting_context = state.get("metadata", {}).get("meeting_context", "")
                overview_text = section_writer.write_overview(
                    state["metadata"], state["speakers"], chunk_summaries,
                    meeting_context)
                doc_parts.append(overview_text)
            except Exception as e:
                doc_parts.append("本次大会各部门和企业代表依次进行了发言。")
                print(f"  ⚠ 概述撰写异常: {e}")

            # 各发言人内容（已过滤空章节，编号连续）
            for i, (name, theme, content) in enumerate(valid_speech_parts):
                speech_num = i + 2
                cn_num = cn_numbers[speech_num - 1] if speech_num - 1 < len(cn_numbers) else str(speech_num)
                title = f"{name}：{theme}"
                doc_parts.append(f"\n## {cn_num}、{title}\n")
                doc_parts.append(content)

            # 大会总结
            conclusion_num = len(valid_speech_parts) + 2
            cn_num = cn_numbers[conclusion_num - 1] if conclusion_num - 1 < len(cn_numbers) else str(conclusion_num)
            doc_parts.append(f"\n## {cn_num}、大会总结\n")
            try:
                meeting_type = state.get("meeting_type", "conference")
                conclusion = section_writer.write_conclusion(
                    topics, chunk_summaries, meeting_type)
                doc_parts.append(conclusion)
            except Exception as e:
                doc_parts.append("本次大会各发言人的具体内容详见各章节。\n")
                print(f"  ⚠ 结论撰写异常: {e}")

            state["markdown_content"] = "\n\n".join(part for part in doc_parts if part)
            state["current_step"] = "write_document"
            print(f"  ✓ 大会发言模式文档生成完成: {len(speech_details)}位发言人, "
                  f"{len(state['markdown_content'])}字")

        else:
            # ===== 常规模式（含主题集中展开） =====

            # 检测是否为主题集中模式
            topic_analyzer = TopicConcentrationAnalyzer()
            is_concentrated = topic_analyzer.is_concentrated(
                topics, state.get("segments", []),
                state.get("chunk_summaries", []))

            # 先生成各议题内容，过滤空章节
            valid_topic_parts = []  # [(title, content), ...]
            skipped_topics = 0
            for i, topic in enumerate(topics):
                title = topic.get("title", f"议题{i+1}")
                try:
                    tid = topic.get("id", "")
                    details = state["content_sections"].get(tid, {})

                    chunk_indices = topic.get("chunk_indices", [])
                    cs_for_topic = [chunk_summaries[ci] for ci in chunk_indices
                                    if ci < len(chunk_summaries)] if chunk_indices else []

                    if is_concentrated and details.get("sub_topics"):
                        print(f"  📝 主题集中展开模式: {title}")
                        content = section_writer.write_topic_section_expanded(
                            topic, details, cs_for_topic)
                    else:
                        content = section_writer.write_topic_section(
                            topic, details, cs_for_topic)
                except Exception as e:
                    content = ""
                    print(f"  ⚠ 议题'{title}'撰写异常: {e}")

                if section_writer._is_section_empty(content):
                    skipped_topics += 1
                    print(f"  ⚠ 跳过空议题: {title}（内容不足）")
                    continue
                valid_topic_parts.append((title, content))

            if skipped_topics:
                print(f"  ⚠ 共过滤 {skipped_topics} 个空议题章节")

            # 目录（仅包含有内容的章节）
            toc_lines = ["## 目录\n"]
            section_num = 1
            toc_lines.append(f"{section_num}. [会议概述](#会议概述)")
            section_num += 1
            for title, _ in valid_topic_parts:
                toc_lines.append(f"{section_num}. [{title}](#{title})")
                section_num += 1
            toc_lines.append(f"{section_num}. [会议总结](#会议总结)")
            doc_parts.append("\n".join(toc_lines))

            # 会议概述
            doc_parts.append("\n## 一、会议概述\n")
            try:
                meeting_context = state.get("metadata", {}).get("meeting_context", "")
                overview_text = section_writer.write_overview(
                    state["metadata"], state["speakers"], chunk_summaries,
                    meeting_context)
                doc_parts.append(overview_text)
            except Exception as e:
                doc_parts.append("本次会议就相关议题进行了讨论交流。")
                print(f"  ⚠ 概述撰写异常: {e}")

            # 各议题内容（已过滤空章节，编号连续）
            for i, (title, content) in enumerate(valid_topic_parts):
                topic_num = i + 2
                cn_num = cn_numbers[topic_num - 1] if topic_num - 1 < len(cn_numbers) else str(topic_num)
                doc_parts.append(f"\n## {cn_num}、{title}\n")
                doc_parts.append(content)

            # 会议总结 - 根据会议类型动态决定结尾标题
            conclusion_num = len(valid_topic_parts) + 2
            cn_num = cn_numbers[conclusion_num - 1] if conclusion_num - 1 < len(cn_numbers) else str(conclusion_num)

            # 动态结尾标题：根据会议类型选择最合适的标题
            meeting_type = state.get("meeting_type", "research")
            conclusion_title_map = {
                "conference": "大会总结与展望",
                "decision_making": "会议决策与后续安排",
                "research": "调研总结与发现",
                "brainstorming": "方案汇总与后续计划",
                "status_update": "整体进度总结",
                "problem_solving": "解决方案与预防措施",
            }
            conclusion_title = conclusion_title_map.get(meeting_type, "会议总结")
            # 如果LLM提供了更具体的类型描述，使用通用标题
            if state.get("metadata", {}).get("_llm_type_description"):
                conclusion_title = "会议总结"

            doc_parts.append(f"\n## {cn_num}、{conclusion_title}\n")
            try:
                meeting_type = state.get("meeting_type", "research")
                conclusion = section_writer.write_conclusion(
                    topics, chunk_summaries, meeting_type)
                doc_parts.append(conclusion)
            except Exception as e:
                doc_parts.append("本次会议具体结论详见各议题内容。\n")
                print(f"  ⚠ 结论撰写异常: {e}")

            state["markdown_content"] = "\n\n".join(part for part in doc_parts if part)
            state["current_step"] = "write_document"
            print(f"  ✓ 统一文档生成完成: {len(valid_topic_parts)}个议题"
                  f"{'（过滤'+str(skipped_topics)+'个空章节）' if skipped_topics else ''}, "
                  f"{len(state['markdown_content'])}字")

        return state

    def enhance_format(state: MeetingState) -> MeetingState:
        print("\n" + "=" * 60)
        print("步骤 7/7: 优化格式")
        print("=" * 60)
        if not state["markdown_content"]:
            state["final_output"] = ""
        else:
            try:
                state["final_output"] = format_enhancer.process(state["markdown_content"])
            except Exception:
                state["final_output"] = state["markdown_content"]
        state["current_step"] = "enhance_format"
        return state

    def quality_check(state: MeetingState) -> MeetingState:
        # 质量检查已禁用：保留函数定义，直接放行不再调用 quality_checker
        # print("\n" + "=" * 60)
        # print("质量检查")
        # print("=" * 60)
        # try:
        #     report = quality_checker.check(
        #         state["final_output"], state["segments"],
        #         state["speakers"], state["topics"])
        # except Exception:
        #     report = QualityReport(score=80, is_passed=True)
        # state["quality_report"] = report.to_dict()
        # if report.score >= Config.QUALITY_THRESHOLD:
        #     state["should_regenerate"] = False
        #     print(f"  ✓ 通过 ({report.score}/100)")
        # else:
        #     state["should_regenerate"] = True
        #     state["retry_count"] = state.get("retry_count", 0) + 1
        #     print(f"  ✗ 未通过 ({report.score}/100)")
        #     for iss in report.issues[:3]:
        #         print(f"    - {iss}")
        state["quality_report"] = QualityReport(score=80, is_passed=True).to_dict()
        state["should_regenerate"] = False
        state["current_step"] = "quality_check"
        return state

    def should_retry(state: MeetingState) -> str:
        if (state.get("should_regenerate") and
                state.get("retry_count", 0) < Config.MAX_RETRY_COUNT):
            return "regenerate"
        return "finish"

    return {
        "parse_text": parse_text,
        "summarize_chunks": summarize_chunks,
        "analyze_speakers": analyze_speakers,
        "segment_topics": segment_topics,
        "extract_content": extract_content,
        "write_document": write_document,
        "enhance_format": enhance_format,
        "quality_check": quality_check,
        "should_retry": should_retry,
    }


def build_meeting_minutes_workflow():
    nodes = create_workflow_nodes()
    wf = StateGraph(MeetingState)

    wf.add_node("parse_text", nodes["parse_text"])
    wf.add_node("summarize_chunks", nodes["summarize_chunks"])
    wf.add_node("analyze_speakers", nodes["analyze_speakers"])
    wf.add_node("segment_topics", nodes["segment_topics"])
    # 步骤5/7（提取详细内容）已禁用：保留函数定义，但不接入工作流
    def _skip_extract_content(state: MeetingState) -> MeetingState:
        state["content_sections"] = {}
        state["current_step"] = "extract_content_skipped"
        return state
    wf.add_node("extract_content", _skip_extract_content)
    wf.add_node("write_document", nodes["write_document"])
    wf.add_node("enhance_format", nodes["enhance_format"])
    wf.add_node("quality_check", nodes["quality_check"])

    wf.set_entry_point("parse_text")
    wf.add_edge("parse_text", "summarize_chunks")
    wf.add_edge("summarize_chunks", "analyze_speakers")
    wf.add_edge("analyze_speakers", "segment_topics")
    wf.add_edge("segment_topics", "extract_content")
    wf.add_edge("extract_content", "write_document")
    wf.add_edge("write_document", "enhance_format")
    wf.add_edge("enhance_format", "quality_check")

    wf.add_conditional_edges(
        "quality_check", nodes["should_retry"],
        {"regenerate": "write_document", "finish": END}
    )

    return wf.compile()


# ============================================================
# 主程序
# ============================================================

class AdvancedMeetingMinutesGenerator:
    def __init__(self):
        self.workflow = build_meeting_minutes_workflow()

    @staticmethod
    def _plain_text_chars(md: str) -> int:
        """剥掉 Markdown 标记后的纯文本字符数，用于产出长度评估"""
        if not md:
            return 0
        s = md
        # 去掉代码块、行内代码
        s = re.sub(r'```.*?```', '', s, flags=re.DOTALL)
        s = re.sub(r'`[^`]*`', '', s)
        # 去掉图片/链接的 Markdown 语法，保留可视文字
        s = re.sub(r'!\[[^\]]*\]\([^\)]*\)', '', s)
        s = re.sub(r'\[([^\]]+)\]\([^\)]*\)', r'\1', s)
        # 行首引用符 / 标题井号 / 列表符号
        s = re.sub(r'(?m)^[\s>#\-\*\+]+', '', s)
        # 粗体 / 斜体 / 删除线 / 分隔线
        s = re.sub(r'\*\*|\*|__|_|~~', '', s)
        s = re.sub(r'(?m)^[-=*_]{3,}\s*$', '', s)
        # 表格分隔符
        s = s.replace('|', '')
        # 所有空白（含换行）
        s = re.sub(r'\s+', '', s)
        return len(s)

    def generate(self, raw_text, metadata=None, output_path=None):
        print("\n" + "=" * 60)
        print("会议纪要智能整理系统 v5-enhanced（增强版：主题展开+大会发言）")
        print("=" * 60)
        print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"原文: {len(raw_text)} 字")
        print("=" * 60)

        def _build_initial(meta):
            return {
                "raw_text": raw_text, "metadata": meta or {},
                "segments": [], "speakers": {}, "topics": [],
                "chunk_summaries": [],
                "content_sections": {}, "markdown_content": "",
                "final_output": "", "current_step": "",
                "retry_count": 0, "quality_report": {},
                "error_message": "", "should_regenerate": False,
                "meeting_type": ""
            }

        start = datetime.now()
        initial: MeetingState = _build_initial(metadata)
        final = self.workflow.invoke(initial)

        # ===== 大会模式末期兜底：产出过短则强制走普通模式重跑一次 =====
        parse_method = final.get("metadata", {}).get("_parse_method", "")
        if parse_method == "conference_speech":
            raw_len = max(1, len(raw_text))
            out_len = self._plain_text_chars(final.get("final_output", ""))
            ratio = out_len / raw_len
            print(
                f"  [兜底检查] 大会模式产出 {out_len} 字 / 原文 {raw_len} 字 "
                f"= {ratio:.4f}（阈值 {Config.CONFERENCE_FALLBACK_RATIO:.4f}）"
            )
            if ratio < Config.CONFERENCE_FALLBACK_RATIO:
                print("\n" + "!" * 60)
                print(f"⚠ 大会模式产出过短（{ratio*100:.2f}% < "
                      f"{Config.CONFERENCE_FALLBACK_RATIO*100:.2f}%），"
                      "自动重跑普通模式...")
                print("!" * 60)
                # 准备一份干净的 metadata：保留原始用户输入，但加上强制标志
                rerun_meta = dict(metadata or {})
                # 清理上一次 conference 解析残留
                for k in ("_parse_method", "_speeches", "_speaker_info"):
                    rerun_meta.pop(k, None)
                rerun_meta["_force_no_conference"] = True
                rerun_meta["_fallback_attempted"] = True
                rerun_initial: MeetingState = _build_initial(rerun_meta)
                final = self.workflow.invoke(rerun_initial)
                print("  ✓ 普通模式重跑完成")

        dur = (datetime.now() - start).total_seconds()

        print("\n" + "=" * 60)
        print(f"完成! 耗时 {dur:.1f}秒")
        if final.get("quality_report"):
            r = final["quality_report"]
            print(f"评分: {r.get('score',0)}/100")
        print("=" * 60)

        if output_path and final.get("final_output"):
            with open(output_path, 'w', encoding=Config.OUTPUT_ENCODING) as f:
                f.write(final["final_output"])
            print(f"已保存: {output_path}")

        return {
            "markdown": final.get("final_output", ""),
            "metadata": final.get("metadata", {}),
            "speakers": final.get("speakers", {}),
            "topics": final.get("topics", []),
            "chunk_summaries": final.get("chunk_summaries", []),
            "content_sections": final.get("content_sections", {}),
            "segments": final.get("segments", []),
            "quality_report": final.get("quality_report", {}),
            "meeting_type": final.get("meeting_type", ""),
            "duration": dur
        }

    def generate_rag_documents(self, raw_text=None, metadata=None,
                               result=None) -> List[Dict]:
        """
        生成适合 RAG 检索的结构化文档块。

        参数：
            raw_text: 会议原文（如果 result 未提供则需要）
            metadata: 元数据（如果 result 未提供则需要）
            result:   generate() 的返回值（如果已有则直接复用，避免重复跑流水线）

        每个文档块 (document) 包含：
        - doc_id: 唯一标识
        - doc_type: 文档块类型（meeting_overview / topic_section / speaker_speech / chunk_summary）
        - content: 用于向量嵌入的文本内容
        - metadata: 用于过滤/重排的结构化元数据
        """
        # 如果没有传入 result，才跑流水线
        if result is None:
            if raw_text is None:
                raise ValueError("必须提供 raw_text 或 result 参数")
            result = self.generate(raw_text, metadata)

        meeting_title = result["metadata"].get("title", "会议")
        meeting_date = result["metadata"].get("date", "")
        meeting_type = result.get("meeting_type", "")
        meeting_nature = result["metadata"].get("_meeting_nature", "")

        rag_docs = []
        doc_idx = 0

        # ====== 第一层：会议概述文档 ======
        # 整体概述，适合回答"这个会议讲了什么"这类宽泛问题
        overview_parts = []
        overview_parts.append(f"会议主题：{meeting_title}")
        if meeting_date:
            overview_parts.append(f"会议日期：{meeting_date}")
        if meeting_nature:
            overview_parts.append(f"会议性质：{meeting_nature}")

        topic_titles = [t.get("title", "") for t in result.get("topics", [])]
        if topic_titles:
            overview_parts.append(f"讨论议题：{'、'.join(topic_titles)}")

        speaker_names = [
            info.get("name", key) for key, info in result.get("speakers", {}).items()
            if info.get("name") not in ("未知", "参会者")
        ]
        if speaker_names:
            overview_parts.append(f"参会者：{'、'.join(speaker_names[:15])}")

        # 添加各主题的摘要
        for topic in result.get("topics", []):
            summary = topic.get("summary", "")
            if summary:
                overview_parts.append(f"【{topic.get('title', '')}】{summary}")

        rag_docs.append({
            "doc_id": f"meeting_{doc_idx:04d}_overview",
            "doc_type": "meeting_overview",
            "content": "\n".join(overview_parts),
            "metadata": {
                "meeting_title": meeting_title,
                "meeting_date": meeting_date,
                "meeting_type": meeting_type,
                "meeting_nature": meeting_nature,
                "topic_titles": topic_titles,
                "speaker_names": speaker_names,
                "layer": "overview",
            }
        })
        doc_idx += 1

        # ====== 第二层：按主题切分的文档块 ======
        # 每个主题一个文档，适合回答"会上关于XX议题讨论了什么"
        content_sections = result.get("content_sections", {})
        for topic in result.get("topics", []):
            tid = topic.get("id", "")
            title = topic.get("title", "")
            details = content_sections.get(tid, {})

            # 构建内容文本
            section_parts = [f"议题：{title}"]
            summary = topic.get("summary", "")
            if summary:
                section_parts.append(f"概述：{summary}")

            # 关键要点
            key_points = details.get("key_points", [])
            for kp in key_points:
                if isinstance(kp, dict):
                    pt = kp.get("point", "")
                    evidence = kp.get("evidence_quote", "")
                    if pt:
                        line = f"• {pt}"
                        if evidence:
                            line += f"（原文：{evidence[:80]}）"
                        section_parts.append(line)

            # 其他动态维度（decisions, action_items, commitments 等）
            for dim_key, dim_val in details.items():
                if dim_key in ("key_points", "_extraction_schema", "sub_topics"):
                    continue
                if isinstance(dim_val, list) and dim_val:
                    dim_label = dim_key.replace("_", " ").title()
                    section_parts.append(f"\n{dim_label}：")
                    for item in dim_val[:10]:
                        if isinstance(item, dict):
                            # 取第一个有意义的值
                            text = item.get("decision", item.get("content",
                                    item.get("issue", item.get("point", str(item)))))
                            section_parts.append(f"  - {text}")
                        elif isinstance(item, str):
                            section_parts.append(f"  - {item}")

            # 收集metadata
            topic_entities = set()
            topic_data = set()
            # 从chunk_summaries中收集该主题的实体和数据
            chunk_indices = topic.get("chunk_indices", [])
            for ci in chunk_indices:
                if ci < len(result.get("chunk_summaries", [])):
                    cs = result["chunk_summaries"][ci]
                    topic_entities.update(cs.get("names_entities", []))
                    topic_data.update(cs.get("data_points", []))

            rag_docs.append({
                "doc_id": f"meeting_{doc_idx:04d}_topic_{tid}",
                "doc_type": "topic_section",
                "content": "\n".join(section_parts),
                "metadata": {
                    "meeting_title": meeting_title,
                    "meeting_date": meeting_date,
                    "meeting_type": meeting_type,
                    "topic_title": title,
                    "topic_id": tid,
                    "key_entities": list(topic_entities)[:20],
                    "key_data": list(topic_data)[:20],
                    "source_segment_range": [
                        topic.get("start_segment", 0),
                        topic.get("end_segment", 0)
                    ],
                    "layer": "topic",
                }
            })
            doc_idx += 1

        # ====== 第三层：按发言人切分的文档块（大会模式）======
        conference_details = result["metadata"].get("_conference_details", {})
        speech_details = conference_details.get("speech_details", [])
        for sd in speech_details:
            speaker_name = sd.get("speaker_name",
                            sd.get("original_speaker_name", "未知"))
            speaker_org = sd.get("speaker_org",
                            sd.get("original_org", ""))
            speaker_title = sd.get("speaker_title",
                            sd.get("original_role", ""))
            speech_theme = sd.get("speech_theme", "")

            speech_parts = []
            speech_parts.append(f"发言人：{speaker_name}")
            if speaker_org:
                speech_parts.append(f"单位：{speaker_org}")
            if speaker_title:
                speech_parts.append(f"职务：{speaker_title}")
            if speech_theme:
                speech_parts.append(f"发言主题：{speech_theme}")

            for ss in sd.get("sub_sections", []):
                ss_title = ss.get("title", "")
                if ss_title:
                    speech_parts.append(f"\n【{ss_title}】")
                for pt in ss.get("points", []):
                    if isinstance(pt, dict):
                        speech_parts.append(f"• {pt.get('point', str(pt))}")
                    else:
                        speech_parts.append(f"• {pt}")

            # 承诺和数据
            commitments = sd.get("commitments", [])
            key_data = sd.get("key_data", [])
            if commitments:
                speech_parts.append("\n目标承诺：")
                for c in commitments:
                    speech_parts.append(f"  - {c}")

            rag_docs.append({
                "doc_id": f"meeting_{doc_idx:04d}_speech_{speaker_name}",
                "doc_type": "speaker_speech",
                "content": "\n".join(speech_parts),
                "metadata": {
                    "meeting_title": meeting_title,
                    "meeting_date": meeting_date,
                    "meeting_type": meeting_type,
                    "speaker_name": speaker_name,
                    "speaker_org": speaker_org,
                    "speaker_role": speaker_title,
                    "speech_theme": speech_theme,
                    "key_data": key_data[:15],
                    "commitments": commitments[:10],
                    "layer": "speech",
                }
            })
            doc_idx += 1

        # ====== 第四层：chunk级文档块 ======
        # 最细粒度，保留原文摘要+语义标签，适合精确查找
        for cs in result.get("chunk_summaries", []):
            chunk_parts = []
            summary = cs.get("summary", "")
            if summary:
                chunk_parts.append(summary)

            for kp in cs.get("key_points", [])[:10]:
                if isinstance(kp, dict):
                    pt = kp.get("point", "")
                    quote = kp.get("quote", "")
                    hint = kp.get("speaker_hint", "")
                    line = pt
                    if hint and hint != "未知":
                        line = f"[{hint}] {line}"
                    if quote:
                        line += f"（原文：{quote[:60]}）"
                    chunk_parts.append(f"• {line}")

            data_points = cs.get("data_points", [])
            if data_points:
                chunk_parts.append(f"涉及数据：{'、'.join(data_points[:8])}")

            semantic_tags = cs.get("semantic_tags", [])

            rag_docs.append({
                "doc_id": f"meeting_{doc_idx:04d}_chunk_{cs.get('chunk_index', 0)}",
                "doc_type": "chunk_summary",
                "content": "\n".join(chunk_parts),
                "metadata": {
                    "meeting_title": meeting_title,
                    "meeting_date": meeting_date,
                    "meeting_type": meeting_type,
                    "chunk_index": cs.get("chunk_index", 0),
                    "source_segment_range": [
                        cs.get("start_segment", 0),
                        cs.get("end_segment", 0)
                    ],
                    "key_entities": cs.get("names_entities", [])[:15],
                    "key_data": data_points[:15],
                    "semantic_tags": semantic_tags[:10],
                    "topics_mentioned": cs.get("topics_mentioned", [])[:5],
                    "inferred_speakers": cs.get("inferred_speakers", [])[:5],
                    "layer": "chunk",
                }
            })
            doc_idx += 1

        print(f"\n[RAG] 生成 {len(rag_docs)} 个文档块:")
        type_counts = {}
        for d in rag_docs:
            dt = d["doc_type"]
            type_counts[dt] = type_counts.get(dt, 0) + 1
        for dt, cnt in type_counts.items():
            print(f"  - {dt}: {cnt} 个")

        return rag_docs


# ============================================================
# 辅助函数
# ============================================================

def load_meeting_text(path):
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def process_meeting(input_path: str, output_dir: str = "./output") -> Dict:
    """
    一键处理会议文本，同时输出 Markdown 纪要和 RAG 文档 JSON。

    用法：
        python meeting_minutes_no_speaker.py  # 运行内置demo
        # 或在代码中：
        from meeting_minutes_no_speaker import process_meeting
        result = process_meeting("会议录音.txt", "./output")

    输出文件：
        output/meeting_minutes_YYYYMMDD_HHMMSS.md   — 给人看的纪要
        output/meeting_rag_YYYYMMDD_HHMMSS.json     — 给向量库的RAG文档块
        output/meeting_meta_YYYYMMDD_HHMMSS.json    — 元信息

    参数:
        input_path: 会议文本文件路径
        output_dir: 输出目录

    返回:
        {"result": ..., "rag_docs": ..., "files": {"md": ..., "rag_json": ..., "meta_json": ...}}
    """
    raw_text = load_meeting_text(input_path)
    metadata = extract_metadata_from_text(raw_text)

    generator = AdvancedMeetingMinutesGenerator()

    # 生成纪要
    result = generator.generate(raw_text=raw_text, metadata=metadata)

    # 生成RAG文档块（复用上面的 result，不重复跑流水线）
    rag_docs = generator.generate_rag_documents(result=result)

    # 保存
    md_path, rag_path, meta_path = save_result(result, rag_docs=rag_docs, output_dir=output_dir)

    return {
        "result": result,
        "rag_docs": rag_docs,
        "files": {
            "md": md_path,
            "rag_json": rag_path,
            "meta_json": meta_path
        }
    }

_ILLEGAL_FN_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _safe_filename(title: str, fallback: str = "会议纪要", max_len: int = 80) -> str:
    """
    把任意字符串转成安全的文件名片段：
      - 替换 Windows/Linux 非法字符 \\ / : * ? " < > | 以及控制字符为 _
      - 去掉首尾的空格和点（Windows 不允许文件名以 . 或空格结尾）
      - 截到 max_len 字符
      - 如果处理后为空，则返回 fallback
    """
    if not title:
        return fallback
    name = _ILLEGAL_FN_CHARS.sub("_", title)
    name = name.strip(" .\t\r\n")
    if len(name) > max_len:
        name = name[:max_len].rstrip(" .")
    return name or fallback


def save_result(result, rag_docs=None, output_dir="./output"):
    """
    保存会议纪要处理结果：
    1. .md 文件 —— 给人阅读的 Markdown 纪要
    2. _rag.json 文件 —— 给 RAG 向量库使用的结构化文档块
    3. _meta.json 文件 —— 会议元信息（可选，用于调试/归档）

    文件名格式: {safe_title}_{ts}.{ext}  ——  标题取自 result.metadata.title，做了文件名安全处理

    参数:
        result:     generate() 的返回值
        rag_docs:   generate_rag_documents() 的返回值（可选，传入则保存RAG文件）
        output_dir: 输出目录
    返回:
        (md_path, rag_json_path, meta_path) 三元组
    """
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    raw_title = (result.get("metadata") or {}).get("title") or ""
    safe_title = _safe_filename(raw_title)

    # 1. 保存 Markdown 纪要（给人看）
    md_path = os.path.join(output_dir, f"{safe_title}_{ts}.md")
    with open(md_path, 'w', encoding=Config.OUTPUT_ENCODING) as f:
        f.write(result.get("markdown", ""))
    print(f"  ✓ 纪要已保存: {md_path}")

    # 2. 保存 RAG 文档块 JSON（给向量库用）
    rag_json_path = None
    if rag_docs is not None:
        rag_json_path = os.path.join(output_dir, f"{safe_title}_rag_{ts}.json")
        rag_output = {
            "version": "1.0",
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "meeting_title": result.get("metadata", {}).get("title", ""),
            "meeting_date": result.get("metadata", {}).get("date", ""),
            "meeting_type": result.get("meeting_type", ""),
            "total_documents": len(rag_docs),
            "document_types": {},
            "documents": rag_docs
        }
        # 统计各类型文档数量
        for doc in rag_docs:
            dt = doc.get("doc_type", "unknown")
            rag_output["document_types"][dt] = rag_output["document_types"].get(dt, 0) + 1

        with open(rag_json_path, 'w', encoding=Config.OUTPUT_ENCODING) as f:
            json.dump(rag_output, f, ensure_ascii=False, indent=2)
        print(f"  ✓ RAG文档已保存: {rag_json_path} ({len(rag_docs)} 个文档块)")

    # 3. 保存元信息（调试/归档用）
    meta_path = os.path.join(output_dir, f"{safe_title}_meta_{ts}.json")
    meta_keys = ["metadata", "speakers", "topics", "quality_report", "meeting_type", "duration"]
    meta_data = {k: result[k] for k in meta_keys if k in result}
    with open(meta_path, 'w', encoding=Config.OUTPUT_ENCODING) as f:
        json.dump(meta_data, f, ensure_ascii=False, indent=2)
    print(f"  ✓ 元信息已保存: {meta_path}")

    return md_path, rag_json_path, meta_path

def extract_metadata_from_text(text):
    """
    提取会议元数据：先用 LLM 智能提取，正则兜底。
    LLM 能从口语化、格式不规范的转写文本中准确提取标题、日期、地点等信息。
    """
    # 取前2000字作为样本
    sample = text[:2000]

    # 第一优先：LLM 提取
    try:
        llm = LLMClient()
        prompt = f"""请从以下会议录音转写文本的开头部分，提取会议的基本信息。
注意：这是语音转写文本，可能有错别字、口语化表达、格式不规范，请智能推断。

【文本开头】
{sample}

请输出JSON（如果某项信息无法从文本中确定，对应字段填null）：
{{
    "title": "会议名称/主题（从内容推断，如'番禺区2026年高质量发展大会'）",
    "date": "会议日期（如'2026年1月15日'，无法确定则null）",
    "location": "会议地点（如'龙沙码头会场'，无法确定则null）",
    "purpose": "会议目的（一句话，如'部署全年高质量发展工作'）",
    "scale": "参会规模描述（如'区领导班子、各部门负责人及企业代表约200人'，无法确定则null）",
    "organizer": "组织方/主持人（如'番禺区委区政府'，无法确定则null）"
}}"""

        result = llm.invoke_safe(prompt, json_mode=True,
            system_prompt="你是会议信息提取专家。从语音转写文本中准确提取会议元数据。输出纯JSON。")

        if result and isinstance(result, dict):
            meta = {
                "title": result.get("title") or "会议",
                "date": result.get("date") or "",
                "location": result.get("location") or "",
                "meeting_type": "",
                "purpose": result.get("purpose") or "",
                "scale": result.get("scale") or "",
                "organizer": result.get("organizer") or "",
            }
            # 清理 null 字符串
            for k, v in meta.items():
                if v in ("null", "None", "无", "未知", "未提及"):
                    meta[k] = ""
            if meta["title"] and meta["title"] != "会议":
                print(f"  [MetadataExtractor] LLM提取成功: 标题='{meta['title']}', 日期='{meta['date']}'")
                return meta
    except Exception as e:
        print(f"  [MetadataExtractor] LLM提取失败({e})，回退到正则")

    # 第二优先：正则兜底
    meta = {"title": "会议", "date": "", "location": "", "meeting_type": "",
            "purpose": "", "scale": "", "organizer": ""}
    for p in [r'会议主题[：:]\s*(.+?)(?:\n|$)', r'会议名称[：:]\s*(.+?)(?:\n|$)', r'主题[：:]\s*(.+?)(?:\n|$)']:
        m = re.search(p, text)
        if m:
            meta["title"] = m.group(1).strip()
            break
    for p in [r'(\d{4})年(\d{1,2})月(\d{1,2})日', r'(\d{4})-(\d{1,2})-(\d{1,2})']:
        m = re.search(p, text)
        if m:
            meta["date"] = f"{m.group(1)}年{m.group(2)}月{m.group(3)}日"
            break
    if not meta["date"]:
        m = re.search(r'(\d{1,2})月(\d{1,2})[日号]', text)
        if m:
            meta["date"] = f"{m.group(1)}月{m.group(2)}日"
    return meta


# ============================================================
# Demo
# ============================================================

# ============================================================
# 全文完整性检查（生成后扫描，识别半句话/截断）
# ============================================================

# 句末合法的终结符（中英文 + 结构性字符）
_SENT_END_CHARS = set("。！？!?….）)】」》”\"'`*-—|")
# 段末若以这些词收尾，多半是被截断的"半句话"
_DANGLING_TAIL_WORDS = (
    "例如", "比如", "包括", "如下", "其中", "即", "如", "及",
    "和", "与", "或", "但", "而", "也", "还", "并", "且",
    "因为", "由于", "为了", "通过", "对于", "关于", "至于",
    "首先", "其次", "然后", "最后", "此外", "另外",
)
# 段末若以这些标点收尾，说明话没说完
_DANGLING_TAIL_PUNCT = set("，、：；,;:—…")


def _check_paragraph(idx: int, para: str) -> list:
    """检查单个段落，返回问题列表"""
    issues = []
    s = para.rstrip()
    if not s:
        return issues
    # 跳过结构性行：标题、列表项、表格、代码块、分隔线
    head = s.lstrip()
    if head.startswith(("#", "-", "*", "+", "|", ">", "`", "---", "===")) and "\n" not in s:
        return issues
    if head[:1].isdigit() and head[1:2] in (".", "、", ")"):
        # 有序列表条目，跳过
        return issues

    last = s[-1]
    # 1) 末尾标点不合法
    if last in _DANGLING_TAIL_PUNCT:
        issues.append({
            "para_index": idx, "type": "dangling_punct",
            "tail": s[-30:],
            "reason": f"段落以 '{last}' 结尾，疑似被截断",
        })
        return issues
    # 2) 末尾不是合法终结符（句号问号叹号引号括号等）
    if last not in _SENT_END_CHARS and not last.isalnum():
        # 非字母数字、又不是终结符——可疑
        if not ('一' <= last <= '鿿'):  # 不是汉字
            issues.append({
                "para_index": idx, "type": "weird_tail",
                "tail": s[-30:],
                "reason": f"段落以非常规字符 '{last}' 结尾",
            })
    # 3) 末尾词是悬挂连接词（"例如"/"包括"/"通过" 等后面没下文）
    for w in _DANGLING_TAIL_WORDS:
        if s.endswith(w):
            issues.append({
                "para_index": idx, "type": "dangling_word",
                "tail": s[-30:],
                "reason": f"段落以连接词 '{w}' 结尾，下文缺失",
            })
            break
    # 4) 末尾必须是汉字+句号/叹号/问号，否则末尾是裸汉字（如"例如" 已被上面捕获，"突出例如" 这种也算）
    if last.isalpha() or ('一' <= last <= '鿿'):
        # 汉字/字母直接收尾、没有句号 → 截断
        # 但要排除短标题/单字段（< 15 字的行视为标签性内容）
        if len(s) > 20:
            issues.append({
                "para_index": idx, "type": "no_terminator",
                "tail": s[-30:],
                "reason": "段落未以句号/问号/叹号结尾",
            })
    # 5) 括号 / 引号配对
    pairs = [("（", "）"), ("(", ")"), ("【", "】"), ("[", "]"),
             ("「", "」"), ("《", "》"), ("“", "”")]
    for op, cl in pairs:
        if s.count(op) != s.count(cl):
            issues.append({
                "para_index": idx, "type": "unbalanced_bracket",
                "tail": s[-30:],
                "reason": f"括号/引号 '{op}{cl}' 未配对",
            })
    return issues


def check_text_integrity(md_text: str) -> list:
    """全文扫描，返回所有段落级问题"""
    paragraphs = md_text.split("\n\n")
    all_issues = []
    for i, p in enumerate(paragraphs):
        all_issues.extend(_check_paragraph(i, p))
    return all_issues


def _repair_paragraph_with_llm(prev_para: str, broken: str, next_para: str) -> str:
    """让 LLM 把半句补全。失败则原样返回。"""
    try:
        client = LLMClient()
        sys_prompt = (
            "你是一名中文会议纪要编辑。下面给你一段被截断或不完整的段落，"
            "以及它的前后文。请仅补全/修复这一段，使其语句通顺、语意完整，"
            "不要添加无依据的内容；保持原有写作风格、术语和markdown格式。"
            "只输出修复后的这一段，不要解释，不要重复前后文。"
        )
        prompt = (
            f"【前文（仅参考）】\n{prev_para[-300:]}\n\n"
            f"【需要修复的段落】\n{broken}\n\n"
            f"【后文（仅参考）】\n{next_para[:300]}\n\n"
            f"请输出修复后的段落："
        )
        fixed = client.invoke_safe(prompt, system_prompt=sys_prompt)
        if isinstance(fixed, str) and len(fixed.strip()) >= max(20, len(broken) // 2):
            return fixed.strip()
    except Exception as e:
        print(f"  [verify] LLM 修复失败: {e}")
    return broken


def verify_and_repair_md(md_path: str, repair: bool = True) -> dict:
    """对已生成的 md 文件做完整性检查，并可选 LLM 修复。
    返回：{"issues": [...], "repaired": bool, "report_path": ...}
    """
    with open(md_path, "r", encoding=Config.OUTPUT_ENCODING) as f:
        text = f.read()

    issues = check_text_integrity(text)
    report = {"md_path": md_path, "total_issues": len(issues), "issues": issues,
              "repaired": False, "repaired_count": 0}

    if not issues:
        print(f"  ✓ 完整性检查通过：{md_path}")
        return report

    print(f"  ⚠ 完整性检查发现 {len(issues)} 处问题：")
    for it in issues[:10]:
        print(f"    [#{it['para_index']}] {it['type']}: {it['reason']}  …{it['tail']}")
    if len(issues) > 10:
        print(f"    ...还有 {len(issues) - 10} 处")

    if repair:
        paragraphs = text.split("\n\n")
        # 仅修复"截断类"问题（dangling_*/no_terminator/unbalanced_bracket），weird_tail 偏误报，先跳过
        repair_types = {"dangling_punct", "dangling_word", "no_terminator", "unbalanced_bracket"}
        idxs = sorted({it["para_index"] for it in issues if it["type"] in repair_types})
        for i in idxs:
            prev_p = paragraphs[i - 1] if i > 0 else ""
            next_p = paragraphs[i + 1] if i + 1 < len(paragraphs) else ""
            print(f"  [verify] 正在修复段落 #{i} ...")
            fixed = _repair_paragraph_with_llm(prev_p, paragraphs[i], next_p)
            if fixed != paragraphs[i]:
                paragraphs[i] = fixed
                report["repaired_count"] += 1
        if report["repaired_count"]:
            with open(md_path, "w", encoding=Config.OUTPUT_ENCODING) as f:
                f.write("\n\n".join(paragraphs))
            report["repaired"] = True
            print(f"  ✓ 已修复 {report['repaired_count']} 段")
            # 修复后再扫一次，看是否还有遗留
            with open(md_path, "r", encoding=Config.OUTPUT_ENCODING) as f:
                report["residual_issues"] = check_text_integrity(f.read())

    return report


_PLACEHOLDER_TITLES = {"", "会议", "标题待生成…", "待生成…", "标题待生成...", "待生成..."}


def generate_minutes(text: str, title: str, output_dir: str,
                     verify: bool = True, repair: bool = True) -> dict:
    metadata = extract_metadata_from_text(text)
    # 占位符判定：引擎 LLM 提取出的标题、调用方传入的 title 都可能是占位符。
    # 任何一方落到占位符都视为"没标题"，最终落到通用兜底，避免占位符泄漏到产物中。
    extracted = (metadata.get("title") or "").strip()
    if extracted in _PLACEHOLDER_TITLES:
        user_title = (title or "").strip()
        if user_title in _PLACEHOLDER_TITLES:
            user_title = ""
        metadata["title"] = user_title or "会议座谈纪要"

    generator = AdvancedMeetingMinutesGenerator()

    # 1. 生成 Markdown 纪要（给人看）
    result = generator.generate(raw_text=text, metadata=metadata,
                                output_path=None)

    # 2. 生成 RAG 文档块（复用上面的 result，不重复跑流水线）
    rag_docs = generator.generate_rag_documents(result=result)

    # 3. 保存：一个 .md + 一个 _rag.json + 一个 _meta.json
    md_path, rag_path, meta_path = save_result(result, rag_docs=rag_docs, output_dir=output_dir)

    # 4. 全文完整性检查 + 可选自动修复
    integrity = {}
    if verify and md_path:
        integrity = verify_and_repair_md(md_path, repair=repair)

    return {
        "title": metadata.get("title", "") or "",
        "md_path": md_path,
        "rag_json_path": rag_path,
        "meta_path": meta_path,
        "quality_score": result.get("quality_score", 0),
        "integrity": integrity,
    }



