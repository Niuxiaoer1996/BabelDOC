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


class TOCProcessor:
    """目录页结构化处理器。"""

    def __init__(self, translation_config):
        self.translation_config = translation_config
        # 每页的 (标题段, 布局段) 配对，供 normalize_boxes 统一 y 基准
        self._pairs: list[
            tuple[il_version_1.PdfParagraph, il_version_1.PdfParagraph]
        ] = []

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
    def is_toc_page(self, page: il_version_1.Page) -> bool:
        if not page.pdf_paragraph:
            return False
        text_parts: list[str] = []
        dotted_lines = 0
        for para in page.pdf_paragraph:
            chars = self._para_chars(para)
            if not chars:
                continue
            text = "".join(c.char_unicode for c in chars)
            text_parts.append(text)
            if _LEADER_RUN.search(text):
                dotted_lines += 1
        text = "\n".join(text_parts).replace("\x03", "").lower()
        has_marker = any(m.lower() in text for m in _TOC_MARKERS)
        return has_marker and dotted_lines >= 3

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

    def _split_entry(
        self,
        chars: list[il_version_1.PdfCharacter],
        text: str,
        lead: int,
        m: re.Match,
        base: il_version_1.PdfParagraph,
    ) -> tuple[
        list[il_version_1.PdfCharacter],
        list[il_version_1.PdfCharacter],
        list[il_version_1.PdfCharacter],
    ]:
        """拆分完整条目 -> (标题字符, 布局字符, 页眉字符)。

        坐标约定：text 为原始字符串（含首尾空白），m 匹配自 strip 后的文本，
        因此 m 中的下标 + lead 才是 text 的下标。
        """
        title_s = m.start("title") + lead
        title_e = m.end("title") + lead
        leader_s = m.start("leader") + lead
        page_e = m.end("page") + lead

        # 1) 页眉前缀剥离（如 "Contents (cont'd) Table 46 – ..."）
        header_end = title_s
        title_part = text[title_s:title_e]
        hdr_len = self._match_header_prefix(title_part)
        if hdr_len:
            header_end = title_s + hdr_len

        # 2) 行内裸整数编号剥离到布局段（"1 Scope ...." -> 布局段收走 "1"）
        #    "Table N -"/"Figure N -"/"6.2.1.1" 等前缀留在标题段（LLM 可靠保留/本地化）
        inline_num_end = header_end
        rest = text[header_end:title_e]
        rest_clean = rest.lstrip()
        rest_lead = len(rest) - len(rest_clean)
        if not _INLINE_WORD_NUM_RE.match(rest_clean):
            m3 = _INLINE_NUM_RE.match(rest_clean)
            if m3 and re.fullmatch(r"\d+", m3.group("num")):
                inline_num_end = header_end + rest_lead + m3.end("num")

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
        """重组目录页段落。返回 True 表示该页被当作目录页处理。"""
        if not getattr(self.translation_config, "fix_toc", True):
            return False
        if not self.is_toc_page(page):
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

            # 3) 完整条目：标题 + 点线 + 页码
            m = _ENTRY_RE.match(stripped)
            if m:
                title_chars, layout_chars, header_chars = self._split_entry(
                    chars, text, lead, m, para
                )
                # 多行标题续接：把上方同列的续接行并入标题
                while title_chars and result:
                    prev = result[-1]
                    first = title_chars[0]
                    entry_title_x = self._char_box(first).x
                    entry_y = self._char_box(first).y
                    if not self._is_continuation_candidate(prev, entry_title_x, entry_y):
                        break
                    prev_chars, _ = self._para_chars_text(prev)
                    result.pop()
                    title_chars = prev_chars + title_chars

                if header_chars:
                    result.append(self._build_paragraph(header_chars, para, None))

                if title_chars and layout_chars:
                    title_para = self._build_paragraph(title_chars, para, "title")
                    layout_para = self._build_paragraph(layout_chars, para, "layout")
                    result.append(title_para)
                    result.append(layout_para)
                    self._pairs.append((title_para, layout_para))
                elif title_chars:
                    # 只有标题（理论少见）
                    result.append(self._build_paragraph(title_chars, para, "title"))
                elif layout_chars:
                    para.toc_role = "layout"
                    result.append(para)
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
        """统一同一目录行标题段/布局段的 y 基准。

        多行标题（标题折行）的标题段字符横跨两行，update_paragraph_data 会把
        box 撑成两行高，导致排版从最后一行起排；这里把标题段压回单行：
        y = 首字符顶边，y2 = 首字符顶边 + 布局段行高。
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
                # （IL 为底上坐标，y 越大越靠上；标题首行在点线行上方）
                top_line_bottom = max(ys)
                title_para.box.y = top_line_bottom
                title_para.box.y2 = top_line_bottom + line_h
            else:
                # 单行标题：与同行的布局段（点线/页码）共用同一 y 基准，
                # 使译文标题基线与 passthrough 的点线/页码严格对齐
                title_para.box.y = layout_para.box.y
                title_para.box.y2 = layout_para.box.y2
