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
# 宽松点线：2+ 连续点。layout 模型可能把合并块最后一个条目的点线截短成 2 个点
# （如 "Figure 167: ... Push-Pull .."），故条目边界用 2 点即可识别。
_LEADER_RUN_LOOSE = re.compile(r"[" + _LEADER_CHARS + r"]{2,}")
# 页码紧贴章节号的边界（布局模型缺失点线时，如 "...Word3187.29.3 ..."）。
# 匹配 "页码(318) + 紧贴章节号(7.29.3)"：318 是上一条目页码，7.29.3 是新条目。
# 页码要求至少 2 位且前面非点号/数字，避免误伤章节号自身（如 "7.29.1" 的 7）。
_ATTACHED_SEC_RE = re.compile(r"(?<![.\d])\d{2,}(?=\d+\.\d+(?:\.\d+)*\s)")

# 完整目录条目：标题 + 3+ 点引导线 + 页码（匹配前先 strip，坐标用 lead 偏移换算）
_ENTRY_RE = re.compile(
    r"^(?P<title>.*?)"
    r"(?P<leader>[" + _LEADER_CHARS + r"]{3,})\s*"
    r"(?P<page>\d+)\s*$"
)
# 行内数字编号前缀："6.2.1.1 Internal DBIac..."（编号后跟空格）
_INLINE_NUM_RE = re.compile(r"^(?P<num>[\d.]+)\s+(?P<title>.+)$")
# 段首裸整数紧贴标题（layout 模型把上一行跨行条目的页码残片并进本段段首且无空格，
# 如 "313Table 257:...cients....314" 中的 "313"）。剥离整数，标题从 Table 开始。
_INLINE_ATTACHED_NUM_RE = re.compile(r"^\d+(?=[^\d.\s])")
# 行内文字编号前缀："Table 1 – ..." / "Figure 46 – ..."
_INLINE_WORD_NUM_RE = re.compile(
    r"^(?P<num>(?:Table|FIGURE|TABLE|Figure)\s*\d+)\s*[-\u2013\u2014:.\t]\s*(?P<title>.+)$"
)
# 点线+页码 独立段：".......3"
_DOTS_PAGE_RE = re.compile(r"^[" + _LEADER_CHARS + r"]{3,}\s*(\d+)\s*$")
# 纯编号行："1" / "3.1" / "6.2.1.1"
_NUMBER_RE = re.compile(r"^[\d.]+\s*$")
# 目录条目起点（编号前缀）："Figure 74" / "Table 332"（标题开头）
_ENTRY_TOKEN_RE = re.compile(r"(?:Figure|TABLE|Table|FIGURE)\s*\d+")

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
        """统计页面上的点引导线（3+ 连续点）run 数量。

        按 run 计数而非段落计数：layout 模型可能把多个目录条目合并进同一段落
        （每段含多个点线 run，如物理28 的合并块），按段落计数会低估点线行数，
        导致目录尾页（条目少但每块含多条）被 _TOC_MIN_DOTTED_LINES 误判为非目录页。
        """
        n = 0
        for para in page.pdf_paragraph or []:
            chars = self._para_chars(para)
            if not chars:
                continue
            text = "".join(c.char_unicode for c in chars)
            n += len(_LEADER_RUN.findall(text))
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
        - 目录续页（prev_in_toc）：目录尾页可能只剩最后一条点线条目（如
          NB25036 表目录尾页单条 "Table N ... 页码"），点线行数 < 阈值但仍
          需处理。此时只要仍有 >= 1 条点线（dotted_lines == 0 才真正结束）
          即视为目录续页，否则最后一条条目的点线/页码不会被拆分保留。
        """
        if not page.pdf_paragraph:
            return False
        if dotted_lines is None:
            dotted_lines = self._count_dotted_lines(page)
        min_dots = _TOC_MIN_DOTTED_LINES if not prev_in_toc else 1
        if dotted_lines < min_dots:
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
    def _filter_title_outliers(
        self, chars: list[il_version_1.PdfCharacter]
    ) -> list[il_version_1.PdfCharacter]:
        """剔除标题段中混入的离群残留字符。

        layout 模型把多个目录条目合并进同一段落时，会把相邻条目的极少量字符
        （下标/装饰/上一行残留）混入当前标题字符集。这些字符几何 y 与标题主体
        相差超过一行，会撑大 title 段 box，导致 normalize_boxes 误判为"多行标题"
        并锚定到错误 y，造成标题重叠、后续错位。
        本方法按 y 聚类成行簇，只剔除"字符数远少于主簇且跨行孤立"的残留簇，
        保留字符量可观的行（含正常多行标题的每一行）。
        """
        if not chars:
            return chars
        from collections import Counter

        boxes = [self._char_box(c) for c in chars]
        # 行高 = 字符高度众数
        hs = Counter(
            round(b.y2 - b.y, 1)
            for b in boxes
            if b is not None and b.y is not None and b.y2 is not None
        )
        line_h = max(hs.items(), key=lambda kv: kv[1])[0] if hs else 10.0
        if line_h <= 0:
            line_h = 10.0
        # 按 y 聚类成行簇（相邻 y 差 < 半行高视为同一行）
        items = sorted(
            [(b.y, c) for c, b in zip(chars, boxes) if b is not None and b.y is not None],
            key=lambda t: t[0],
        )
        if len(items) < 2:
            return chars
        clusters: list[list[tuple[float, il_version_1.PdfCharacter]]] = []
        cur = [items[0]]
        for item in items[1:]:
            if item[0] - cur[-1][0] < line_h * 0.5:
                cur.append(item)
            else:
                clusters.append(cur)
                cur = [item]
        clusters.append(cur)
        # 主簇 = 字符数最多的行簇
        main = max(clusters, key=len)
        main_y = sum(i[0] for i in main) / len(main)
        keep_ids: set[int] = set()
        for cl in clusters:
            cl_y = sum(i[0] for i in cl) / len(cl)
            # 只剔除：字符数极少（<5 个，多为下标/装饰/残留）且 y 距主簇跨行
            # （正常多行标题的每一行字符数通常 >=5，不会被误删）
            if len(cl) < 5 and abs(cl_y - main_y) > line_h:
                continue
            keep_ids.update(id(c) for _, c in cl)
        return [c for c in chars if id(c) in keep_ids]

    def _build_paragraph(
        self,
        chars: list[il_version_1.PdfCharacter],
        base: il_version_1.PdfParagraph,
        toc_role: str | None,
    ) -> il_version_1.PdfParagraph:
        """用给定字符子集构造新段落（单行）。

        标题段会剔除几何离群的残留字符（见 _filter_title_outliers），
        避免混入相邻条目字符导致 box 撑大、标题重叠错位。
        """
        if toc_role == "title":
            chars = self._filter_title_outliers(chars)
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

    def _iter_entry_spans(self, text: str, chars: list | None = None):
        """从左到右扫描文本，产出每个目录条目的
        (title_s, title_e, leader_s, leader_e, page_s, page_e) 绝对下标
        （page_s/page_e 为 None 表示无页码）。

        layout 模型可能把多个相邻目录条目合并进同一段落，有两种结构：
        - 常规（页码在点线后）：[title][dots][page][next title]...
          涵盖章节目录（"3.25.1 Power up .... 92 3.25.2 Mux Mode .... 93"）、
          JESD 图目录（"Figure 28 ... 52 Figure 29 ... 7"）等。
        - 页码前移（NB25036 图目录块首，布局模型把页码剥离并移到标题前）：
          [page][title][dots][page][title][dots]...
        两者在"点线+数字+下一条目"层面无法区分，故以"块首是否有前移页码"
        判别：块首有页码 => 前移结构，按 Figure/Table 边界切分；
        否则 => 常规结构，按点线驱动切分。
        """
        tokens = [m.start() for m in _ENTRY_TOKEN_RE.finditer(text)]
        if tokens:
            first = tokens[0]
            leading_page = re.search(r"(\d+)\s*$", text[:first])
            if leading_page is not None:
                # 前移结构：页码位于标题之前，[page][Figure N: title][dots]...
                # 但 layout 模型可能把上一行条目（跨行）的页码残片并进本段段首
                # （如 "313Table 257:...cients....314"，313 是上一行 Table 256
                #  的页码，y 坐标不同行）。此时不是真正的前移结构，必须按点线
                # 结构解析，否则点线后的真实页码（314）会丢失。用字符 y 坐标
                # 验证段首数字与标题是否同一行。
                if self._leading_page_same_line(chars, text, leading_page, first):
                    # 前移结构：页码在标题前，按 Figure/Table 边界拆分
                    yield from self._iter_entry_spans_shifted(text, tokens)
                    return
        # 点线结构：章节目录 / JESD / 表目录（页码在点线后）
        yield from self._iter_entry_spans_dotted(text)

    def _leading_page_same_line(
        self,
        chars: list,
        text: str,
        leading_page: re.Match,
        title_start: int,
    ) -> bool:
        """判断段首数字（leading_page）与标题是否同一行。

        chars 与 text 一一对应（chars 拼接成 text）。若段首数字与标题首字符
        的 y 坐标差异超过半行高，说明该数字是上一行的页码残片（不同行），
        不是真正的前移结构页码。chars 为空时退化返回 True（沿用原逻辑）。
        """
        if not chars or title_start >= len(chars):
            return True
        lp_start = leading_page.start(1)
        lp_end = leading_page.end()
        if lp_end > len(chars) or lp_start >= len(chars):
            return True
        page_y = self._char_box(chars[lp_start]).y
        title_y = self._char_box(chars[title_start]).y
        title_box = self._char_box(chars[title_start])
        line_h = (title_box.y2 or title_y) - title_y
        if line_h <= 0:
            line_h = 10.0
        return abs(page_y - title_y) <= line_h * 0.5

    def _iter_entry_spans_dotted(self, text: str):
        """点线驱动切分（常规结构，页码在点线后）。

        每个目录条目 = [标题][3+点线][页码]；点线后紧跟页码，页码后是下一条目。
        适用于章节目录（无 Figure/Table 前缀）、JESD 图目录、以及单条目。
        """
        n = len(text)
        pos = 0
        while pos < n:
            lm = _LEADER_RUN_LOOSE.search(text, pos)
            title_end = lm.start() if lm else n
            # 形态C：点线缺失时，标题内混入"页码紧贴章节号"边界（如
            # "Word3187.29.3 ..."：318 是上一条目页码，7.29.3 是新章节条目）。
            # 优先按该边界切分，避免上一条目吞掉下一条目、页码错配。
            if lm is not None:
                am = _ATTACHED_SEC_RE.search(text, pos, title_end)
            else:
                am = _ATTACHED_SEC_RE.search(text, pos)
            if am is not None:
                page_s = am.start()
                page_e = am.end()
                # 当前 title 到页码前；页码归入当前条目；章节号开始新条目
                yield (pos, page_s, page_s, page_s, page_s, page_e)
                pos = am.end()
                continue
            if not lm:
                break
            title_s, title_e = pos, lm.start()
            leader_s, leader_e = lm.start(), lm.end()
            m2 = re.match(r"\s*(\d+)", text[leader_e:])
            if m2:
                page_s = leader_e + m2.start(1)
                page_e = leader_e + m2.end()
                pos = page_e
            else:
                # 点线后无页码（标题内含点线或末尾残缺）：仅到点线为止
                page_s = page_e = None
                pos = leader_e
            yield (title_s, title_e, leader_s, leader_e, page_s, page_e)
        # 兜底：点线驱动结束后，若已切出过条目（pos>0，说明是合并块）且剩余文本
        # 非空，剩余内容应为未处理条目（其点线被布局模型损坏/截短导致上述循环
        # 未覆盖），补充切出，避免长标题/末尾条目的标题整行丢失。
        if pos > 0 and pos < n:
            rest_tokens = [m.start() for m in _ENTRY_TOKEN_RE.finditer(text, pos)]
            if rest_tokens:
                # 图/表目录：按 Figure/Table token 切分剩余条目
                for k, s in enumerate(rest_tokens):
                    next_s = rest_tokens[k + 1] if k + 1 < len(rest_tokens) else n
                    yield (s, next_s, next_s, next_s, None, None)
            else:
                # 章节目录（无 Figure/Table 前缀）：剩余文本整体作为一个 title，
                # 剥离尾部点线/页码残留
                rest = text[pos:].strip()
                if rest:
                    end = n
                    m = re.search(r"[\s" + re.escape(_LEADER_CHARS) + r"\d]+$", rest)
                    if m:
                        end = pos + m.start()
                    yield (pos, end, end, end, None, None)

    def _iter_entry_spans_shifted(self, text: str, tokens: list[int]):
        """前移结构切分（页码在标题前，NB25036 图目录块首）。

        结构：[page][Figure N: title][dots][page][Figure N+1: title][dots]...
        每条目标题从 Figure/Table N 起，页码 = 其 Figure/Table 前的数字。
        """
        n = len(text)
        for i, s in enumerate(tokens):
            next_s = tokens[i + 1] if i + 1 < len(tokens) else n
            seg = text[s:next_s]
            lm = _LEADER_RUN_LOOSE.search(seg)
            if lm:
                title_e = s + lm.start()
                leader_s = s + lm.start()
                leader_e = s + lm.end()
            else:
                # 无点线（末尾残缺条目或纯标题段）：标题到下一个 token / 末尾
                title_e = next_s
                leader_s = leader_e = next_s
            # 前移结构：本条目页码 = 其 Figure/Table 前的数字
            pm = re.search(r"(\d+)\s*$", text[:s])
            page_s = pm.start(1) if pm else None
            page_e = pm.end() if pm else None
            yield (s, title_e, leader_s, leader_e, page_s, page_e)

    def _split_entry(
        self,
        chars: list[il_version_1.PdfCharacter],
        text: str,
        title_s: int,
        title_e: int,
        leader_s: int,
        leader_e: int,
        page_s: int | None,
        page_e: int | None,
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
        - 布局段 = 点线 + 页码；页码可能位于标题之前（前移结构，page_s<page_e
          与点线不连续，故单独收集）。
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
            else:
                # 段首裸整数紧贴标题（上一行跨行条目的页码残片并进段首，无空格）
                m4 = _INLINE_ATTACHED_NUM_RE.match(rest_clean)
                if m4:
                    inline_num_end = header_end + rest_lead + m4.end()

        title_chars = chars[inline_num_end:title_e]
        # 布局段 = 点线 + 页码（页码在前移结构下位于标题之前，单独收集）
        layout_chars = list(chars[leader_s:leader_e])
        if page_s is not None and page_e is not None:
            layout_chars += list(chars[page_s:page_e])
        if inline_num_end > header_end:
            layout_chars = list(chars[header_end:inline_num_end]) + layout_chars
        header_chars = list(chars[title_s:header_end]) if header_end > title_s else []
        return title_chars, layout_chars, header_chars

    def _merge_left_title_fragment(
        self,
        prev: il_version_1.PdfParagraph,
        title_chars: list[il_version_1.PdfCharacter],
        entry_title_x: float,
        entry_y: float,
    ) -> tuple[list[il_version_1.PdfCharacter], bool]:
        """合并 result 中同一行的左邻标题残片到 title_chars 开头。

        layout 模型可能把长标题横向切成2段（如 "2 Mechanical Ou" + "tline (...)"、
        "Absolute Maxi" + "mum Ratings"），右侧段被识别为 title（有 layout 配对），
        左侧段 role=None 残片。本方法把左邻残片合并到 title 开头，恢复完整标题。
        返回 (合并后字符, 是否合并)。
        """
        if not prev or prev.box is None:
            return title_chars, False
        prev_chars, prev_text = self._para_chars_text(prev)
        if not prev_chars:
            return title_chars, False
        prev_stripped = prev_text.strip()
        if not prev_stripped:
            return title_chars, False
        # 残片不含点线/纯编号/页眉
        if _LEADER_RUN.search(prev_text):
            return title_chars, False
        if _NUMBER_RE.match(prev_stripped):
            return title_chars, False
        if self._match_header_prefix(prev_stripped):
            return title_chars, False
        # 同行（y 差 < 行高）
        prev_y = prev.box.y or 0
        line_h = (prev.box.y2 or prev_y) - prev_y
        if line_h <= 0:
            line_h = 10.0
        if abs(prev_y - entry_y) > line_h:
            return title_chars, False
        # x 相邻：前段 x2 与当前 title x 差 <= 2pt（无缝）
        prev_x2 = prev.box.x2 or 0
        if abs(prev_x2 - entry_title_x) > 2.0:
            return title_chars, False
        return prev_chars + title_chars, True

    def _collect_orphan_title_fragment(
        self,
        result: list[il_version_1.PdfParagraph],
        layout: il_version_1.PdfParagraph,
    ) -> list[il_version_1.PdfParagraph]:
        """扫描整个 result，收集与 layout 段同一行、x 相邻成链的标题残片段。

        layout 模型可能把章节目录条目拆成 [标题段1][标题段2][点线+页码段]
        （如 "Absolute Maxi" + "mum Ratings" + "......32"），标题与点线完全分离，
        无任何段落含完整"标题+点线+页码"，点线驱动无法识别。本方法以点线+页码
        layout 段为锚点，在整页 result 中寻找同行、从 layout 左缘向左 x 无缝相邻
        （gap<=2pt）成链的未配对残片段，合并为标题。返回从左到右的碎片列表。
        """
        if layout.box is None:
            return []
        lay_box = layout.box
        line_h = (lay_box.y2 or 0) - (lay_box.y or 0)
        if line_h <= 0:
            line_h = 10.0
        # 收集与 layout 同一行、未配对、无点线/编号/页眉的候选段
        candidates: list[il_version_1.PdfParagraph] = []
        for pp in result:
            if pp is layout:
                continue
            if pp.toc_role not in (None, "normal"):
                continue
            if pp.box is None:
                continue
            if abs((pp.box.y or 0) - (lay_box.y or 0)) > line_h:
                continue
            pp_chars, pp_text = self._para_chars_text(pp)
            if not pp_chars:
                continue
            pp_stripped = pp_text.strip()
            if not pp_stripped:
                continue
            if _LEADER_RUN.search(pp_text):
                continue
            if _NUMBER_RE.match(pp_stripped):
                continue
            if self._match_header_prefix(pp_stripped):
                continue
            candidates.append(pp)
        if not candidates:
            return []
        # 从 layout 左缘向左构建 x 相邻链（每次取最靠右的相邻段，逐步左移）
        frags: list[il_version_1.PdfParagraph] = []
        right_edge = lay_box.x or 0
        while True:
            best = None
            best_x2 = -1.0
            for pp in candidates:
                if pp in frags:
                    continue
                x2 = pp.box.x2 or 0
                if abs(x2 - right_edge) <= 2.0 and x2 > best_x2:
                    best = pp
                    best_x2 = x2
            if best is None:
                break
            frags.append(best)
            right_edge = best.box.x or 0
        frags.sort(key=lambda pp: pp.box.x or 0)
        return frags

    def _pair_orphan_layouts(self, result: list[il_version_1.PdfParagraph]) -> None:
        """post-pass：把"孤立点线+页码"布局段与同一行 x 相邻的标题残片段配对。

        主循环内处理时，标题残片段可能因 y 微差（如 0.2pt）排在点线段之后，
        循环内看不到；这里在整个 result 上统一扫描，为每个未进入 _pairs 的
        孤立 layout 段收集同行 x 相邻的残片段，合并为标题并配对。
        已有配对的 title/layout 段、普通正文段、页眉、纯编号段不受影响。
        """
        if not result:
            return
        paired_layout = {id(lp) for _, lp in self._pairs}
        orphans = [
            pp
            for pp in result
            if pp.toc_role == "layout" and id(pp) not in paired_layout
        ]
        if not orphans:
            return
        remove_ids: set[int] = set()
        new_pairs: list[tuple] = []
        for lay in orphans:
            frags = self._collect_orphan_title_fragment(result, lay)
            if not frags:
                continue
            title_chars: list[il_version_1.PdfCharacter] = []
            for frag in frags:
                fc, _ = self._para_chars_text(frag)
                title_chars.extend(fc)
                remove_ids.add(id(frag))
            title_chars = self._filter_title_outliers(title_chars)
            if not title_chars:
                continue
            title_para = self._build_paragraph(title_chars, lay, "title")
            new_pairs.append((title_para, lay))
        if not new_pairs:
            return
        orphan_lays = {id(lay) for _, lay in new_pairs}
        title_for = {id(lay): tp for tp, lay in new_pairs}
        rebuilt: list[il_version_1.PdfParagraph] = []
        for pp in result:
            if id(pp) in remove_ids:
                continue
            if id(pp) in orphan_lays:
                tp = title_for[id(pp)]
                rebuilt.append(tp)
                rebuilt.append(pp)
                self._pairs.append((tp, pp))
                continue
            rebuilt.append(pp)
        result[:] = rebuilt

    def _merge_adjacent_titles(self, result: list[il_version_1.PdfParagraph]) -> None:
        """合并同行、x 相邻（gap<=2pt）的两个已配对 title 段为一个条目。

        layout 模型可能把章节目录标题横切成两个独立布局块（如第8章
        "Absolute Maxi" + "mum Ratings"），TOCProcessor 把它们各自识别为
        title+layout 条目。本方法把同行、x 无缝相邻的两个 title 段及其
        layout 合并为单个条目，恢复完整标题。仅处理"同行且 x 相邻"的段，
        图目录/表目录无此类情况，不会误合并。
        """
        if len(result) < 2 or not self._pairs:
            return
        title_to_layout = {id(tp): lp for tp, lp in self._pairs}
        titles = [
            (pp, pp.box)
            for pp in result
            if pp.toc_role == "title" and id(pp) in title_to_layout and pp.box is not None
        ]
        if len(titles) < 2:
            return
        # 按 y 分簇（同行）：y 差 <= 行高视为同一行。
        # 不能按 (y,x) 全局排序后只查相邻——折行标题的两个碎片可能因 y 微差
        # （如 "mum Ratings" 含离群字符 y 比 "Absolute Maxi" 小 2.4pt）排序颠倒，
        # 导致 x 相邻判定失败。按 y 分簇后行内按 x 排序，保证 x 相邻配对正确。
        from collections import Counter
        hs = Counter(
            round((b.y2 or 0) - (b.y or 0), 1)
            for _, b in titles
            if b.y is not None and b.y2 is not None
        )
        line_h = max(hs.items(), key=lambda kv: kv[1])[0] if hs else 12.0
        if line_h <= 0:
            line_h = 12.0
        # y 分簇容差：用半个行高（而非整行高）。整行高会把相邻多行的 title 链式
        # 并进同一簇（如物理21 line_h=14.6 把 y 579~607 全部并簇），导致簇内按 x 排序后
        # 目标碎片（Absolute x=84 / mum x=159）被其他条目隔开，x 相邻判定失败。
        # 用半行高（~7pt）：mum(593.4) 与 Absolute(595.82) 差 2.4 仍同簇，
        # 但与相邻行（Electrical 579.4）差 14 被正确隔开。
        cluster_tol = max(line_h * 0.5, 3.0)
        titles.sort(key=lambda t: (t[1].y or 0))
        clusters: list[list] = []
        cur = [titles[0]]
        for i in range(1, len(titles)):
            if abs((titles[i][1].y or 0) - (titles[i - 1][1].y or 0)) <= cluster_tol:
                cur.append(titles[i])
            else:
                clusters.append(cur)
                cur = [titles[i]]
        clusters.append(cur)
        # 对每个同行簇，按 x 排序，找 x 相邻（gap<=2pt）的配对
        pairs: list[tuple] = []
        used: set[int] = set()
        for cluster in clusters:
            cluster.sort(key=lambda t: (t[1].x or 0))
            for i in range(len(cluster) - 1):
                t1, b1 = cluster[i]
                t2, b2 = cluster[i + 1]
                if id(t1) in used or id(t2) in used:
                    continue
                if abs((b2.x or 0) - (b1.x2 or 0)) > 2.0:
                    continue
                lp1 = title_to_layout.get(id(t1))
                lp2 = title_to_layout.get(id(t2))
                if lp1 is None or lp2 is None:
                    continue
                used.add(id(t1))
                used.add(id(t2))
                pairs.append((t1, t2, lp1, lp2))
        if not pairs:
            return
        # 构造合并后的 title + layout
        remove_ids = set()
        new_entries = {}
        for t1, t2, lp1, lp2 in pairs:
            remove_ids.update((id(t1), id(t2), id(lp1), id(lp2)))
            tchars: list[il_version_1.PdfCharacter] = []
            for t in sorted([t1, t2], key=lambda x: (x.box.x or 0)):
                tc, _ = self._para_chars_text(t)
                tchars.extend(tc)
            lchars: list[il_version_1.PdfCharacter] = []
            for l in sorted([lp1, lp2], key=lambda x: (x.box.x or 0)):
                lc, _ = self._para_chars_text(l)
                lchars.extend(lc)
            tchars = self._filter_title_outliers(tchars)
            new_title = self._build_paragraph(tchars, t1, "title")
            new_layout = self._build_paragraph(lchars, t1, "layout")
            new_entries[id(t1)] = (new_title, new_layout)
        # 重建 result：插入合并后的条目（优先），跳过被合并的段
        rebuilt: list[il_version_1.PdfParagraph] = []
        for pp in result:
            if id(pp) in new_entries:
                nt, nl = new_entries[id(pp)]
                rebuilt.append(nt)
                rebuilt.append(nl)
                continue
            if id(pp) in remove_ids:
                continue
            rebuilt.append(pp)
        # 更新 _pairs：移除被合并的，追加新的
        self._pairs = [
            (tp, lp)
            for tp, lp in self._pairs
            if id(tp) not in remove_ids and id(lp) not in remove_ids
        ]
        self._pairs.extend(new_entries.values())
        result[:] = rebuilt

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
            if dotted_lines == 0:
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

            # 1) 点线+页码独立段：布局段（比纯编号更具体，需先于 _NUMBER_RE 判断，
            #    否则 "......32" 会被 _NUMBER_RE 当成纯编号提前截走）。
            #    与标题残片段的配对在末尾 post-pass（_pair_orphan_layouts）统一处理，
            #    因为残片段可能因 y 微差排在点线段之后，循环内看不到。
            if _DOTS_PAGE_RE.match(stripped):
                para.toc_role = "layout"
                result.append(para)
                continue

            # 2) 纯编号段：不翻译（il_translator 的 is_pure_numeric 跳过），原样保留
            if _NUMBER_RE.match(stripped):
                result.append(para)
                continue

            # 3) 完整条目：标题 + 点线 + 页码（layout 模型可能把多条相邻条目
            #    合并进同一段落，逐条拆分）
            spans = list(self._iter_entry_spans(text, chars))
            if spans:
                for idx, (title_s, title_e, leader_s, leader_e, page_s, page_e) in enumerate(spans):
                    title_chars, layout_chars, header_chars = self._split_entry(
                        chars, text, title_s, title_e, leader_s, leader_e, page_s, page_e, para
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
                        # 横向分割标题：合并同一行的左邻残片（如 "2 Mechanical Ou" + "tline"）
                        if title_chars and result:
                            prev = result[-1]
                            first = title_chars[0]
                            entry_title_x = self._char_box(first).x
                            entry_y = self._char_box(first).y
                            merged, did_merge = self._merge_left_title_fragment(
                                prev, title_chars, entry_title_x, entry_y
                            )
                            if did_merge:
                                title_chars = merged
                                result.pop()

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

        # 末尾 post-pass：
        # 1) 孤立点线+页码布局段与同行 x 相邻的标题残片段配对
        #    （标题残片可能因 y 微差排在点线段之后，循环内看不到，需整体扫描）
        # 2) 同行、x 相邻的两个已配对 title 段合并为一个条目
        #    （布局模型把章节目录标题横切成两个独立块，如第8章 Absolute Maximum Ratings）
        self._pair_orphan_layouts(result)
        self._merge_adjacent_titles(result)

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
            # 修复 box.x：标题起始行（字符最多的 y 行簇）的 min x。
            # 折行标题的续接行（如 "Coefficients" 折行的 "cients" x=108）y 靠上、
            # x 靠左，会拉低 _compute_box 的 min x，使 box.x 定位到编号列（与章节号重合）。
            # 这里把 box.x 提升到标题起始行的 min x，**不改变 y**（避免标题偏移）。
            # 单行标题主簇即本身，box.x 不变；图/表目录 title（x=72）也不受影响。
            from collections import Counter
            yvals = [
                round(self._char_box(c).y, 1)
                for c in title_chars
                if self._char_box(c).y is not None
            ]
            if yvals:
                main_y = Counter(yvals).most_common(1)[0][0]
                # 聚类容差用字符行高（众数），不能用 layout 段高度（异常大，如 36.3），
                # 否则折行续接行也会被算进主簇，导致主簇 min x 被拉低、修复失效
                hs = Counter(
                    round(self._char_box(c).y2 - self._char_box(c).y, 1)
                    for c in title_chars
                    if self._char_box(c).y is not None
                    and self._char_box(c).y2 is not None
                )
                char_h = max(hs.items(), key=lambda kv: kv[1])[0] if hs else 12.0
                if char_h <= 0:
                    char_h = 12.0
                tol = char_h * 0.5
                main_xs = [
                    self._char_box(c).x
                    for c in title_chars
                    if self._char_box(c).y is not None
                    and abs(round(self._char_box(c).y, 1) - main_y) < tol
                    and self._char_box(c).x is not None
                ]
                if main_xs:
                    main_x = min(main_xs)
                    if title_para.box.x < main_x - 2:
                        title_para.box.x = main_x
            # 单行标题：保持原文 y 坐标不变，不强制对齐布局段
            # （标题段和布局段在原文中本就同行，y 坐标相同，
            #   强制覆盖 layout_para.box.y 可能导致不同条目标题重叠）
            # 扩展标题段 x2 到布局段（点线/页码）左边界前 2pt
            # 避免标题文字被缩字号（如 13.5.4 MBIST 被缩到 sz=4）
            if layout_para.box.x > title_para.box.x:
                title_para.box.x2 = max(title_para.box.x2, layout_para.box.x - 2)
