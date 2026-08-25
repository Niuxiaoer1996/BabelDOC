#!/usr/bin/env python3
"""toc_processor.py - 目录页（TOC / List of Tables / List of Figures）结构化处理。

背景
----
BabelDOC 默认把目录行当作普通段落整体送 LLM 翻译，导致四个问题：
1. 章节/表/图编号被误译（如 "1" -> "页码1"）；
2. 点引导线（dot leader）被 LLM 压缩或拆行；
3. 页码不再右对齐（typesetting 是纯流式布局，无右对齐机制）；
4. 页眉（Contents (cont'd) 等）被 layout 模型合并进条目。

方案
----
在 ParagraphFinder 阶段检测目录页，把每个目录条目拆成两类段落：
- 标题段（toc_role="title"）：仅标题文字（含 inline 的 "Table N -"/"6.2.1.1"
  等前缀，LLM 能可靠本地化/保留它们），送 LLM 翻译，正常流式排版；
- 布局段（toc_role="layout"）：点引导线 + 页码（以及被剥离的裸整数编号），
  不送 LLM（il_translator 跳过），由 typesetting passthrough 按原始字符坐标
  渲染 -> 点线保留、页码右对齐天然成立。

同时：
- 目录页跳过 merge_alternating_line_number_paragraphs（避免 "1" 被并进标题
  后 LLM 译成 "页码1"）与 merge_mid_sentence_continuation_paragraphs（避免
  把拆开的两行标题重新并成带点线的整段）；
- 目录页跳过 fix_overlapping_paragraphs / render_page 的避让逻辑对
  toc_role 段落的调整（标题段与布局段同处一行，y 基准会被 normalize）。

用法
----
    toc = TOCProcessor(translation_config)
    is_toc = toc.process(page)        # 在 process_independent_paragraphs 之后调用
    ...
    toc.normalize_boxes()             # 在最终 update_paragraph_data 之后调用
"""

from __future__ import annotations

import logging
import re

from babeldoc.format.pdf.document_il import il_version_1

logger = logging.getLogger(__name__)

_LEADER_CHARS = ".。\u2024\u2025"
_LEADER_RUN = re.compile(r"[" + _LEADER_CHARS + r"]{3,}")

# 完整目录条目：标题 + 3+ 点引导线 + 页码（匹配前先 strip，坐标用 lead 偏移换算）
_ENTRY_RE = re.compile(
    r"^(?P<title>.*?)"
    r"(?P<leader>[" + _LEADER_CHARS + r"]{3,})\s*"
    r"(?P<page>\d+)\s*$"
)
# 行内数字编号前缀："6.2.1.1 Internal DBIac..."（编号后跟空格）
_INLINE_NUM_RE = re.compile(r"^(?P<num>[\d.]+)\s+(?P<title>.+)$")
# 行内文字编号前缀："Table 1 – ..." / "Figure 46 – ..."
_INLINE_WORD_NUM_RE = re.compile(
    r"^(?P<num>(?:Table|FIGURE|TABLE|Figure)\s*\d+)\s*[-\u2013\u2014:.\t]\s*(?P<title>.+)$"
)
# 点线+页码 独立段：".......3"
_DOTS_PAGE_RE = re.compile(r"^[" + _LEADER_CHARS + r"]{3,}\s*(\d+)\s*$")
# 纯编号行："1" / "3.1" / "6.2.1.1"
_NUMBER_RE = re.compile(r"^[\d.]+\s*$")

# 可能被 layout 模型合并进条目的页标题前缀（顺序敏感：长的在前）
_PAGE_HEADERS = (
    "Contents (cont'd)",
    "List of Tables (cont'd)",
    "List of Figures (cont'd)",
    "Contents",
    "List of Tables",
    "List of Figures",
    "Table of Contents",
    "TABLE OF CONTENTS",
    "Pages",
    "Page",
)

_HEADER_PATTERNS = [
    re.compile(re.escape(h).replace(r"\ ", r"\s+") + r"\s*", re.IGNORECASE)
    for h in _PAGE_HEADERS
]

# 目录页判定 markers（源 PDF 侧）
_TOC_MARKERS = ("Contents", "List of Figures", "List of Tables")

# 目录续页判定：一页至少要有这么多行点线才可能被当作目录页（或目录续页）。
# 太低会把含零星点线的正文（代码示例/表格）误判；太高会漏掉条目少的目录尾页。
_TOC_MIN_DOTTED_LINES = 3


class TOCProcessor:
    """目录页结构化处理器。"""

    def __init__(self, translation_config, toc_state: dict | None = None):
        self.translation_config = translation_config
        # 跨页目录状态（由 ParagraphFinder 持有、跨页共享）：{"in_toc": bool}。
        # 用于识别"多页目录中不带 Contents marker 的续页"（如 NsightCompute 第3页、
        # NsightSystems 第3-7页），仅当上一页已是目录页时才放行无 marker 的续页，
        # 避免正文页被误判。
        self._toc_state = toc_state
        # 每页的 (标题段, 布局段) 配对，供 normalize_boxes 统一 y 基准
        self._pairs: list[
            tuple[il_version_1.PdfParagraph, il_version_1.PdfParagraph]
        ] = []

    def _count_dotted_lines(self, page: il_version_1.Page) -> int:
        """统计页面上包含点引导线（3+ 连续点）的行数。"""
        n = 0
        for para in page.pdf_paragraph or []:
            chars = self._para_chars(para)
            if not chars:
                continue
            text = "".join(c.char_unicode for c in chars)
            if _LEADER_RUN.search(text):
                n += 1
        return n

    # ------------------------------------------------------------------
    # 字符/文本工具
    # ------------------------------------------------------------------
    @staticmethod
    def _para_chars(para: il_version_1.PdfParagraph) -> list[il_version_1.PdfCharacter]:
        chars: list[il_version_1.PdfCharacter] = []
        for comp in para.pdf_paragraph_composition or []:
            if comp is None:
                continue
            if comp.pdf_line:
                chars.extend(comp.pdf_line.pdf_character or [])
            elif comp.pdf_character:
                chars.append(comp.pdf_character)
            elif comp.pdf_same_style_characters:
                chars.extend(comp.pdf_same_style_characters.pdf_character or [])
        return [c for c in chars if c.char_unicode]

    @classmethod
    def _para_chars_text(
        cls, para: il_version_1.PdfParagraph
    ) -> tuple[list[il_version_1.PdfCharacter], str]:
        chars = cls._para_chars(para)
        text = "".join(c.char_unicode for c in chars)
        return chars, text

    @staticmethod
    def _char_box(char: il_version_1.PdfCharacter) -> il_version_1.Box:
        if char.visual_bbox and char.visual_bbox.box:
            return char.visual_bbox.box
        return char.box

    @classmethod
    def _compute_box(
        cls, chars: list[il_version_1.PdfCharacter]
    ) -> il_version_1.Box:
        boxes = []
        for c in chars:
            b = cls._char_box(c)
            if b is not None:
                boxes.append(b)
        if not boxes:
            return il_version_1.Box(0, 0, 0, 0)
        return il_version_1.Box(
            min(b.x for b in boxes),
            min(b.y for b in boxes),
            max(b.x2 for b in boxes),
            max(b.y2 for b in boxes),
        )

    # ------------------------------------------------------------------
    # 目录页检测
    # ------------------------------------------------------------------
    def is_toc_page(
        self,
        page: il_version_1.Page,
        prev_in_toc: bool = False,
        dotted_lines: int | None = None,
    ) -> bool:
        """判断是否为目录页（或目录续页）。

        - 首目录页：页面含 "Contents / List of Tables / List of Figures" marker
          且点线行数 >= _TOC_MIN_DOTTED_LINES。
        - 目录续页：多页目录中不带 marker 的续页（如 NsightCompute 第3页、
          NsightSystems 第3-7页），仅当上一页已在目录模式（prev_in_toc）且本页
          点线行数 >= _TOC_MIN_DOTTED_LINES 时判定为目录页。
        - 点线行数 < _TOC_MIN_DOTTED_LINES 的页不可能是目录页（含目录尾页之后
          的正文过渡页）。
        """
        if not page.pdf_paragraph:
            return False
        if dotted_lines is None:
            dotted_lines = self._count_dotted_lines(page)
        if dotted_lines < _TOC_MIN_DOTTED_LINES:
            return False
        text_parts: list[str] = []
        for para in page.pdf_paragraph:
            chars = self._para_chars(para)
            if not chars:
                continue
            text_parts.append("".join(c.char_unicode for c in chars))
        text = "\n".join(text_parts).replace("\x03", "").lower()
        has_marker = any(m.lower() in text for m in _TOC_MARKERS)
        return has_marker or prev_in_toc

    # ------------------------------------------------------------------
    # 页眉前缀
    # ------------------------------------------------------------------
    @staticmethod
    def _match_header_prefix(s: str) -> int | None:
        """若 s 以页眉前缀开头，返回其在 s 中的长度，否则 None。"""
        for pat in _HEADER_PATTERNS:
            m = pat.match(s)
            if m:
                return m.end()
        return None

    # ------------------------------------------------------------------
    # 条目拆分
    # ------------------------------------------------------------------
    def _build_paragraph(
        self,
        chars: list[il_version_1.PdfCharacter],
        base: il_version_1.PdfParagraph,
        toc_role: str | None,
    ) -> il_version_1.PdfParagraph:
        """用给定字符子集构造新段落（单行）。"""
        line = il_version_1.PdfLine(pdf_character=chars)
        line.box = self._compute_box(chars)
        para = il_version_1.PdfParagraph(
            box=self._compute_box(chars),
            pdf_style=base.pdf_style,
            pdf_paragraph_composition=[
                il_version_1.PdfParagraphComposition(pdf_line=line)
            ],
            xobj_id=base.xobj_id,
            unicode="".join(c.char_unicode for c in chars),
            vertical=base.vertical,
            first_line_indent=False,
            debug_id=f"{base.debug_id or 'p'}_{'toc_title' if toc_role == 'title' else 'toc_layout'}",
            layout_label=base.layout_label,
            layout_id=base.layout_id,
            toc_role=toc_role,
        )
        return para

    def _iter_entry_spans(self, text: str):
        """从左到右扫描文本，产出每个目录条目的
        (title_s, title_e, leader_s, leader_e, page_e) 绝对下标。

        处理 layout 模型把多个相邻条目合并进同一段落的情况（标题内嵌
        "点线+页码+下一条目"，如 "Figure 28 ... 52 Figure 29 ... 7"）。
        """
        pos = 0
        n = len(text)
        while pos < n:
            m = _ENTRY_RE.match(text[pos:])
            if not m:
                break
            title_s = pos + m.start("title")
            title_e = pos + m.end("title")
            leader_s = pos + m.start("leader")
            leader_e = pos + m.end("leader")
            page_e = pos + m.end("page")
            # 标题内嵌另一条目的点线（两条目合并成一段）
            inner = _LEADER_RUN.search(text[title_s:title_e])
            if inner:
                inner_s = title_s + inner.start()
                inner_e = title_s + inner.end()
                m2 = re.match(r"\s*(\d+)", text[inner_e:])
                if m2:
                    first_page_e = inner_e + m2.end()
                    yield (title_s, inner_s, inner_s, inner_e, first_page_e)
                    pos = first_page_e
                    continue
            yield (title_s, title_e, leader_s, leader_e, page_e)
            pos = page_e

    def _split_entry(
        self,
        chars: list[il_version_1.PdfCharacter],
        text: str,
        title_s: int,
        title_e: int,
        leader_s: int,
        leader_e: int,
        page_e: int,
        base: il_version_1.PdfParagraph,
    ) -> tuple[
        list[il_version_1.PdfCharacter],
        list[il_version_1.PdfCharacter],
        list[il_version_1.PdfCharacter],
    ]:
        """拆分一个目录条目（绝对文本下标）-> (标题字符, 布局字符, 页眉字符)。

        - 页眉前缀（"Contents (cont'd)" 等）剥离为独立普通段；
        - 行内裸整数编号（"1 Scope ...." 中的 "1"）剥离到布局段；
        - "Table N -"/"Figure N -"/"6.2.1.1" 等前缀留在标题段（LLM 可靠保留/本地化）。
        """
        # 1) 页眉前缀剥离
        header_end = title_s
        title_part = text[title_s:title_e]
        hdr_len = self._match_header_prefix(title_part)
        if hdr_len:
            header_end = title_s + hdr_len

        # 2) 行内数字编号剥离到布局段
        #    包括纯整数（1、2）和带点编号（6.5、6.7.1.1、13.5.4）
        #    这样标题段只保留纯标题文字，box 从标题列（如 x=112）开始，
        #    typesetting 排版时所有标题文字从统一位置起始，与原文对齐。
        #    Table/Figure 编号不剥离（LLM 需翻译 Table->表、Figure->图）。
        inline_num_end = header_end
        rest = text[header_end:title_e]
        rest_clean = rest.lstrip()
        rest_lead = len(rest) - len(rest_clean)
        if not _INLINE_WORD_NUM_RE.match(rest_clean):
            m3 = _INLINE_NUM_RE.match(rest_clean)
            if m3:  # 剥离所有数字编号（含带点编号如 6.5、6.7.1.1）
                # 用 start("title") 跳过编号和中间空格，标题段从标题文字开始
                inline_num_end = header_end + rest_lead + m3.start("title")

        title_chars = chars[inline_num_end:title_e]
        layout_chars = list(chars[leader_s:page_e])
        if inline_num_end > header_end:
            layout_chars = list(chars[header_end:inline_num_end]) + layout_chars
        header_chars = list(chars[title_s:header_end]) if header_end > title_s else []
        return title_chars, layout_chars, header_chars

    def _is_continuation_candidate(
        self,
        prev: il_version_1.PdfParagraph,
        entry_title_x: float,
        entry_y: float,
    ) -> bool:
        """prev 是否为当前条目的“标题续接行”（多行标题的上一行）。"""
        if prev.box is None:
            return False
        prev_chars, prev_text = self._para_chars_text(prev)
        if not prev_chars:
            return False
        prev_stripped = prev_text.strip()
        if not prev_stripped:
            return False
        # 带点线/纯编号/页眉的段不是续接行
        if _LEADER_RUN.search(prev_text):
            return False
        if _NUMBER_RE.match(prev_stripped):
            return False
        if self._match_header_prefix(prev_stripped):
            return False
        # 同列起始（x 差 <= 2pt）
        prev_x = prev.box.x or 0
        if abs(prev_x - entry_title_x) > 2.0:
            return False
        # 垂直相邻：紧贴在上方（间隙 <= 1.5 倍行高）
        gap = entry_y - (prev.box.y2 or 0)
        if gap < -1.0 or gap > max((prev.box.y2 or 0) - (prev.box.y or 0), 8.0) * 1.5:
            return False
        return True

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def process(self, page: il_version_1.Page) -> bool:
        """重组目录页段落。返回 True 表示该页被当作目录页处理。

        跨页状态维护：本页点线行数 < _TOC_MIN_DOTTED_LINES 时退出目录模式
        （目录结束、进入正文）；否则若命中 marker 或上一页已在目录模式则
        进入/延续目录模式。
        """
        if not getattr(self.translation_config, "fix_toc", True):
            return False

        dotted_lines = self._count_dotted_lines(page)
        if self._toc_state is not None:
            prev_in_toc = bool(self._toc_state.get("in_toc", False))
            if dotted_lines < _TOC_MIN_DOTTED_LINES:
                # 目录结束（进入正文过渡页），退出目录模式
                if prev_in_toc:
                    self._toc_state["in_toc"] = False
                return False
            if not self.is_toc_page(page, prev_in_toc, dotted_lines):
                return False
            self._toc_state["in_toc"] = True
        elif not self.is_toc_page(page):
            return False

        self._pairs = []
        paras = sorted(
            [p for p in page.pdf_paragraph],
            key=lambda p: (
                round((p.box.y if p.box else 0), 1),
                (p.box.x if p.box else 0),
            ),
        )
        result: list[il_version_1.PdfParagraph] = []

        for para in paras:
            chars, text = self._para_chars_text(para)
            if not chars:
                result.append(para)
                continue
            stripped = text.strip()
            if not stripped:
                result.append(para)
                continue
            lead = len(text) - len(text.lstrip())

            # 1) 纯编号段：不翻译（il_translator 的 is_pure_numeric 跳过），原样保留
            if _NUMBER_RE.match(stripped):
                result.append(para)
                continue

            # 2) 点线+页码独立段：布局段
            if _DOTS_PAGE_RE.match(stripped):
                para.toc_role = "layout"
                result.append(para)
                continue

            # 3) 完整条目：标题 + 点线 + 页码（layout 模型可能把多条相邻条目
            #    合并进同一段落，逐条拆分）
            spans = list(self._iter_entry_spans(text))
            if spans:
                for idx, (title_s, title_e, leader_s, leader_e, page_e) in enumerate(spans):
                    title_chars, layout_chars, header_chars = self._split_entry(
                        chars, text, title_s, title_e, leader_s, leader_e, page_e, para
                    )
                    # 多行标题续接：把上方同列的续接行并入标题（仅第一条目）
                    if idx == 0:
                        while title_chars and result:
                            prev = result[-1]
                            first = title_chars[0]
                            entry_title_x = self._char_box(first).x
                            entry_y = self._char_box(first).y
                            if not self._is_continuation_candidate(
                                prev, entry_title_x, entry_y
                            ):
                                break
                            prev_chars, _ = self._para_chars_text(prev)
                            result.pop()
                            title_chars = prev_chars + title_chars

                    if header_chars:
                        result.append(self._build_paragraph(header_chars, para, None))

                    if title_chars and layout_chars:
                        title_para = self._build_paragraph(title_chars, para, "title")
                        layout_para = self._build_paragraph(
                            layout_chars, para, "layout"
                        )
                        result.append(title_para)
                        result.append(layout_para)
                        self._pairs.append((title_para, layout_para))
                    elif title_chars:
                        # 只有标题（理论少见）
                        result.append(self._build_paragraph(title_chars, para, "title"))
                    elif layout_chars:
                        result.append(
                            self._build_paragraph(layout_chars, para, "layout")
                        )
                continue

            # 4) 标题 + 尾部孤立点线（页码/点线在下一段）：剥掉尾部点
            stripped_dots = stripped.rstrip(_LEADER_CHARS).rstrip()
            if stripped_dots and stripped_dots != stripped:
                if _NUMBER_RE.match(stripped_dots):
                    result.append(para)
                    continue
                title_chars = chars[lead : lead + len(stripped_dots)]
                if title_chars:
                    result.append(self._build_paragraph(title_chars, para, "title"))
                continue

            # 5) 其他（页眉、列标题 "Page" 等）：正常翻译
            result.append(para)

        page.pdf_paragraph.clear()
        page.pdf_paragraph.extend(result)
        return True

    # ------------------------------------------------------------------
    # 归一化
    # ------------------------------------------------------------------
    def normalize_boxes(self):
        """统一同一目录行标题段/布局段的 y 基准，并扩展标题段宽度。

        - y 基准：多行标题压回单行（锚定首行）；单行标题保持原文 y 坐标不变
          （标题段和布局段在原文中本就同行，y 相同，无需强制覆盖；
          强制覆盖反而会导致不同条目的标题被压到同一 y，造成重叠）。
        - x 宽度：标题段 box.x2 扩展到布局段（点线/页码）左边界前 2pt，
          给 typesetting 足够宽度排版标题文字，避免缩字号或折行。
        """
        for title_para, layout_para in self._pairs:
            if not title_para.box or not layout_para.box:
                continue
            title_chars = self._para_chars(title_para)
            if not title_chars:
                continue
            line_h = layout_para.box.y2 - layout_para.box.y
            if line_h <= 0:
                line_h = 13.0
            ys = [self._char_box(c).y for c in title_chars]
            y2s = [self._char_box(c).y2 for c in title_chars]
            min_y, max_y2 = min(ys), max(y2s)
            if (max_y2 - min_y) > line_h * 1.5:
                # 多行标题（目录标题折行后续接合并）：锚定最上方行
                top_line_bottom = max(ys)
                title_para.box.y = top_line_bottom
                title_para.box.y2 = top_line_bottom + line_h
            # 单行标题：保持原文 y 坐标不变，不强制对齐布局段
            # （标题段和布局段在原文中本就同行，y 坐标相同，
            #   强制覆盖 layout_para.box.y 可能导致不同条目标题重叠）
            # 扩展标题段 x2 到布局段（点线/页码）左边界前 2pt
            # 避免标题文字被缩字号（如 13.5.4 MBIST 被缩到 sz=4）
            if layout_para.box.x > title_para.box.x:
                title_para.box.x2 = max(title_para.box.x2, layout_para.box.x - 2)
