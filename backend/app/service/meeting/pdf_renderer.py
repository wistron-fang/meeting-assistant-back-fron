#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
会议纪要 Markdown → PDF 精美排版转换器（reportlab 版）

直接用 reportlab 排版引擎，不依赖 WeasyPrint / GTK / xhtml2pdf。
Windows / Mac / Linux 均可，中文渲染无乱码。

依赖安装：
  pip install reportlab fonttools markdown

用法：
  python md_to_pdf.py 会议纪要.md
  python md_to_pdf.py 会议纪要.md -o ./pdf报告/
  python md_to_pdf.py ./output/                  # 批量
"""

import os, re, platform, tempfile
from typing import Optional, List, Tuple

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm, mm
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.colors import HexColor
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak,
    Table, TableStyle, HRFlowable, KeepTogether,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# 中文避头尾（kinsoku）：扩展不能出现在行首/行尾的标点集合
try:
    from reportlab.lib import textsplit as _textsplit
    _EXTRA_CANNOT_START = '，。、；：？！）】》」』〗〕〉"\'"%℃°'
    _EXTRA_CANNOT_END = '（【《「『〖〔〈"\''
    _cs = getattr(_textsplit, 'ALL_CANNOT_START', '')
    for _c in _EXTRA_CANNOT_START:
        if _c not in _cs:
            _cs = _cs + _c
    _textsplit.ALL_CANNOT_START = _cs
    _ce = getattr(_textsplit, 'ALL_CANNOT_END', '')
    for _c in _EXTRA_CANNOT_END:
        if _c not in _ce:
            _ce = _ce + _c
    _textsplit.ALL_CANNOT_END = _ce
except Exception:
    pass


# ============================================================
# 字体管理
# ============================================================

_FONT_CACHE_DIR = os.path.join(tempfile.gettempdir(), "md2pdf_fonts")
_FONT_REGISTERED = False
FONT_NAME = "ZHFont"
FONT_BOLD = "ZHFont"  # 很多中文字体没有独立 Bold，复用同一个


def _find_font_source() -> Optional[str]:
    """查找系统中文字体。

    顺序：① 各平台常见显式路径（含 alinux/RHEL/CentOS/Debian/Ubuntu）；
    ② 扫描常见字体目录里任意 CJK 字体；③ fc-list 动态查询系统已装中文字体
    （最通用——只要系统装了任意中文字体就能用，无需软链到写死路径）。
    """
    import glob
    import subprocess

    system = platform.system()
    candidates: List[str] = []
    if system == "Windows":
        fd = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        candidates = [
            os.path.join(fd, "msyh.ttc"), os.path.join(fd, "msyh.ttf"),
            os.path.join(fd, "simsun.ttc"), os.path.join(fd, "simhei.ttf"),
            os.path.join(fd, "simkai.ttf"), os.path.join(fd, "STSONG.TTF"),
        ]
    elif system == "Darwin":
        candidates = [
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/Library/Fonts/Songti.ttc",
        ]
    else:
        candidates = [
            # Debian / Ubuntu
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            # RHEL / Alibaba Cloud Linux / CentOS / Fedora
            "/usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc",
            "/usr/share/fonts/wqy-microhei/wqy-microhei.ttc",
            "/usr/share/fonts/google-noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/google-noto-sans-cjk-fonts/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/google-noto-sans-cjk-vf-fonts/NotoSansCJK-VF.ttc",
        ]

    for p in candidates:
        if os.path.exists(p):
            return p

    # ② 扫描常见字体目录，按文件名匹配任意 CJK 字体（递归）
    if system not in ("Windows", "Darwin"):
        name_pats = [
            "wqy*", "*NotoSansCJK*", "*NotoSerifCJK*", "*NotoSansSC*", "*NotoSansMonoCJK*",
            "*SourceHanSans*", "*SourceHanSerif*", "*uming*", "*ukai*", "*droidsansfallback*",
        ]
        font_dirs = [
            "/usr/share/fonts", "/usr/local/share/fonts",
            os.path.expanduser("~/.fonts"), os.path.expanduser("~/.local/share/fonts"),
        ]
        for d in font_dirs:
            if not os.path.isdir(d):
                continue
            for pat in name_pats:
                for ext in ("ttf", "otf", "ttc"):  # ttf/otf 优先，reportlab 直接可用
                    hits = glob.glob(os.path.join(d, "**", f"{pat}.{ext}"), recursive=True)
                    if hits:
                        return sorted(hits)[0]

    # ③ fc-list 动态查询：系统装了任意中文字体即可命中（最通用）
    try:
        out = subprocess.run(
            ["fc-list", ":lang=zh", "file"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        zh_fonts = []
        for line in out.splitlines():
            path = line.split(":")[0].strip()
            if path and path.lower().endswith((".ttf", ".otf", ".ttc")) and os.path.exists(path):
                zh_fonts.append(path)
        # 优先非 .ttc（省去 fonttools 抽取这步），否则取第一个
        for path in zh_fonts:
            if not path.lower().endswith(".ttc"):
                return path
        if zh_fonts:
            return zh_fonts[0]
    except Exception:
        pass

    return None


def _ensure_ttf(source: str) -> Optional[str]:
    """如果是 .ttc 则提取为 .ttf（reportlab 对 .ttc 的 CFF 支持不稳定）"""
    if source.lower().endswith(".ttf"):
        return source

    basename = os.path.splitext(os.path.basename(source))[0]
    ttf_path = os.path.join(_FONT_CACHE_DIR, f"{basename}.ttf")
    if os.path.exists(ttf_path):
        return ttf_path

    try:
        from fontTools.ttLib import TTFont as FTFont
        os.makedirs(_FONT_CACHE_DIR, exist_ok=True)
        f = FTFont(source, fontNumber=0)
        f.save(ttf_path)
        f.close()
        return ttf_path
    except ImportError:
        print("  ⚠ 需要 fonttools 处理 .ttc 字体: pip install fonttools")
        # 尝试直接用 reportlab 加载 TTC（部分版本支持）
        try:
            pdfmetrics.registerFont(TTFont(FONT_NAME, source, subfontIndex=0))
            return source
        except Exception:
            return None
    except Exception as e:
        print(f"  ⚠ 字体提取失败: {e}")
        return None


def register_font():
    """注册中文字体到 reportlab（只执行一次）"""
    global _FONT_REGISTERED, FONT_NAME, FONT_BOLD
    if _FONT_REGISTERED:
        return

    source = _find_font_source()
    if source:
        ttf = _ensure_ttf(source)
        if ttf:
            try:
                pdfmetrics.registerFont(TTFont(FONT_NAME, ttf))
                _FONT_REGISTERED = True
                print(f"  字体: {os.path.basename(source)}")
                return
            except Exception as e:
                print(f"  ⚠ 字体注册失败: {e}")

    print("  ⚠ 未找到中文字体，PDF中文可能显示异常")
    FONT_NAME = "Helvetica"
    FONT_BOLD = "Helvetica-Bold"
    _FONT_REGISTERED = True


# ============================================================
# 样式定义
# ============================================================

def create_styles() -> dict:
    """创建全套排版样式"""
    register_font()
    s = {}

    # 封面
    s["cover_title"] = ParagraphStyle(
        "CoverTitle", fontName=FONT_NAME, fontSize=28, leading=40,
        alignment=TA_CENTER, textColor=HexColor("#1a5276"), spaceAfter=10,
    )
    s["cover_sub"] = ParagraphStyle(
        "CoverSub", fontName=FONT_NAME, fontSize=14, leading=20,
        alignment=TA_CENTER, textColor=HexColor("#555555"), spaceAfter=30,
    )
    s["cover_meta"] = ParagraphStyle(
        "CoverMeta", fontName=FONT_NAME, fontSize=22, leading=32,
        alignment=TA_CENTER, textColor=HexColor("#444444"), spaceAfter=4,
    )

    # 正文
    s["body"] = ParagraphStyle(
        "Body", fontName=FONT_NAME, fontSize=11, leading=20,
        firstLineIndent=22, alignment=TA_JUSTIFY,
        spaceBefore=2, spaceAfter=2, wordWrap="CJK",
        textColor=HexColor("#222222"),
    )
    s["body_noi"] = ParagraphStyle(
        "BodyNoIndent", fontName=FONT_NAME, fontSize=11, leading=20,
        alignment=TA_JUSTIFY, spaceBefore=2, spaceAfter=2, wordWrap="CJK",
        textColor=HexColor("#222222"),
    )
    # 小标题（段首加粗标签 **xxx**：识别为小节标题）
    s["h5"] = ParagraphStyle(
        "H5", fontName=FONT_NAME, fontSize=11, leading=18,
        textColor=HexColor("#1f618d"),
        spaceBefore=6, spaceAfter=2,
        leftIndent=0,
    )

    # 标题
    s["h1"] = ParagraphStyle(
        "H1", fontName=FONT_NAME, fontSize=20, leading=28,
        alignment=TA_CENTER, textColor=HexColor("#1a1a2e"),
        spaceBefore=10, spaceAfter=8,
        borderPadding=(0, 0, 6, 0), borderWidth=0,
        borderColor=HexColor("#1a5276"),
    )
    s["h2"] = ParagraphStyle(
        "H2", fontName=FONT_NAME, fontSize=15, leading=24,
        textColor=HexColor("#1a5276"),
        spaceBefore=16, spaceAfter=6, leftIndent=0,
        borderPadding=(2, 0, 2, 8),
    )
    s["h3"] = ParagraphStyle(
        "H3", fontName=FONT_NAME, fontSize=12.5, leading=20,
        textColor=HexColor("#2c3e50"),
        spaceBefore=10, spaceAfter=4,
    )
    s["h4"] = ParagraphStyle(
        "H4", fontName=FONT_NAME, fontSize=11.5, leading=18,
        textColor=HexColor("#34495e"),
        spaceBefore=8, spaceAfter=3,
    )

    # 列表
    s["li"] = ParagraphStyle(
        "ListItem", fontName=FONT_NAME, fontSize=11, leading=20,
        leftIndent=24, bulletIndent=12, wordWrap="CJK",
        alignment=TA_JUSTIFY,
        spaceBefore=1, spaceAfter=1,
    )
    s["li_num"] = ParagraphStyle(
        "NumItem", fontName=FONT_NAME, fontSize=11, leading=20,
        leftIndent=24, wordWrap="CJK", alignment=TA_JUSTIFY,
        spaceBefore=1, spaceAfter=1,
    )

    # 引用
    s["quote"] = ParagraphStyle(
        "Quote", fontName=FONT_NAME, fontSize=10, leading=16,
        leftIndent=16, textColor=HexColor("#666666"),
        spaceBefore=6, spaceAfter=6, wordWrap="CJK",
        borderPadding=(6, 8, 6, 10),
    )

    return s


# ============================================================
# Markdown 解析 → reportlab Story
# ============================================================

def extract_meeting_info(md_text: str) -> dict:
    info = {"title": "会议纪要", "date": "", "meeting_type": ""}
    m = re.search(r'^#\s+(.+)$', md_text, re.MULTILINE)
    if m:
        t = re.sub(r'\*+', '', m.group(1)).strip()
        if t:
            info["title"] = t
    m = re.search(r'\*\*日期\*\*[：:]\s*(.+)', md_text)
    if m:
        info["date"] = m.group(1).strip()
    else:
        m = re.search(r'(\d{4}年\d{1,2}月\d{1,2}日)', md_text)
        if m:
            info["date"] = m.group(1)
    m = re.search(r'\*\*会议类型\*\*[：:]\s*(.+)', md_text)
    if m:
        info["meeting_type"] = m.group(1).strip()
    return info


# 数字 / 关键量词 自动高亮（红色加粗）
_NUM_RE = re.compile(
    r'(\d+(?:[\.,]\d+)?(?:\s*[至~\-]\s*\d+(?:[\.,]\d+)?)?'
    r'\s*(?:亿元|亿港元|亿|万元|万|％|%|公里|平方公里|平方米|项|年|月|日|个|倍|次|条|'
    r'港元|元|岁|名|人|家|号|期|届|栋|套|间|kg|km|m)'
    r'|\d{3,}(?:[\.,]\d+)?)'
)
# 段首加粗标签（如 **关键数据**：或 **主要成果**）识别，冒号可选
_SUBHEAD_RE = re.compile(r'^\*\*([^*\n]{2,40})\*\*\s*[：:]?\s*$')


def _highlight_numbers(text: str) -> str:
    return _NUM_RE.sub(lambda m: f'\x00H\x00{m.group(0)}\x00/H\x00', text)


def _clean_line(text: str, highlight: bool = False) -> str:
    """将 Markdown 行内格式转为 reportlab XML 标签"""
    # 先用占位符标记 markdown 行内格式，避免与 HTML 转义冲突
    text = re.sub(r'\*\*(.+?)\*\*', lambda m: f'\x00B\x00{m.group(1)}\x00/B\x00', text)
    text = re.sub(r'(?<!\*)\*([^*\n]+?)\*(?!\*)', lambda m: f'\x00I\x00{m.group(1)}\x00/I\x00', text)
    text = re.sub(r'`(.+?)`', lambda m: f'\x00C\x00{m.group(1)}\x00/C\x00', text)

    if highlight:
        text = _highlight_numbers(text)

    # 转义 HTML 特殊字符
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    # 还原占位符为 reportlab 内联标签
    text = (text
            .replace('\x00B\x00', '<b>').replace('\x00/B\x00', '</b>')
            .replace('\x00I\x00', '<i>').replace('\x00/I\x00', '</i>')
            .replace('\x00C\x00', '<font face="Courier" size="9">').replace('\x00/C\x00', '</font>')
            .replace('\x00H\x00', '<font color="#c0392b"><b>').replace('\x00/H\x00', '</b></font>'))
    return text


def md_to_story(md_text: str, styles: dict) -> Tuple[list, dict]:
    """将 Markdown 文本解析为 reportlab Story 元素列表"""
    info = extract_meeting_info(md_text)
    story = []

    # ====== 封面 ======
    story.append(Spacer(1, 9 * cm))
    story.append(Paragraph(info["title"], styles["cover_title"]))
    if info["date"]:
        story.append(Spacer(1, 4 * cm))
        # <b> 包裹用 reportlab 内联标签让日期加粗
        story.append(Paragraph(f"<b>{info['date']}</b>", styles["cover_meta"]))
    story.append(PageBreak())

    # ====== 正文 ======
    lines = md_text.split("\n")
    i = 0
    skip_header_block = True  # 跳过开头的元信息区
    subhead_idx = 0  # 当前 H2 章节下的小标题序号，遇到新 H2 重置

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # 跳过幻觉提示块（包含正常完整块，以及末尾残缺的 `> ⚠️` 残块）
        if stripped.startswith("> ⚠"):
            i += 1
            while i < len(lines) and lines[i].strip().startswith(">"):
                i += 1
            continue

        # 跳过开头的元信息（标题、日期、参会人员、目录等，直到第一个正文 h2）
        if skip_header_block:
            if stripped.startswith("## "):
                # 检查是不是"目录"章节——也要跳过
                h2_text = stripped[3:].strip()
                if "目录" in h2_text:
                    i += 1
                    while i < len(lines):
                        ns = lines[i].strip()
                        if ns.startswith("## ") or ns.startswith("# "):
                            break
                        i += 1
                    continue
                # 遇到第一个正文 h2，结束跳过模式
                skip_header_block = False
                # 不 continue，让下面的 h2 处理器来处理
            else:
                # 元信息区的所有内容全部跳过
                i += 1
                continue

        # 空行
        if not stripped:
            i += 1
            continue

        # h1
        if stripped.startswith("# ") and not stripped.startswith("## "):
            text = _clean_line(stripped[2:].strip())
            story.append(Paragraph(text, styles["h1"]))
            story.append(HRFlowable(width="100%", thickness=1.5, color=HexColor("#1a5276"),
                                     spaceBefore=0, spaceAfter=8))
            i += 1
            continue

        # h2
        if stripped.startswith("## "):
            text = _clean_line(stripped[3:].strip())
            # 跳过 "## 目录" 整个章节
            if "目录" in text:
                i += 1
                while i < len(lines):
                    ns = lines[i].strip()
                    if ns.startswith("## ") or ns.startswith("# "):
                        break  # 遇到下一个标题，停止跳过
                    i += 1
                continue
            subhead_idx = 0  # 进入新 H2 章节，小标题编号重置
            story.append(Spacer(1, 4 * mm))
            story.append(Paragraph(text, styles["h2"]))
            i += 1
            continue

        # h3
        if stripped.startswith("### "):
            text = _clean_line(stripped[4:].strip())
            story.append(Paragraph(text, styles["h3"]))
            i += 1
            continue

        # h4
        if stripped.startswith("#### "):
            text = _clean_line(stripped[5:].strip())
            story.append(Paragraph(text, styles["h4"]))
            i += 1
            continue

        # 引用（非幻觉提示的正常引用）
        if stripped.startswith("> "):
            quote_lines = []
            while i < len(lines) and lines[i].strip().startswith("> "):
                quote_lines.append(lines[i].strip()[2:].strip())
                i += 1
            text = _clean_line(" ".join(quote_lines))
            story.append(Paragraph(text, styles["quote"]))
            continue

        # 无序列表
        if re.match(r'^[-*]\s+', stripped):
            text = _clean_line(re.sub(r'^[-*]\s+', '', stripped))
            # 如果列表内容为空，向下合并下一行
            if not text.strip() and i + 1 < len(lines) and lines[i + 1].strip():
                next_s = lines[i + 1].strip()
                # 下一行不是新的列表项或标题
                if not next_s.startswith('#') and not re.match(r'^[-*]\s+', next_s) \
                        and not re.match(r'^\d+[.、]', next_s):
                    text = _clean_line(next_s)
                    i += 1
            if text.strip():
                story.append(Paragraph(f"•  {text}", styles["li"]))
            i += 1
            continue

        # 有序列表
        m = re.match(r'^(\d+)[.、]\s*(.*)', stripped)
        if m:
            num = m.group(1)
            text = _clean_line(m.group(2))
            # 如果编号后内容为空，向下合并下一行
            if not text.strip() and i + 1 < len(lines) and lines[i + 1].strip():
                next_s = lines[i + 1].strip()
                if not next_s.startswith('#') and not re.match(r'^[-*]\s+', next_s) \
                        and not re.match(r'^\d+[.、]', next_s):
                    text = _clean_line(next_s)
                    i += 1
            story.append(Paragraph(f"{num}. {text}", styles["li_num"]))
            i += 1
            continue

        # 分隔线
        if re.match(r'^[-*_]{3,}\s*$', stripped):
            story.append(HRFlowable(width="100%", thickness=0.5, color=HexColor("#dddddd"),
                                     spaceBefore=6, spaceAfter=6))
            i += 1
            continue

        # 目录行（跳过 Markdown 内链目录）
        if re.match(r'^\d+\.\s*\[.+\]\(#.+\)$', stripped):
            i += 1
            continue

        # 段首加粗标签 → 小标题
        sub_m = _SUBHEAD_RE.match(stripped)
        if sub_m:
            label = sub_m.group(1).strip()
            subhead_idx += 1
            story.append(Paragraph(_clean_line(f"{subhead_idx}、{label}", highlight=False), styles["h5"]))
            i += 1
            continue

        # 普通段落（合并连续非空行）
        para_lines = [stripped]
        i += 1
        while i < len(lines):
            next_stripped = lines[i].strip()
            if not next_stripped or next_stripped.startswith("#") or \
               next_stripped.startswith("> ") or next_stripped.startswith("- ") or \
               next_stripped.startswith("* ") or re.match(r'^\d+[.、]', next_stripped) or \
               re.match(r'^[-*_]{3,}', next_stripped) or _SUBHEAD_RE.match(next_stripped):
                break
            para_lines.append(next_stripped)
            i += 1

        raw = " ".join(para_lines)
        # 段首形如 **xxx**：正文…… → 拆为小标题 + 正文
        inline_sub = re.match(r'^\*\*([^*\n]{2,40})\*\*\s*[：:]\s*(.+)$', raw)
        if inline_sub:
            label = inline_sub.group(1).strip()
            rest = inline_sub.group(2).strip()
            subhead_idx += 1
            story.append(Paragraph(_clean_line(f"{subhead_idx}、{label}", highlight=False), styles["h5"]))
            text = _clean_line(rest)
            if text.strip():
                story.append(Paragraph(text, styles["body"]))
        else:
            text = _clean_line(raw)
            if text.strip():
                story.append(Paragraph(text, styles["body"]))

    return story, info


# ============================================================
# 页眉页脚
# ============================================================

class HeaderFooter:
    def __init__(self, title: str):
        self.title = title
        self.is_cover = True

    def __call__(self, canvas, doc):
        if self.is_cover:
            self.is_cover = False
            return

        register_font()
        canvas.saveState()
        w, h = A4

        # 页脚（去掉页眉）
        canvas.setFont(FONT_NAME, 8)
        canvas.setFillColor(HexColor("#999999"))
        page_num = canvas.getPageNumber() - 1  # 封面不算
        if page_num > 0:
            canvas.drawCentredString(w / 2, 1.2 * cm, f"第 {page_num} 页")

        canvas.restoreState()


# ============================================================
# 主转换函数
# ============================================================



def md_to_pdf(md_path: str, pdf_path: str) -> str:
    with open(md_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    styles = create_styles()
    story, info = md_to_story(md_text, styles)
    title = info["title"]

    os.makedirs(os.path.dirname(pdf_path) or ".", exist_ok=True)

    doc = SimpleDocTemplate(
        pdf_path, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
        title=title, author="会议纪要系统",
    )
    hf = HeaderFooter(title)
    doc.build(story, onFirstPage=hf, onLaterPages=hf)
    return pdf_path



