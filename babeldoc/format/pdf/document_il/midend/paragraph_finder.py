import logging
import random
import re

import numpy as np

from babeldoc.babeldoc_exception.BabelDOCException import ExtractTextError
from babeldoc.format.pdf.document_il import Box
from babeldoc.format.pdf.document_il import Document
from babeldoc.format.pdf.document_il import Page
from babeldoc.format.pdf.document_il import PdfCharacter
from babeldoc.format.pdf.document_il import PdfLine
from babeldoc.format.pdf.document_il import PdfParagraph
from babeldoc.format.pdf.document_il import PdfParagraphComposition
from babeldoc.format.pdf.document_il import PdfRectangle
from babeldoc.format.pdf.document_il.utils.fontmap import FontMapper
from babeldoc.format.pdf.document_il.utils.formular_helper import (
    collect_page_formula_font_ids,
)
from babeldoc.format.pdf.document_il.utils.layout_helper import (
    HEIGHT_NOT_USFUL_CHAR_IN_CHAR,
)
from babeldoc.format.pdf.document_il.utils.layout_helper import SPACE_REGEX
from babeldoc.format.pdf.document_il.utils.layout_helper import Layout
from babeldoc.format.pdf.document_il.utils.layout_helper import add_space_dummy_chars
from babeldoc.format.pdf.document_il.utils.layout_helper import build_layout_index
from babeldoc.format.pdf.document_il.utils.layout_helper import calculate_iou_for_boxes
from babeldoc.format.pdf.document_il.utils.layout_helper import get_char_unicode_string
from babeldoc.format.pdf.document_il.utils.layout_helper import get_character_layout
from babeldoc.format.pdf.document_il.utils.layout_helper import is_bullet_point
from babeldoc.format.pdf.document_il.utils.layout_helper import (
    is_character_in_formula_layout,
)
from babeldoc.format.pdf.document_il.utils.layout_helper import is_text_layout
from babeldoc.format.pdf.document_il.utils.paragraph_helper import is_cid_paragraph
from babeldoc.format.pdf.document_il.utils.style_helper import INDIGO
from babeldoc.format.pdf.document_il.utils.style_helper import WHITE
from babeldoc.format.pdf.translation_config import TranslationConfig
from babeldoc.format.pdf.document_il.midend.toc_processor import TOCProcessor

logger = logging.getLogger(__name__)

# Base58 alphabet (Bitcoin style, without numbers 0, O, I, l)
BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def generate_base58_id(length: int = 5) -> str:
    """Generate a random base58 ID of specified length."""
    return "".join(random.choice(BASE58_ALPHABET) for _ in range(length))


class ParagraphFinder:
    stage_name = "Parse Paragraphs"

    # 定义项目符号的正则表达式模式

    def __init__(self, translation_config: TranslationConfig):
        self.translation_config = translation_config
        self.font_mapper = FontMapper(translation_config)
        # 参考文献模式状态：跨页跟踪（上一页处于 REFERENCES 章节）
        self._in_references = False
        # 目录模式状态：跨页跟踪（多页目录中不带 marker 的续页，如 NsightCompute
        # 第3页、NsightSystems 第3-7页，需在上一页是目录页时延续处理）
        self._in_toc = False

    def _preprocess_formula_layouts(self, page: Page):
        """
        Identifies 'formula' layouts that do not significantly overlap with any text layouts
        and re-labels them as 'isolate_formula'.
        """
        # Use a simplified Layout object for is_text_layout check
        text_layouts = [
            layout
            for layout in page.page_layout
            if is_text_layout(Layout(layout.id, layout.class_name))
        ]
        formula_layouts = [
            layout for layout in page.page_layout if layout.class_name == "formula"
        ]

        if not text_layouts or not formula_layouts:
            return

        for formula_layout in formula_layouts:
            is_isolated = True
            for text_layout in text_layouts:
                iou = calculate_iou_for_boxes(formula_layout.box, text_layout.box)
                if iou >= 0.5:
                    is_isolated = False
                    break

            if is_isolated:
                formula_layout.class_name = "isolate_formula"

    def add_text_fill_background(self, page: Page):
        layout_map = {layout.id: layout for layout in page.page_layout}
        for paragraph in page.pdf_paragraph:
            layout_id = paragraph.layout_id
            if layout_id is None:
                continue
            layout = layout_map[layout_id]
            if paragraph.box is None:
                continue
            x1, y1, x2, y2 = (
                paragraph.box.x,
                paragraph.box.y,
                paragraph.box.x2,
                paragraph.box.y2,
            )
            layout_box = layout.box
            if layout_box.x < x1:
                x1 = layout_box.x
            if layout_box.y < y1:
                y1 = layout_box.y
            if layout_box.x2 > x2:
                x2 = layout_box.x2
            if layout_box.y2 > y2:
                y2 = layout_box.y2
            assert x2 > x1 and y2 > y1
            page.pdf_rectangle.append(
                PdfRectangle(
                    box=Box(x1, y1, x2, y2),
                    fill_background=True,
                    graphic_state=WHITE,
                    debug_info=False,
                    xobj_id=paragraph.xobj_id,
                )
            )

    def update_paragraph_data(self, paragraph: PdfParagraph, update_unicode=False):
        if not paragraph.pdf_paragraph_composition:
            return

        chars = []
        for composition in paragraph.pdf_paragraph_composition:
            if composition.pdf_line:
                chars.extend(composition.pdf_line.pdf_character)
            elif composition.pdf_formula:
                chars.extend(composition.pdf_formula.pdf_character)
            elif composition.pdf_character:
                chars.append(composition.pdf_character)
            elif composition.pdf_same_style_unicode_characters:
                continue
            else:
                logger.error(
                    "Unexpected composition type"
                    " in PdfParagraphComposition. "
                    "This type only appears in the IL "
                    "after the translation is completed.",
                )
                continue

        if update_unicode and chars:
            paragraph.unicode = get_char_unicode_string(chars)
        if not chars:
            return
        # 更新边界框
        min_x = min(char.visual_bbox.box.x for char in chars)
        min_y = min(char.visual_bbox.box.y for char in chars)
        max_x = max(char.visual_bbox.box.x2 for char in chars)
        max_y = max(char.visual_bbox.box.y2 for char in chars)
        paragraph.box = Box(min_x, min_y, max_x, max_y)
        paragraph.vertical = chars[0].vertical
        paragraph.xobj_id = chars[0].xobj_id

        paragraph.first_line_indent = False
        if (
            paragraph.pdf_paragraph_composition
            and paragraph.pdf_paragraph_composition[0].pdf_line
            and paragraph.pdf_paragraph_composition[0]
            .pdf_line.pdf_character[0]
            .visual_bbox.box.x
            - paragraph.box.x
            > 1
        ):
            paragraph.first_line_indent = True

    def update_line_data(self, line: PdfLine):
        min_x = min(char.visual_bbox.box.x for char in line.pdf_character)
        min_y = min(char.visual_bbox.box.y for char in line.pdf_character)
        max_x = max(char.visual_bbox.box.x2 for char in line.pdf_character)
        max_y = max(char.visual_bbox.box.y2 for char in line.pdf_character)
        line.box = Box(min_x, min_y, max_x, max_y)

    def add_debug_info(self, page: Page):
        if not self.translation_config.debug:
            return
        for paragraph in page.pdf_paragraph:
            for composition in paragraph.pdf_paragraph_composition:
                if composition.pdf_line:
                    line = composition.pdf_line
                    page.pdf_rectangle.append(
                        PdfRectangle(
                            box=line.box,
                            fill_background=False,
                            graphic_state=INDIGO,
                            debug_info=True,
                            line_width=0.2,
                        )
                    )

    def process(self, document):
        with self.translation_config.progress_monitor.stage_start(
            self.stage_name,
            len(document.page),
        ) as pbar:
            if not document.page:
                return
            for page in document.page:
                self.translation_config.raise_if_cancelled()
                self.process_page(page)
                pbar.advance()

            total_paragraph_count = 0
            for page in document.page:
                total_paragraph_count += len(page.pdf_paragraph)
            if total_paragraph_count == 0:
                raise ExtractTextError("The document contains no paragraphs.")

            if self.check_cid_paragraph(document):
                raise ExtractTextError("The document contains too many CID paragraphs.")

    def check_cid_paragraph(self, doc: Document):
        cid_para_count = 0
        para_total = 0
        for page in doc.page:
            para_total += len(page.pdf_paragraph)
            for para in page.pdf_paragraph:
                if is_cid_paragraph(para):
                    cid_para_count += 1
        return cid_para_count / para_total > 0.8

    def bbox_overlap(self, bbox1: Box, bbox2: Box) -> bool:
        return (
            bbox1.x < bbox2.x2
            and bbox1.x2 > bbox2.x
            and bbox1.y < bbox2.y2
            and bbox1.y2 > bbox2.y
        )

    def process_page(self, page: Page):
        # 源文档（多为 MDPI）用 U+25E6 '◦'（白色圆圈）表示度符号。
        # 归一化为标准度符号 U+00B0 '°'，避免占位符回填/未译直出时出现
        # "◦℃" 双度符号（LLM 常把 '◦C' 的 C 译成 ℃ 而保留 ◦）。
        for char in page.pdf_character:
            cu = char.char_unicode
            if cu and "\u25e6" in cu:
                char.char_unicode = cu.replace("\u25e6", "\u00b0")
        layout_index, layout_map = build_layout_index(page)
        # 预处理公式布局的标签
        self._preprocess_formula_layouts(page)

        # 第一步：根据 layout 创建 paragraphs
        # 在这一步中，page.pdf_character 中的字符会被移除
        paragraphs = self._group_characters_into_paragraphs(
            page, layout_index, layout_map
        )
        page.pdf_paragraph = paragraphs

        page_level_formula_font_ids, xobj_specific_formula_font_ids = (
            collect_page_formula_font_ids(
                page, self.translation_config.formular_font_pattern
            )
        )

        # for para in paragraphs:
        #     if not para.debug_id:
        #         continue
        #     new_line = PdfLine(
        #         pdf_character=[x.pdf_character for x in para.pdf_paragraph_composition]
        #     )
        #     self.update_line_data(new_line)
        #     para.pdf_paragraph_composition = [
        #         PdfParagraphComposition(pdf_line=new_line)
        #     ]

        # 第二步：将段落内的字符拆分为行
        for paragraph in paragraphs:
            if (
                paragraph.xobj_id
                and paragraph.xobj_id in xobj_specific_formula_font_ids
            ):
                current_formula_font_ids = xobj_specific_formula_font_ids[
                    paragraph.xobj_id
                ]
            else:
                current_formula_font_ids = page_level_formula_font_ids
            self._split_paragraph_into_lines(paragraph, current_formula_font_ids)

        # 第三步：处理段落中的空格
        for paragraph in paragraphs:
            add_space_dummy_chars(paragraph)
            self.process_paragraph_spacing(paragraph)
            self.update_paragraph_data(paragraph)

        # 第三步半：拆分包含多个 "NOTE N" 条目的段落
        # 图表 Note 部分的多个 NOTE 条目常被版面模型归为同一布局块，
        # 合并为一个段落。LLM 翻译整段后逐行格式丢失。按 "NOTE N" 行拆分。
        self.split_note_paragraphs(paragraphs)

        # 第三步 3/4：拆分"多个无序列表项被并成一行"的行
        # 行分组 threading 扫描对行距紧/字符 y 重叠的列表会把多个垂直
        # 排列的 bullet 项并成一行（"• A • B • C"），使行级列表项拆分失效。
        # 按 bullet 字符拆行后再走行级拆段。
        self._split_bullets_in_merged_lines(paragraphs)

        # 第四步：计算所有行宽度的中位数
        median_width = self.calculate_median_line_width(paragraphs)

        # 第五步：处理独立段落
        self.process_independent_paragraphs(paragraphs, median_width)

        # 目录页结构化处理：把目录条目拆成"标题段（可译）+ 布局段（passthrough）"
        # 必须在行号/句中合并之前执行；目录页跳过下述两个合并（避免编号被并进
        # 标题后误译、以及两行标题被重新并成带点线的整段）。
        # 传入跨页目录状态，使不带 marker 的目录续页也能被识别处理。
        _toc_state = {"in_toc": self._in_toc}
        toc_processor = TOCProcessor(self.translation_config, _toc_state)
        toc_page = toc_processor.process(page)
        self._in_toc = bool(_toc_state.get("in_toc", False))
        paragraphs = page.pdf_paragraph

        # 表格感知：标记落在 table 版面框内的段落，使"句中续接"合并跳过它们。
        # 表格单元格行（通常是独立的 fallback_line 段落）满足 merge 的"上段无句末
        # 标点 + 下段小写开头"条件，会被误判为"句中续接"并回一段，导致表格同列
        # 多行内容在译文流式重排时挤压到表格顶部。逐段落按中心点是否落在任一
        # "table" 版面框内判定。
        table_boxes = [
            layout.box
            for layout in page.page_layout
            if layout.class_name == "table" and layout.box is not None
        ]
        for paragraph in paragraphs:
            paragraph.in_table_layout = self._is_in_table_layout(
                paragraph, table_boxes
            )
        # 几何特征兜底：版面模型 table 框漏标时，用"同行多短段落"特征补充标记
        self._mark_table_row_paragraphs(paragraphs)

        # 参考文献（References/Bibliography）章节检测：
        # 检测到 "REFERENCES" 标题段后，其下方以 "[N]" 开头的段落标记为
        # skip_translate（翻译器跳过，保留原文 passthrough）。弱模型 LLM 常忽略
        # 提示词中"参考文献不翻译"的规则，引擎级检测才能可靠保证。
        self._mark_reference_paragraphs(paragraphs)

        # 新增后处理：合并带行号交替的正文段落（a 正文、b 行号、c 正文 -> 合并 a 与 c，保留 b）
        if (
            not toc_page
            and getattr(self.translation_config, "merge_alternating_line_numbers", True)
        ):
            self.merge_alternating_line_number_paragraphs(paragraphs)

        # 新增后处理：合并 box 嵌套在父段内的碎片段落（布局模型偶发嵌套输出，
        # 碎片独立翻译后与父段译文叠印，父段文本缺失且顺序错乱）。必须在
        # 水平合并之前执行（先恢复完整父段，再处理水平相邻片段）。
        if not toc_page:
            self.merge_nested_fragment_paragraphs(paragraphs)

        # 新增后处理：合并同一行被水平切分的标题/图题片段，以及表格单元格内的垂直多行
        if (
            not toc_page
            and getattr(self.translation_config, "merge_mid_sentence_paragraphs", True)
        ):
            self.merge_title_caption_and_table_fragments(paragraphs)

        # 新增后处理：合并表格内同一行水平重叠的段落（如 "Bi"+"ts" 被拆成两个段落）
        # 这类段落 box 在 x 方向重叠（gap < 0），排版引擎检测到重叠后会压缩 y 范围导致挤压
        if not toc_page:
            self.merge_overlapping_table_cells(paragraphs)

        # 新增后处理：合并被版面模型误切分的句中续接段落（如图片旁正文被切成多块）
        if (
            not toc_page
            and getattr(self.translation_config, "merge_mid_sentence_paragraphs", True)
        ):
            self.merge_mid_sentence_continuation_paragraphs(paragraphs)

        for paragraph in paragraphs:
            self.update_paragraph_data(paragraph, update_unicode=True)

        # 目录页：统一标题段/布局段的 y 基准（多行标题压回单行）
        if toc_page:
            toc_processor.normalize_boxes()

        if self.translation_config.ocr_workaround:
            self.add_text_fill_background(page)
            # since this is ocr file,
            # image characters are not needed
            page.pdf_character = []

        self.fix_overlapping_paragraphs(page)

        # 第六步：对每一行的字符进行排序
        # self._sort_characters_in_lines(page)

        self.add_debug_info(page)

        # 新阶段：设置段落的 renderorder 为所有组成部分中 renderorder 最小的
        self._set_paragraph_render_order(page)

    def _set_paragraph_render_order(self, page: Page):
        """
        设置段落的 renderorder 为段落所有组成部分中 renderorder 最小的值
        """
        for paragraph in page.pdf_paragraph:
            min_render_order = 9999999999999999

            # 遍历段落的所有组成部分
            for composition in paragraph.pdf_paragraph_composition:
                # 检查 PdfLine 中的字符
                if composition.pdf_line:
                    for char in composition.pdf_line.pdf_character:
                        if (
                            hasattr(char, "render_order")
                            and char.render_order is not None
                        ):
                            min_render_order = min(min_render_order, char.render_order)

                # 检查单个字符
                elif composition.pdf_character:
                    char = composition.pdf_character
                    if hasattr(char, "render_order") and char.render_order is not None:
                        min_render_order = min(min_render_order, char.render_order)

                # 检查公式中的字符
                elif composition.pdf_formula:
                    for char in composition.pdf_formula.pdf_character:
                        if (
                            hasattr(char, "render_order")
                            and char.render_order is not None
                        ):
                            min_render_order = min(min_render_order, char.render_order)

            # 如果找到了有效的 renderorder，设置段落的 renderorder
            if min_render_order != 9999999999999999:
                paragraph.render_order = min_render_order

    def is_isolated_formula(self, char: PdfCharacter):
        return char.char_unicode in (
            "(cid:122)",
            "(cid:123)",
            "(cid:124)",
            "(cid:125)",
        )

    def _paragraph_text_ascii(self, p: PdfParagraph) -> str:
        parts: list[str] = []
        for comp in p.pdf_paragraph_composition or []:
            if comp.pdf_line:
                for ch in comp.pdf_line.pdf_character or []:
                    if ch.char_unicode is not None:
                        parts.append(ch.char_unicode)
            elif comp.pdf_character and comp.pdf_character.char_unicode is not None:
                parts.append(comp.pdf_character.char_unicode)
        return "".join(parts)

    def _is_ascii_digit_or_space_paragraph(self, p: PdfParagraph) -> bool:
        text = self._paragraph_text_ascii(p)
        if not text:
            return True
        has_digit = False
        for c in text:
            if c.isdigit() and ord(c) < 128:
                has_digit = True
                continue
            if c.isspace():
                continue
            return False
        return True if has_digit or text.strip() == "" else False

    @staticmethod
    def _same_layout_and_xobj(a: PdfParagraph, c: PdfParagraph) -> bool:
        return (
            a.layout_id is not None
            and c.layout_id is not None
            and a.layout_id == c.layout_id
            and a.xobj_id is not None
            and c.xobj_id is not None
            and a.xobj_id == c.xobj_id
        )

    def merge_alternating_line_number_paragraphs(self, paragraphs: list[PdfParagraph]):
        # a 代表正文
        # l 代表行号
        if not paragraphs or len(paragraphs) < 3:
            return
        i = 0
        while i < len(paragraphs) - 2:
            a = paragraphs[i]
            # 表格区段落不参与行号交替合并（表格单元格行应保持独立）；
            # 参考文献条目（skip_translate）也不参与合并
            if getattr(a, "in_table_layout", False) or getattr(a, "skip_translate", False):
                i += 1
                continue
            # 吞掉一个或多个连续的行号段 l
            j = i + 1
            saw_l = False
            while j < len(paragraphs) and self._is_ascii_digit_or_space_paragraph(
                paragraphs[j]
            ):
                saw_l = True
                j += 1
            # 现在 j 指向候选的 c
            if saw_l and j < len(paragraphs):
                c = paragraphs[j]
                if (
                    not getattr(c, "in_table_layout", False)
                    and not getattr(c, "skip_translate", False)
                    and self._same_layout_and_xobj(a, c)
                ):
                    a.pdf_paragraph_composition.extend(c.pdf_paragraph_composition)
                    self.update_paragraph_data(a)
                    del paragraphs[j]
                    # 不移动 i，继续尝试把更多正文接到 a，实现 a l+ a l+ a ... 链式合并
                    continue
            i += 1

    def _paragraph_last_char(self, p: PdfParagraph) -> str:
        text = self._paragraph_text_ascii(p).rstrip()
        return text[-1] if text else ""

    def _paragraph_first_char(self, p: PdfParagraph) -> str:
        text = self._paragraph_text_ascii(p).lstrip()
        return text[0] if text else ""

    def _paragraph_is_list_item_start(self, p: PdfParagraph) -> bool:
        """判断段落是否以列表项标记开头（有序 1./a) 或无序 bullet）。

        供 merge_mid_sentence_continuation_paragraphs 排除列表项，避免
        "a) xxx" 这类列表项（小写字母开头）被误当作句中续接段落重新合并。
        """
        text = self._paragraph_text_ascii(p).lstrip()
        if not text:
            return False
        if bool(ParagraphFinder._ORDERED_LIST_ITEM_RE.match(text)):
            return True
        # 无序 bullet：取首个字符判断
        try:
            comps = p.pdf_paragraph_composition or []
            if comps and comps[0].pdf_line and comps[0].pdf_line.pdf_character:
                return is_bullet_point(comps[0].pdf_line.pdf_character[0])
        except Exception:
            pass
        return False

    @staticmethod
    def _estimate_line_pitch(p: PdfParagraph) -> float | None:
        """估算段落行距（相邻行中心的垂直间距中位数）。"""
        lines = [
            c.pdf_line for c in p.pdf_paragraph_composition or [] if c.pdf_line
        ]
        if not lines:
            return None
        if len(lines) >= 2:
            centers = sorted((line.box.y + line.box.y2) / 2 for line in lines)
            pitches = [b - a for a, b in zip(centers, centers[1:]) if b > a]
            if pitches:
                pitches.sort()
                mid = len(pitches) // 2
                return (
                    pitches[mid]
                    if len(pitches) % 2 == 1
                    else (pitches[mid - 1] + pitches[mid]) / 2
                )
        return (lines[0].box.y2 - lines[0].box.y) * 1.5

    @staticmethod
    def _last_line_height(p: PdfParagraph) -> float | None:
        for c in reversed(p.pdf_paragraph_composition or []):
            if c.pdf_line:
                return c.pdf_line.box.y2 - c.pdf_line.box.y
        return None

    @staticmethod
    def _first_line_height(p: PdfParagraph) -> float | None:
        for c in p.pdf_paragraph_composition or []:
            if c.pdf_line:
                return c.pdf_line.box.y2 - c.pdf_line.box.y
        return None

    _NOTE_START_RE = re.compile(r"^NOTE\s+\d+", re.IGNORECASE)

    def split_note_paragraphs(self, paragraphs: list[PdfParagraph]):
        """拆分包含多个 "NOTE N" 条目的段落。

        图表 Note 部分的多个 NOTE 条目常被版面模型归为同一布局块，
        合并为一个段落。LLM 翻译整段后逐行格式丢失。
        本函数检测段落中以 "NOTE N" 开头的行，按行拆分为独立段落，
        使每个 NOTE 条目独立翻译、保留原始行格式。
        """
        new_paragraphs = []
        for paragraph in paragraphs:
            comps = paragraph.pdf_paragraph_composition
            if not comps or len(comps) < 2:
                new_paragraphs.append(paragraph)
                continue

            # 获取每行的文本（仅 pdf_line 类型）
            line_texts = []
            for comp in comps:
                if comp.pdf_line:
                    text = get_char_unicode_string(
                        comp.pdf_line.pdf_character
                    ).strip()
                    line_texts.append(text)
                else:
                    line_texts.append(None)

            # 找到所有 "NOTE N" 开头的行索引
            note_indices = [
                i
                for i, t in enumerate(line_texts)
                if t and self._NOTE_START_RE.match(t)
            ]

            # NOTE 行不超过 1 个，不需要拆分
            if len(note_indices) <= 1:
                new_paragraphs.append(paragraph)
                continue

            logger.info(
                f"Splitting NOTE paragraph {paragraph.debug_id} into "
                f"{len(note_indices)} paragraphs"
            )

            # NOTE 之前的行（如果有）作为独立段落
            first_note_idx = note_indices[0]
            if first_note_idx > 0:
                pre_para = self._create_split_paragraph(
                    paragraph, comps[:first_note_idx]
                )
                if pre_para:
                    new_paragraphs.append(pre_para)

            # 按 NOTE 行拆分，每组从 NOTE 行到下一个 NOTE 行之前
            for gi, start_idx in enumerate(note_indices):
                end_idx = (
                    note_indices[gi + 1]
                    if gi + 1 < len(note_indices)
                    else len(comps)
                )
                group_comps = comps[start_idx:end_idx]
                note_para = self._create_split_paragraph(
                    paragraph, group_comps
                )
                if note_para:
                    new_paragraphs.append(note_para)

        paragraphs.clear()
        paragraphs.extend(new_paragraphs)

    def _create_split_paragraph(
        self, original: PdfParagraph, comps: list
    ) -> PdfParagraph | None:
        """从原段落的 composition 子集创建新段落。"""
        if not comps:
            return None
        new_para = PdfParagraph(
            pdf_paragraph_composition=list(comps),
            layout_id=original.layout_id,
            debug_id=generate_base58_id(),
            layout_label=original.layout_label,
        )
        new_para.pdf_style = original.pdf_style
        new_para.xobj_id = original.xobj_id
        self.update_paragraph_data(new_para, update_unicode=True)
        return new_para

    def _is_in_table_layout(
        self, paragraph: PdfParagraph, table_boxes: list[Box]
    ) -> bool:
        """判断段落是否落在任一 table 版面框内。

        以段落 box 中心点是否被某个 "table" 版面框包含为准。表格区单元格行
        （fallback_line）会被归为 True，从而跳过"句中续接"合并，保持每行独立。

        增强：除中心点外，还检查段落 box 与 table 框的重叠面积比（IOU > 50%），
        解决中心点偏移导致的漏标（如单元格行偏窄、中心点落在框外）。
        """
        if not table_boxes or paragraph.box is None:
            return False
        cx = (paragraph.box.x + paragraph.box.x2) / 2
        cy = (paragraph.box.y + paragraph.box.y2) / 2
        for box in table_boxes:
            # 原逻辑：中心点在 table 框内
            if box.x <= cx <= box.x2 and box.y <= cy <= box.y2:
                return True
            # 增强：段落 box 与 table 框重叠面积超过段落面积 50%
            x_overlap = max(
                0, min(paragraph.box.x2, box.x2) - max(paragraph.box.x, box.x)
            )
            y_overlap = max(
                0, min(paragraph.box.y2, box.y2) - max(paragraph.box.y, box.y)
            )
            overlap_area = x_overlap * y_overlap
            para_area = (paragraph.box.x2 - paragraph.box.x) * (
                paragraph.box.y2 - paragraph.box.y
            )
            if para_area > 0 and overlap_area / para_area > 0.5:
                return True
        return False

    def _mark_table_row_paragraphs(self, paragraphs: list[PdfParagraph]):
        """几何特征兜底：检测表格行段落，补充 in_table_layout 标记。

        版面模型（DocLayout）在全文翻译时对某些页的 table 框检测可能不准确，
        导致 in_table_layout 漏标、表格单元格被误合并。这里用几何特征兜底：
        同一行（y 中心差 < 3pt）有 >= 2 个其他短段落（< 30 字符）且 x 不重叠，
        识别为表格行。数值单元格（如 "2 Gb"、"4 Gb"）满足此特征。
        """
        # 已知 layout_label 名，用于排除幽灵段落
        _ghost = {"fallback_line", "plain text", "title", "abandon",
                  "table_caption", "figure_caption", "table", "figure"}
        short_paras = []
        for p in paragraphs:
            if p.box is None:
                continue
            text = (self._para_text(p) or "").strip()
            if text and len(text) < 30 and text.lower() not in _ghost:
                short_paras.append(p)

        for para in short_paras:
            if getattr(para, "in_table_layout", False):
                continue
            cy = (para.box.y + para.box.y2) / 2
            same_row = []
            for p in short_paras:
                if p is para:
                    continue
                pcy = (p.box.y + p.box.y2) / 2
                if abs(cy - pcy) < 3.0:
                    same_row.append(p)
            # 同一行有 >= 2 个其他短段落 -> 表格行
            if len(same_row) >= 2:
                para.in_table_layout = True

    # 参考文献标题（大小写不敏感，允许行尾空白）
    # 仅匹配复数形式 REFERENCES/References，不匹配单数 Reference
    # （避免表格列标题 "Reference" 被误判为参考文献章节标题）
    _REFERENCE_HEADER_RE = re.compile(
        r"^\s*(?:REFERENCES|BIBLIOGRAPHY)\s*$", re.IGNORECASE
    )
    # 参考文献条目格式参考（仅文档说明，标记逻辑已改为"标题下方所有段落"）:
    #   - IEEE 风格: "[1] JEDEC Standard High Bandwidth Memory..."
    #   - MDPI/Elsevier 风格: "1. Jun, H.; Cho, J.; Lee, K.; ..."
    #   - arXiv 无编号: "Yichuan Li, Kaize Ding, and Kyumin Lee. Grenade:..."

    def _mark_reference_paragraphs(self, paragraphs: list[PdfParagraph]):
        """标记 References/Bibliography 章节条目为 skip_translate。

        直接标记 REFERENCES 标题下方所有段落（不再依赖正则分阶段），
        覆盖 [N]（IEEE）/ N.（MDPI/Elsevier）/ 无编号（arXiv 作者名）及混合格式。
        覆盖 [N]/N./无编号/混合格式。排除 title（新章节）、abandon（页眉/页脚）、
        作者简介（连续大写字母开头，如 "WILLIAM J. DALLY is..."）。

        - 遇到新的 section 标题（layout_label=="title"）时退出参考文献模式
        - 跨页：上一页处于参考文献模式时，本页段落继续标记
        - 页眉/页脚（layout_label=="abandon"）和 REFERENCES 标题本身不标记
        - 注意：调用时机在 update_paragraph_data(update_unicode=True) 之前，
          段落的 unicode 属性尚未填充，需从 composition 自行拼接文本。
        """
        # 1) 本页是否有 REFERENCES 标题段
        found_header = False
        for para in paragraphs:
            text = self._para_text(para).strip()
            if text and self._REFERENCE_HEADER_RE.match(text):
                found_header = True
                break

        if found_header:
            self._in_references = True
        elif not self._in_references:
            return

        # 2) 计算 header_y（标题的 y 坐标，用于区分上下方）
        header_y = None
        if found_header:
            for para in paragraphs:
                text = self._para_text(para).strip()
                if text and self._REFERENCE_HEADER_RE.match(text) and para.box is not None:
                    header_y = para.box.y
                    break

        # 辅助：判断段落是否在标题下方（或跨页续接时整页均可）
        def _below_header(para):
            if not found_header or header_y is None or para.box is None:
                return True  # 跨页续接：整页都在参考文献区
            return para.box.y <= header_y

        # 辅助：判断是否为应跳过的非条目段落
        def _is_non_entry(para, text):
            if not text:
                return True
            if self._REFERENCE_HEADER_RE.match(text):
                return True  # 标题本身
            if para.layout_label == "abandon":
                return True  # 页眉/页脚
            return False

        # 标记标题下方所有段落为 skip_translate。
        # 不再依赖 _REFERENCE_ENTRY_RE 正则：直接标记标题下方所有段落，
        # 覆盖 [N]（IEEE）、N.（MDPI/Elsevier）、无编号（arXiv 作者名）及混合格式。
        # 排除：title（新 section 标题，退出模式）、abandon（页眉/页脚）、
        # 作者简介（连续大写字母开头，如 "WILLIAM J. DALLY is..."，避免双栏
        # 论文中参考文献后紧跟的作者简介被误标记）。
        marked_any = False
        exited = False
        for para in paragraphs:
            text = self._para_text(para).strip()
            if _is_non_entry(para, text):
                continue
            # 先判断是否在标题下方（视觉下方 y<=header_y）。标题上方的段落
            # （如参考文献章节前的正文小节标题 "Structural Design"）不属于
            # 参考文献区，不应触发"遇到新 section 标题退出"的逻辑，否则会把
            # _in_references 复位，破坏跨页延续（见补丁 24）。
            if not _below_header(para):
                continue
            # 遇到新的 section 标题（位于 References 标题下方）-> 退出参考文献模式
            if para.layout_label == "title":
                self._in_references = False
                exited = True
                continue
            if getattr(para, "skip_translate", False):
                marked_any = True
                continue
            # 跳过作者简介：连续大写字母开头（如 "WILLIAM J. DALLY is..."）
            if re.match(r"^[A-Z]{3,}\s", text):
                continue
            para.skip_translate = True
            marked_any = True

        # 3) 跨页续接判断：若本页未标记任何条目且无标题，退出参考文献模式
        if not found_header and not marked_any and not exited:
            self._in_references = False

    @staticmethod
    def _para_text(paragraph: PdfParagraph) -> str:
        """从段落 composition 拼接文本（在 update_unicode 之前可用）。"""
        if paragraph.unicode:
            return paragraph.unicode
        chars = []
        for comp in paragraph.pdf_paragraph_composition or []:
            if comp.pdf_line:
                chars.extend(comp.pdf_line.pdf_character)
            elif comp.pdf_character:
                chars.append(comp.pdf_character)
            elif comp.pdf_formula:
                chars.extend(comp.pdf_formula.pdf_character)
        return get_char_unicode_string(chars)

    # 允许嵌套合并的正文类布局标签（排除标题/图题/表格等特殊类）
    _NESTABLE_TEXT_LABELS = {
        "plain text", "text", "content", "fallback_line", "paragraph",
    }

    def _resplit_paragraph_lines_sorted(self, paragraph: PdfParagraph):
        """把段落的所有行打平成字符池，按视觉行重新分组并按 x 排序。

        用于嵌套碎片并回父段后恢复原文阅读顺序：父段的行与碎片的行
        在 y 上互嵌（同一视觉行被拆进不同段落），简单 extend 会导致
        字符顺序错乱；按 (y 行分组, 行内 x 排序) 重建行才能恢复。
        """
        all_chars = []
        for comp in paragraph.pdf_paragraph_composition or []:
            if comp.pdf_line:
                all_chars.extend(comp.pdf_line.pdf_character or [])
            elif comp.pdf_character:
                all_chars.append(comp.pdf_character)
        if not all_chars:
            return
        # 按视觉行分组：y 中心排序后，相邻中心差 > 4pt 视为新行
        # （同一行内正常字符/下标中心差 < ~3pt，相邻行差 ≈ 行距 > 10pt）
        all_chars.sort(
            key=lambda c: (-(c.box.y + c.box.y2) / 2, c.box.x)
        )
        rows = []
        cur_row = []
        cur_cy = None
        for ch in all_chars:
            cy = (ch.box.y + ch.box.y2) / 2
            if cur_row and cur_cy is not None and abs(cy - cur_cy) > 4.0:
                rows.append(cur_row)
                cur_row = []
            cur_row.append(ch)
            cur_cy = cy
        if cur_row:
            rows.append(cur_row)
        # 行内按 x 排序，重建 composition
        new_comps = []
        for row in rows:
            row.sort(key=lambda c: c.box.x)
            new_comps.append(self.create_line(row))
        paragraph.pdf_paragraph_composition = new_comps
        self.update_paragraph_data(paragraph)

    def merge_nested_fragment_paragraphs(self, paragraphs: list[PdfParagraph]):
        """合并 box 嵌套/交叠在父段内的碎片段落（布局模型嵌套输出）。

        布局模型偶尔把父段内部的字符（如行中间的续接文本、下标）
        切成独立段落，其 box 与父段 box 重叠。父段文本因此缺失中间
        片段且顺序错乱，碎片独立翻译后渲染时叠印在父段译文上
        （译文重叠不可读）。

        条件（保守）：
        - b 的 box 完全嵌套在 a 的 box 内（x/y 均包含，容差 1pt）；
          或行级交叠：b 的 y 范围包含于 a（或反之）且 x 重叠 > 0，
          且较小段面积 < 较大段的 60%（一大一小的碎片特征）
        - 同 xobj、同布局标签、均为正文类标签（plain text/text 等）
        - 排除表格/参考文献/skip_translate 段落
        合并方式：小段 composition 并入大段后按视觉行重分组 + 行内
        x 排序，恢复原文顺序。
        """
        if not paragraphs or len(paragraphs) < 2:
            return
        i = 0
        while i < len(paragraphs):
            a = paragraphs[i]
            if (
                a.box is None
                or not a.pdf_paragraph_composition
                or (a.layout_label or "") not in self._NESTABLE_TEXT_LABELS
                or getattr(a, "in_table_layout", False)
                or getattr(a, "skip_translate", False)
            ):
                i += 1
                continue
            merged_any = False
            j = 0
            while j < len(paragraphs):
                if j == i:
                    j += 1
                    continue
                b = paragraphs[j]
                if (
                    b.box is None
                    or not b.pdf_paragraph_composition
                    or b.xobj_id != a.xobj_id
                    or (b.layout_label or "") not in self._NESTABLE_TEXT_LABELS
                    or getattr(b, "in_table_layout", False)
                    or getattr(b, "skip_translate", False)
                ):
                    j += 1
                    continue
                nested = (
                    b.box.x >= a.box.x - 1
                    and b.box.x2 <= a.box.x2 + 1
                    and b.box.y >= a.box.y - 1
                    and b.box.y2 <= a.box.y2 + 1
                )
                if not nested:
                    # 行级交叠：一方 y 范围包含于另一方 + x 重叠 > 0
                    # + 面积比 < 60%（一大一小的碎片特征）
                    x_ov = min(a.box.x2, b.box.x2) - max(a.box.x, b.box.x)
                    y_b_in_a = (
                        b.box.y >= a.box.y - 1 and b.box.y2 <= a.box.y2 + 1
                    )
                    y_a_in_b = (
                        a.box.y >= b.box.y - 1 and a.box.y2 <= b.box.y2 + 1
                    )
                    area_a = (a.box.x2 - a.box.x) * (a.box.y2 - a.box.y)
                    area_b = (b.box.x2 - b.box.x) * (b.box.y2 - b.box.y)
                    if (
                        x_ov > 0
                        and (y_b_in_a or y_a_in_b)
                        and area_a > 0
                        and area_b > 0
                        and (
                            min(area_a, area_b) / max(area_a, area_b) < 0.6
                        )
                    ):
                        nested = True
                if not nested:
                    j += 1
                    continue
                area_a = (a.box.x2 - a.box.x) * (a.box.y2 - a.box.y)
                area_b = (b.box.x2 - b.box.x) * (b.box.y2 - b.box.y)
                if area_a <= 0 or area_b <= 0 or area_b > area_a * 0.5:
                    j += 1
                    continue
                # 并回父段并按视觉行重排，恢复原文顺序
                a.pdf_paragraph_composition.extend(b.pdf_paragraph_composition)
                self._resplit_paragraph_lines_sorted(a)
                del paragraphs[j]
                merged_any = True
                logger.info(
                    "Merged nested fragment"
                    f" {b.debug_id} into {a.debug_id}."
                )
                # 不推进 j，继续检查是否还有其他嵌套碎片
            if not merged_any:
                i += 1

    def merge_title_caption_and_table_fragments(
        self, paragraphs: list[PdfParagraph]
    ):
        """合并同一行被水平切分的标题/图题片段。

        版面模型常把标题/图题的首行切成多个水平相邻片段
        （例如表题 "Table 1. Thermal..." 被切成 "Table 1. T" + "hermal..." +
        "uctures."，节标题被切成 "2.6. Research" + "Advances..."）。各片段独立
        翻译后会出现英文残留（如 "T"/"chnology"）、译文逐词对应等碎片化问题。
        这里做保守的水平合并：
          - b 紧贴 a 右侧且与 a 处于同一行、a 结尾非句末
          - b 以小写字母开头（句中续接特征）任何布局标签均可
          - 或 a、b 均为标题类短片段（title/table_caption/figure_caption/fallback_line）
        均要求同 xobj、b 无首行缩进。
        """
        if not paragraphs or len(paragraphs) < 2:
            return
        title_like = {"title", "table_caption", "figure_caption", "fallback_line"}
        terminal = ".:;!?。：；！？"

        i = 0
        while i < len(paragraphs):
            a = paragraphs[i]
            if (
                a.box is None
                or getattr(a, "in_table_layout", False)
                or getattr(a, "skip_translate", False)
            ):
                i += 1
                continue
            last_ch = self._paragraph_last_char(a)
            if not last_ch or last_ch in terminal:
                i += 1
                continue
            if getattr(a, "toc_role", None) == "layout":
                i += 1
                continue
            a_cy = (a.box.y + a.box.y2) / 2
            best_j, best_gap = None, float("inf")
            for j in range(len(paragraphs)):
                if j == i:
                    continue
                b = paragraphs[j]
                if (
                    b.box is None
                    or getattr(b, "in_table_layout", False)
                    or getattr(b, "skip_translate", False)
                    or a.xobj_id != b.xobj_id
                    or b.first_line_indent
                    or getattr(b, "toc_role", None) == "layout"
                ):
                    continue
                b_cy = (b.box.y + b.box.y2) / 2
                if abs(a_cy - b_cy) > 4.0:  # 同一行
                    continue
                h_gap = b.box.x - a.box.x2  # b 紧贴 a 右侧
                if not (-0.5 <= h_gap <= 4.0):
                    continue
                first_ch = self._paragraph_first_char(b)
                if not first_ch:
                    continue
                a_label = a.layout_label or ""
                b_label = b.layout_label or ""
                is_lower_cont = first_ch.islower()
                is_title_pair = (
                    a_label in title_like
                    and b_label in title_like
                    and first_ch.isalpha()
                    and len((a.unicode or "")) <= 60
                    and len((b.unicode or "")) <= 80
                )
                # 下标/上标续接：b 与 a 同行紧贴且 b 行高明显小于 a
                # （如 "t" + 下标 "INIT2" 被版面切成两个水平片段）。
                # b 是 a 行末词的下标部分，必须并回 a，否则独立段渲染
                # 时下标起始定位偏移，产生大间距（t 与 INIT2 间隔异常）。
                h_a = self._last_line_height(a)
                h_b = self._first_line_height(b)
                is_subscript_cont = bool(
                    first_ch.isalnum()
                    and h_a
                    and h_b
                    and h_b < h_a * 0.75
                )
                if not (is_lower_cont or is_title_pair or is_subscript_cont):
                    continue
                if abs(h_gap) < best_gap:
                    best_j, best_gap = j, abs(h_gap)
            if best_j is not None:
                b = paragraphs[best_j]
                a.pdf_paragraph_composition.extend(b.pdf_paragraph_composition)
                self.update_paragraph_data(a)
                del paragraphs[best_j]
                logger.info(
                    "Merged horizontal fragment"
                    f" {b.debug_id} into {a.debug_id}."
                )
                continue  # 尝试继续把更右的片段并入
            i += 1

    def merge_overlapping_table_cells(self, paragraphs: list[PdfParagraph]):
        """合并表格内同一行水平重叠的段落。

        版面模型有时把表格表头的一个单元格拆成多个水平重叠的段落
        （如 "Bits" 拆成 "Bi" + "ts"，box 在 x 方向重叠）。
        排版引擎检测到 x 重叠后会压缩 y 范围，导致内容挤压不可读。
        本函数检测同属 table 布局框、同一行、x 重叠的短段落并合并。
        严格条件：y 中心差 <= 2pt（同一行），x 实际重叠（gap <= 0），
        排除幽灵段落（unicode 等于 layout_label 名）。
        """
        if not paragraphs or len(paragraphs) < 2:
            return
        # 已知的 layout_label 名，用于排除幽灵段落
        _ghost_texts = {"fallback_line", "plain text", "title", "abandon",
                        "table_caption", "figure_caption", "table", "figure"}
        i = 0
        while i < len(paragraphs):
            a = paragraphs[i]
            if a.box is None or not getattr(a, "in_table_layout", False):
                i += 1
                continue
            a_text = (a.unicode or "").strip()
            if not a_text or a_text.lower() in _ghost_texts:
                i += 1
                continue
            a_cy = (a.box.y + a.box.y2) / 2
            best_j = None
            best_gap = float("inf")
            for j in range(len(paragraphs)):
                if j == i:
                    continue
                b = paragraphs[j]
                if (
                    b.box is None
                    or not getattr(b, "in_table_layout", False)
                    or a.xobj_id != b.xobj_id
                ):
                    continue
                b_text = (b.unicode or "").strip()
                if not b_text or b_text.lower() in _ghost_texts:
                    continue
                b_cy = (b.box.y + b.box.y2) / 2
                if abs(a_cy - b_cy) > 2.0:  # 同一行（严格阈值）
                    continue
                # x 实际重叠（gap <= 0），不是仅仅靠近
                h_gap = b.box.x - a.box.x2
                if h_gap > 0:
                    continue
                # 只合并短段落（表头单元格碎片，非正文）
                if len(a_text) > 30 or len(b_text) > 30:
                    continue
                if abs(h_gap) < best_gap:
                    best_j = j
                    best_gap = abs(h_gap)
            if best_j is not None:
                b = paragraphs[best_j]
                a.pdf_paragraph_composition.extend(b.pdf_paragraph_composition)
                self.update_paragraph_data(a)
                del paragraphs[best_j]
                logger.info(
                    "Merged overlapping table cell"
                    f" {b.debug_id} into {a.debug_id}."
                )
                continue
            i += 1

    def merge_mid_sentence_continuation_paragraphs(self, paragraphs: list[PdfParagraph]):
        """合并被版面模型误切分的“句中续接”段落。

        版面模型有时会把一个连续段落（常见于图片旁的正文，布局检测被
        图片干扰）在句中切成多个布局块。各块独立翻译、独立排版缩放后，
        会出现字号缩小、块间空洞、译文截断在句中等排版劣化。

        仅在严格条件下合并（保守策略，避免误合并真正的分段）：
        - a 段末尾字符不是句末标点（. : ; ! ? 。：；！？）
        - b 段以小写字母开头（句中续接特征）
        - b 段无首行缩进（真分段通常缩进）
        - 同列相邻：b 在 a 正下方，垂直间距 <= 1.3 倍行距
        - 水平重叠超过较窄段落宽度的 50%
        - 相同布局标签、相同 xobj、首尾行高比在 0.6~1.6 之间
        """
        if not paragraphs or len(paragraphs) < 2:
            return
        terminal = ".:;!?。：；！？"
        i = 0
        while i < len(paragraphs):
            a = paragraphs[i]
            # 表格区段落不参与"句中续接"合并：表格单元格行本应每行独立，
            # 若被误并成一段，译文流式重排会挤压到表格顶部，丢失行对齐。
            # 参考文献条目（skip_translate）也不参与合并。
            if getattr(a, "in_table_layout", False) or getattr(a, "skip_translate", False):
                i += 1
                continue
            merged = False
            if a.box is not None:
                last_ch = self._paragraph_last_char(a)
                pitch = self._estimate_line_pitch(a)
                h_a = self._last_line_height(a)
                if (
                    last_ch
                    and last_ch not in terminal
                    and pitch
                    and pitch > 0
                    and h_a
                ):
                    # 双栏渲染顺序中列表相邻 != 几何相邻，
                    # 在全页段落中搜索 a 正下方几何最近的续接候选 b
                    best_j, best_gap = None, float("inf")
                    for j in range(len(paragraphs)):
                        if j == i:
                            continue
                        b = paragraphs[j]
                        if (
                            b.box is None
                            or getattr(b, "in_table_layout", False)
                            or getattr(b, "skip_translate", False)
                            or a.xobj_id != b.xobj_id
                            or (a.layout_label or "") != (b.layout_label or "")
                            or self._paragraph_is_list_item_start(b)
                        ):
                            continue
                        first_ch = self._paragraph_first_char(b)
                        h_b = self._first_line_height(b)
                        # 连词/逗号结尾放宽：a 以逗号或连词（and/or/with/to/of...）
                        # 结尾时，允许 b 以大写开头续接（正常句子不会以连词结尾，
                        # "static LOW and / HIGH levels" 这类句中切分可正确合并）
                        a_text_r = self._paragraph_text_ascii(a).rstrip()
                        a_last_word = (
                            re.split(r"\s+", a_text_r)[-1].lower().strip(".,;:")
                            if a_text_r
                            else ""
                        )
                        allow_upper_cont = a_text_r.endswith(",") or (
                            a_last_word
                            in self._CONTINUATION_CONJUNCTIONS
                        )
                        # 括号续接：a 末尾存在未闭合的左括号（如 "below 0.2 × VDDQ" 前是
                        # "(below..."），b 以 ")" 闭合（布局把括号内容切成两段，b 以右括号
                        # 开头、非字母，常规续接判定失效）。此时 b 是 a 的句中续接，豁免
                        # "小写字母开头"与 first_line_indent，并允许 b 与 a 垂直重叠。
                        a_open = a_text_r.count("(") - a_text_r.count(")")
                        is_paren_cont = bool(
                            first_ch in ")]"
                            and a_open > 0
                        )
                        if (
                            not first_ch
                            or not (
                                first_ch.isalpha() or is_paren_cont
                            )
                            or not (
                                first_ch.islower()
                                or allow_upper_cont
                                or is_paren_cont
                            )
                            or (not is_paren_cont and b.first_line_indent)
                            or not h_b
                            or not 0.6 <= h_a / h_b <= 1.6
                        ):
                            continue
                        gap = a.box.y - b.box.y2  # b 在 a 正下方
                        x_overlap = min(a.box.x2, b.box.x2) - max(a.box.x, b.box.x)
                        min_w = min(a.box.x2 - a.box.x, b.box.x2 - b.box.x)
                        # 括号续接的垂直重叠：b 的 box 与 a 底部对齐、覆盖下方多行，
                        # gap 为负；此时仍应允许合并。
                        if (
                            min_w > 0
                            and x_overlap > 0.5 * min_w
                            and gap <= pitch * 1.3
                            and (is_paren_cont or -2 <= gap)
                            and gap < best_gap
                        ):
                            best_j, best_gap = j, gap
                    if best_j is not None:
                        b = paragraphs[best_j]
                        a.pdf_paragraph_composition.extend(
                            b.pdf_paragraph_composition
                        )
                        self.update_paragraph_data(a)
                        del paragraphs[best_j]
                        merged = True
                        logger.info(
                            "Merged mid-sentence continuation paragraph"
                            f" {b.debug_id} into {a.debug_id}."
                        )
            if not merged:
                i += 1

    def _group_characters_into_paragraphs(
        self, page: Page, layout_index, layout_map
    ) -> list[PdfParagraph]:
        paragraphs: list[PdfParagraph] = []
        if page.pdf_paragraph:
            paragraphs.extend(page.pdf_paragraph)
            page.pdf_paragraph = []

        char_areas = [
            (char.visual_bbox.box.x2 - char.visual_bbox.box.x)
            * (char.visual_bbox.box.y2 - char.visual_bbox.box.y)
            for char in page.pdf_character
        ]
        median_char_area = 0.0
        if char_areas:
            char_areas.sort()
            mid = len(char_areas) // 2
            median_char_area = (
                char_areas[mid]
                if len(char_areas) % 2 == 1
                else (char_areas[mid - 1] + char_areas[mid]) / 2
            )

        current_paragraph: PdfParagraph | None = None
        current_layout: Layout | None = None
        skip_chars = []

        for char in page.pdf_character:
            char_layout = get_character_layout(char, layout_index, layout_map)
            # Check if character is in any formula layout and set formula_layout_id
            char.formula_layout_id = is_character_in_formula_layout(
                char, page, layout_index, layout_map
            )

            if not is_text_layout(char_layout) or self.is_isolated_formula(char):
                skip_chars.append(char)
                continue

            char_box = char.visual_bbox.box
            # char_pdf_box = char.box
            # if calculate_iou_for_boxes(char_box, char_pdf_box) < 0.2:
            #     char_box = char_pdf_box
            char_area = (char_box.x2 - char_box.x) * (char_box.y2 - char_box.y)
            is_small_char = char_area < median_char_area * 0.05

            is_new_paragraph = False
            if current_paragraph is None:
                is_new_paragraph = True
            elif (
                not (
                    is_small_char
                    and current_paragraph.pdf_paragraph_composition
                    and char_layout.id == current_layout.id
                )
                and char.char_unicode not in HEIGHT_NOT_USFUL_CHAR_IN_CHAR
            ):
                if (
                    (
                        char_layout.id != current_layout.id
                        and not SPACE_REGEX.match(char.char_unicode)
                    )
                    or (  # not same xobject
                        current_paragraph.pdf_paragraph_composition
                        and current_paragraph.pdf_paragraph_composition[
                            -1
                        ].pdf_character.xobj_id
                        != char.xobj_id
                    )
                    or (
                        is_bullet_point(char)
                        and not current_paragraph.pdf_paragraph_composition
                    )
                ):
                    is_new_paragraph = True

            if is_new_paragraph:
                current_layout = char_layout
                current_paragraph = PdfParagraph(
                    pdf_paragraph_composition=[],
                    layout_id=current_layout.id,
                    debug_id=generate_base58_id(),
                    layout_label=current_layout.name,
                )
                paragraphs.append(current_paragraph)

            current_paragraph.pdf_paragraph_composition.append(
                PdfParagraphComposition(pdf_character=char)
            )

        page.pdf_character = skip_chars
        for para in paragraphs:
            self.update_paragraph_data(para)
        return paragraphs

    def _merge_overlapping_clusters(
        self, lines: dict[int, list[PdfCharacter]], char_height_average: float
    ) -> dict[int, list[PdfCharacter]]:
        """
        Merge clusters that have significant y-axis overlap.
        If y_intersection / min_height > 0.5 or the distance between y-midlines is less than char_height_average, merge the two clusters.
        """
        if len(lines) <= 1:
            return lines

        # Calculate y-axis ranges for each cluster
        cluster_ranges = {}
        cluster_midlines = {}
        for label, chars in lines.items():
            y_values = [char.visual_bbox.box.y for char in chars] + [
                char.visual_bbox.box.y2 for char in chars
            ]
            y_min, y_max = min(y_values), max(y_values)
            cluster_ranges[label] = (y_min, y_max)
            cluster_midlines[label] = (y_min + y_max) / 2

        # Keep merging until no more merges are possible
        changed = True
        while changed:
            changed = False
            labels_to_check = list(lines.keys())

            for i in range(len(labels_to_check)):
                if not changed:  # Only continue if no merge happened in this iteration
                    for j in range(i + 1, len(labels_to_check)):
                        label1, label2 = labels_to_check[i], labels_to_check[j]

                        # Skip if either label has been merged away
                        if label1 not in lines or label2 not in lines:
                            continue

                        y1_min, y1_max = cluster_ranges[label1]
                        y2_min, y2_max = cluster_ranges[label2]

                        # Calculate intersection
                        intersection_start = max(y1_min, y2_min)
                        intersection_end = min(y1_max, y2_max)

                        # Calculate midline distance
                        midline_distance = abs(
                            cluster_midlines[label1] - cluster_midlines[label2]
                        )

                        should_merge = False
                        if (
                            intersection_end > intersection_start
                        ):  # There is intersection
                            intersection_height = intersection_end - intersection_start
                            height1 = y1_max - y1_min
                            height2 = y2_max - y2_min
                            min_height = min(height1, height2)

                            # Check if intersection ratio exceeds threshold
                            if (
                                min_height > 0
                                and intersection_height / min_height > 0.3
                            ):
                                should_merge = True

                        # Check if midline distance is less than char_height_average
                        if midline_distance < char_height_average:
                            should_merge = True

                        if should_merge:
                            # Merge label2 into label1
                            lines[label1].extend(lines[label2])
                            del lines[label2]

                            # Update cluster range and midline for the merged cluster
                            new_y_min = min(y1_min, y2_min)
                            new_y_max = max(y1_max, y2_max)
                            cluster_ranges[label1] = (new_y_min, new_y_max)
                            cluster_midlines[label1] = (new_y_min + new_y_max) / 2
                            del cluster_ranges[label2]
                            del cluster_midlines[label2]

                            changed = True
                            break

        return lines

    def _get_effective_y_bounds(self, char: PdfCharacter) -> tuple[float, float]:
        """
        Determines the effective vertical boundaries (y1, y2) for a character.

        It prioritizes the visual bounding box if its Intersection over Union (IoU)
        with the PDF bounding box is high (>= 0.5), otherwise, it falls back to the
        PDF bounding box. This helps use more accurate layout information when available.
        """
        visual_box = char.visual_bbox.box
        return visual_box.y, visual_box.y2
        pdf_box = char.box
        if calculate_iou_for_boxes(visual_box, pdf_box) >= 0.5:
            return visual_box.y, visual_box.y2
        return pdf_box.y, pdf_box.y2

    @staticmethod
    def _compute_collision_counts_histogram(
        y1_arr: np.ndarray,
        y2_arr: np.ndarray,
        para_y_min: float,
        para_y_max: float,
        step: float,
    ) -> np.ndarray:
        """Compute overlap counts at each scan line using a difference-array histogram.

        Args:
            y1_arr: 1-D array with lower y bounds of characters (inclusive).
            y2_arr: 1-D array with upper y bounds of characters (exclusive).
            para_y_min: Minimum y of the paragraph.
            para_y_max: Maximum y of the paragraph.
            step: Scan step size.

        Returns:
            1-D NumPy int32 array where index i corresponds to y = para_y_max - i × step.
        """
        # Number of scan positions
        m = int(np.ceil((para_y_max - para_y_min) / step))
        if m <= 0:
            return np.array([], dtype=np.int32)

        # Map character bounds to discrete indices (top inclusive, bottom exclusive)
        starts = np.floor((para_y_max - y2_arr) / step).astype(np.int32)
        ends = np.floor((para_y_max - y1_arr) / step).astype(np.int32) + 1
        # Clip ends to the valid range [0, m]
        np.clip(ends, 0, m, out=ends)

        hist = np.zeros(m + 1, dtype=np.int32)
        np.add.at(hist, starts, 1)
        np.add.at(hist, ends, -1)

        return np.cumsum(hist[:-1])

    def _split_paragraph_into_lines(
        self, paragraph: PdfParagraph, formula_font_ids: set[str]
    ):
        """
        Splits a paragraph into lines using a "line-threading" method.

        This method works by scanning vertically across the paragraph's bounding
        box and counting how many characters intersect with a horizontal line
        at each y-coordinate. The regions with a low number of intersections
        (less than 2) are identified as gaps between lines. The characters
        are then partitioned into lines based on these identified gaps.
        """
        if not paragraph.pdf_paragraph_composition:
            return

        # 1. Extract all characters and other compositions from the paragraph.
        all_chars: list[PdfCharacter] = []
        other_compositions: list[PdfParagraphComposition] = []
        for comp in paragraph.pdf_paragraph_composition:
            if comp.pdf_character:
                all_chars.append(comp.pdf_character)
            else:
                other_compositions.append(comp)

        if not all_chars:
            return

        # 2. Determine effective y-bounds for each character and the paragraph's total vertical range.
        char_y_bounds = [
            {"char": char, "y1": y1, "y2": y2}
            for char in all_chars
            for y1, y2 in [self._get_effective_y_bounds(char)]
        ]

        if not char_y_bounds:
            paragraph.pdf_paragraph_composition = other_compositions
            self.update_paragraph_data(paragraph)
            return

        para_y_min = min(b["y1"] for b in char_y_bounds)
        para_y_max = max(b["y2"] for b in char_y_bounds)

        # If the paragraph is vertically flat, treat it as a single line.
        if (para_y_max - para_y_min) < 5:  # Using a small threshold
            # all_chars.sort(key=lambda c: c.visual_bbox.box.x)
            single_line_composition = self.create_line(all_chars)
            paragraph.pdf_paragraph_composition = [
                single_line_composition
            ] + other_compositions
            self.update_paragraph_data(paragraph)
            return

        # 3. Perform "threading" scan to create a collision histogram.
        # Scan from top (max y) to bottom (min y) with a step of 0.5.
        scan_y_min = para_y_min
        scan_y_max = para_y_max
        step = 0.25

        y_coordinates = np.arange(scan_y_max, scan_y_min, -step)

        # Compute collision counts using NumPy histogram (O(m + n))
        y1_arr = np.array([b["y1"] for b in char_y_bounds], dtype=np.float32)
        y2_arr = np.array([b["y2"] for b in char_y_bounds], dtype=np.float32)
        collision_counts = self._compute_collision_counts_histogram(
            y1_arr,
            y2_arr,
            scan_y_min,
            scan_y_max,
            step,
        )

        # 4. Find gaps (regions with low collision count) from the histogram.
        gaps = []
        in_gap = False
        for i, count in enumerate(collision_counts):
            if count < 1 and not in_gap:
                in_gap = True
                gap_start_index = i
            elif count >= 1 and in_gap:
                in_gap = False
                gaps.append((gap_start_index, i - 1))
        if in_gap:
            gaps.append((gap_start_index, len(collision_counts) - 1))

        # If no significant gaps are found, treat it as a single line.
        if not gaps:
            # all_chars.sort(key=lambda c: c.visual_bbox.box.x)
            single_line_composition = self.create_line(all_chars)
            paragraph.pdf_paragraph_composition = [
                single_line_composition
            ] + other_compositions
            self.update_paragraph_data(paragraph)
            return

        # 5. Assign characters to lines based on the identified gaps.
        # Calculate separator y-coordinates from the midpoints of the gaps.
        separator_y_coords = sorted(
            [y_coordinates[start_idx] for start_idx, end_idx in gaps],
            reverse=True,
        )

        lines: list[list[PdfCharacter]] = [
            [] for _ in range(len(separator_y_coords) + 1)
        ]

        for b in char_y_bounds:
            char_y_center = (b["y1"] + b["y2"]) / 2
            line_idx = 0
            # Find which line bucket the character belongs to.
            for sep_y in separator_y_coords:
                if char_y_center > sep_y:
                    break
                line_idx += 1
            lines[line_idx].append(b["char"])

        # 6. Rebuild the paragraph's composition list from the new lines.
        new_line_compositions = []
        for line_chars in lines:
            if line_chars:
                # Sort characters within each line by x-coordinate (left-to-right).
                # line_chars.sort(key=lambda c: c.visual_bbox.box.x)
                new_line_compositions.append(self.create_line(line_chars))

        # The lines are already sorted vertically due to the scanning process.
        paragraph.pdf_paragraph_composition = new_line_compositions + other_compositions
        self.update_paragraph_data(paragraph)

    def process_paragraph_spacing(self, paragraph: PdfParagraph):
        if not paragraph.pdf_paragraph_composition:
            return

        # 处理行级别的空格
        processed_lines = []
        for composition in paragraph.pdf_paragraph_composition:
            if not composition.pdf_line:
                processed_lines.append(composition)
                continue

            line = composition.pdf_line
            if not "".join(
                x.char_unicode for x in line.pdf_character
            ).strip():  # 跳过完全空白的行
                continue

            # 处理行内字符的尾随空格
            processed_chars = []
            for char in line.pdf_character:
                if not char.char_unicode.isspace():
                    processed_chars = processed_chars + [char]
                elif processed_chars:  # 只有在有非空格字符后才考虑保留空格
                    processed_chars.append(char)

            # 移除尾随空格
            while processed_chars and processed_chars[-1].char_unicode.isspace():
                processed_chars.pop()

            if processed_chars:  # 如果行内还有字符
                line = self.create_line(processed_chars)
                processed_lines.append(line)

        paragraph.pdf_paragraph_composition = processed_lines
        self.update_paragraph_data(paragraph)

    def create_line(self, chars: list[PdfCharacter]) -> PdfParagraphComposition:
        assert chars

        line = PdfLine(pdf_character=chars)
        self.update_line_data(line)
        return PdfParagraphComposition(pdf_line=line)

    def calculate_median_line_width(self, paragraphs: list[PdfParagraph]) -> float:
        # 收集所有行的宽度
        line_widths = []
        for paragraph in paragraphs:
            for composition in paragraph.pdf_paragraph_composition:
                if composition.pdf_line:
                    line = composition.pdf_line
                    line_widths.append(line.box.x2 - line.box.x)

        if not line_widths:
            return 0.0

        # 计算中位数
        line_widths.sort()
        mid = len(line_widths) // 2
        if len(line_widths) % 2 == 0:
            return (line_widths[mid - 1] + line_widths[mid]) / 2
        return line_widths[mid]

    # 有序列表项起始标记（行首）：如 "1. " "2) " "a) " "b. " 等。
    # 数字/字母后接 . 、) 或 ）、并跟空白，作为列表项开头。
    # 要求后跟空白，避免把 "Figure 5." "R[3:0]." 等正文误判为列表项。
    _ORDERED_LIST_ITEM_RE = re.compile(
        r"^(?:\d+|[a-zA-Z])\s*[.、)）]\s+"
    )

    # 句中续接连词：段落以这些词结尾（无句末标点）时，视为明显的句中切分，
    # 允许下一段以大写字母开头续接合并（正常完整句子不会以连词结尾）。
    _CONTINUATION_CONJUNCTIONS = {
        "and", "or", "but", "with", "to", "of", "for", "the", "a", "an",
        "in", "on", "by", "from", "at", "as", "when", "while", "if",
        "that", "which", "whose", "than", "during", "before", "after",
        "between", "within", "into", "onto", "over", "under", "not",
    }

    @staticmethod
    def _is_list_item_start(chars: list) -> bool:
        """判断一行字符是否以列表项标记开头（有序如 1./a)，或无序 bullet）。

        chars: pdf_line.pdf_character 列表（PdfCharacter，含 char_unicode）。
        """
        if not chars:
            return False
        first = chars[0]
        # 无序 bullet 点
        try:
            if is_bullet_point(first):
                return True
        except Exception:
            pass
        # 有序列表标记：取行首若干字符拼文本匹配
        prefix = "".join(
            getattr(c, "char_unicode", "") for c in chars[:6]
        )
        return bool(ParagraphFinder._ORDERED_LIST_ITEM_RE.match(prefix))

    def _split_bullets_in_merged_lines(self, paragraphs: list[PdfParagraph]):
        """把"多个无序列表项被并成一行"的行按 bullet 字符拆开。

        行分组（_split_paragraph_into_lines 的 threading 扫描）要求行间隙
        完全无字符才能分行；行距紧或 bullet 字符 y 略低时相邻行的间隙
        消失，多个垂直排列的 bullet 项会被并成一行（如
        "• A • B • C"）。此处检测行内 >=2 个 bullet 字符且分属不同视觉行
        （y 中心差超过阈值），按 bullet 字符把行字符切成多组、重组为
        多行，使后续行级列表项拆分（process_independent_paragraphs）
        能够生效。
        """
        for paragraph in paragraphs:
            comps = paragraph.pdf_paragraph_composition
            if not comps or len(comps) < 1:
                continue
            if getattr(paragraph, "in_table_layout", False) or getattr(
                paragraph, "skip_translate", False
            ):
                continue
            new_comps = []
            changed = False
            for comp in comps:
                line = comp.pdf_line
                if not line or not line.pdf_character:
                    new_comps.append(comp)
                    continue
                chars = list(line.pdf_character)
                bullet_idx = [
                    k for k, c in enumerate(chars) if is_bullet_point(c)
                ]
                if len(bullet_idx) < 2:
                    new_comps.append(comp)
                    continue
                # bullet 的 y 中心；不同视觉行的 bullet 中心差 ≈ 行距（>10pt），
                # 同一行内多个 bullet 中心差 ≈ 0。阈值 3pt 区分两者。
                centers = []
                for k in bullet_idx:
                    y1, y2 = self._get_effective_y_bounds(chars[k])
                    centers.append((y1 + y2) / 2)
                multi_visual_rows = any(
                    abs(centers[m + 1] - centers[m]) > 3.0
                    for m in range(len(centers) - 1)
                )
                if not multi_visual_rows:
                    new_comps.append(comp)
                    continue
                # 按 bullet 切分：每组从 bullet 开始到下一个 bullet 前；
                # 第一个 bullet 之前的前缀字符（若有）单独成组
                first = bullet_idx[0]
                groups = []
                if first > 0:
                    groups.append(chars[:first])
                for gi, k in enumerate(bullet_idx):
                    end = (
                        bullet_idx[gi + 1]
                        if gi + 1 < len(bullet_idx)
                        else len(chars)
                    )
                    groups.append(chars[k:end])
                for g in groups:
                    if g:
                        new_comps.append(self.create_line(g))
                changed = True
            if changed:
                paragraph.pdf_paragraph_composition = new_comps
                self.update_paragraph_data(paragraph)

    def process_independent_paragraphs(
        self,
        paragraphs: list[PdfParagraph],
        median_width: float,
    ):
        i = 0
        while i < len(paragraphs):
            paragraph = paragraphs[i]
            if len(paragraph.pdf_paragraph_composition) <= 1:  # 跳过只有一行的段落
                i += 1
                continue

            j = 1
            while j < len(paragraph.pdf_paragraph_composition):
                prev_composition = paragraph.pdf_paragraph_composition[j - 1]
                if not prev_composition.pdf_line:
                    j += 1
                    continue

                prev_line = prev_composition.pdf_line
                prev_width = prev_line.box.x2 - prev_line.box.x
                prev_text = "".join([c.char_unicode for c in prev_line.pdf_character])

                # 检查是否包含连续的点（至少 20 个）
                # 如果有至少连续 20 个点，则代表这是目录条目
                if re.search(r"\.{20,}", prev_text):
                    # 创建新的段落
                    new_paragraph = PdfParagraph(
                        box=Box(0, 0, 0, 0),  # 临时边界框
                        pdf_paragraph_composition=(
                            paragraph.pdf_paragraph_composition[j:]
                        ),
                        unicode="",
                        debug_id=generate_base58_id(),
                        layout_label=paragraph.layout_label,
                        layout_id=paragraph.layout_id,
                    )
                    # 更新原段落
                    paragraph.pdf_paragraph_composition = (
                        paragraph.pdf_paragraph_composition[:j]
                    )

                    # 更新两个段落的数据
                    self.update_paragraph_data(paragraph)
                    self.update_paragraph_data(new_paragraph)

                    # 在原段落后插入新段落
                    paragraphs.insert(i + 1, new_paragraph)
                    break

                # 如果前一行宽度小于中位数的一半，将当前行及后续行分割成新段落；
                # 或当前行以列表项标记开头（有序 1./a) 或无序 bullet），强制拆出新段落
                if (
                    self.translation_config.split_short_lines
                    and prev_width
                    < median_width * self.translation_config.short_line_split_factor
                ) or (
                    paragraph.pdf_paragraph_composition
                    and (current_line := paragraph.pdf_paragraph_composition[j])
                    and (line := current_line.pdf_line)
                    and (chars := line.pdf_character)
                    and self._is_list_item_start(chars)
                ):
                    # 创建新的段落
                    new_paragraph = PdfParagraph(
                        box=Box(0, 0, 0, 0),  # 临时边界框
                        pdf_paragraph_composition=(
                            paragraph.pdf_paragraph_composition[j:]
                        ),
                        unicode="",
                        debug_id=generate_base58_id(),
                        layout_label=paragraph.layout_label,
                        layout_id=paragraph.layout_id,
                    )
                    # 更新原段落
                    paragraph.pdf_paragraph_composition = (
                        paragraph.pdf_paragraph_composition[:j]
                    )

                    # 更新两个段落的数据
                    self.update_paragraph_data(paragraph)
                    self.update_paragraph_data(new_paragraph)

                    # 在原段落后插入新段落
                    paragraphs.insert(i + 1, new_paragraph)
                    break
                j += 1
            i += 1

    @staticmethod
    def is_bbox_contain_in_vertical(bbox1: Box, bbox2: Box) -> bool:
        """Check if one bounding box is completely contained within the other."""
        # Check if bbox1 is contained in bbox2
        bbox1_in_bbox2 = bbox1.y >= bbox2.y and bbox1.y2 <= bbox2.y2
        # Check if bbox2 is contained in bbox1
        bbox2_in_bbox1 = bbox2.y >= bbox1.y and bbox2.y2 <= bbox1.y2
        return bbox1_in_bbox2 or bbox2_in_bbox1

    def fix_overlapping_paragraphs(self, page: Page):
        """
        Adjusts the bounding boxes of paragraphs on a page to resolve vertical overlaps.

        Iteratively checks pairs of paragraphs and adjusts their vertical boundaries
        (y and y2) if they overlap, aiming to place the boundary at the midpoint
        of the vertical overlap.
        """
        paragraphs = page.pdf_paragraph
        if not paragraphs or len(paragraphs) < 2:
            return

        max_iterations = len(paragraphs) * len(paragraphs)  # Safety break
        iterations = 0

        while iterations < max_iterations:
            iterations += 1
            overlap_found_in_pass = False

            for i in range(len(paragraphs)):
                for j in range(i + 1, len(paragraphs)):
                    para1 = paragraphs[i]
                    para2 = paragraphs[j]

                    if para1.box is None or para2.box is None:
                        continue

                    # 目录页的标题段/布局段同处一行，属于同一目录条目的两个部分，
                    # 不参与垂直避让调整（否则会把布局段挤下一行）。
                    if getattr(para1, "toc_role", None) or getattr(
                        para2, "toc_role", None
                    ):
                        continue

                    if para1.xobj_id != para2.xobj_id:
                        continue

                    # Check for overlap using the existing method
                    if self.bbox_overlap(para1.box, para2.box):
                        if self.is_bbox_contain_in_vertical(para1.box, para2.box):
                            continue
                        # Calculate vertical overlap details
                        overlap_y_start = max(para1.box.y, para2.box.y)
                        overlap_y_end = min(para1.box.y2, para2.box.y2)
                        overlap_height = overlap_y_end - overlap_y_start

                        # Calculate horizontal overlap details
                        overlap_x_start = max(para1.box.x, para2.box.x)
                        overlap_x_end = min(para1.box.x2, para2.box.x2)
                        overlap_width = overlap_x_end - overlap_x_start

                        # Ensure there's a real 2D overlap, focusing on vertical adjustment
                        if overlap_height > 1e-6 and overlap_width > 1e-6:
                            overlap_found_in_pass = True

                            # Determine which paragraph is visually higher
                            if para1.box.y2 > para2.box.y and para1.box.y < para2.box.y:
                                lower_para = para1
                                higher_para = para2
                            # Handle cases where y values are identical (or very close)
                            # Prefer the one with smaller y2 as the higher one, or break tie arbitrarily
                            elif para1.box.y2 < para2.box.y2:
                                lower_para = para1
                                higher_para = para2
                            else:
                                lower_para = para2
                                higher_para = para1

                            # Calculate the midpoint of the vertical overlap
                            mid_y = overlap_y_start + overlap_height / 2

                            # Adjust boxes, ensuring they remain valid (y2 > y)
                            if mid_y > higher_para.box.y and mid_y < lower_para.box.y2:
                                higher_para.box.y = mid_y + 1
                                lower_para.box.y2 = mid_y - 1
                            else:
                                # This might happen if one box is fully contained vertically
                                # within another, or due to floating point issues.
                                # Log a warning and skip adjustment for this pair in this iteration.
                                # A more complex strategy might be needed for full containment.
                                logger.warning(
                                    "Could not resolve overlap between paragraphs"
                                    f" {higher_para.debug_id} and {lower_para.debug_id}"
                                    " using simple midpoint strategy."
                                    f" Midpoint: {mid_y},"
                                    f" Higher Box: {higher_para.box},"
                                    f" Lower Box: {lower_para.box}"
                                )

            # If no overlaps were found and adjusted in this pass, we're done.
            if not overlap_found_in_pass:
                break

        if iterations == max_iterations:
            logger.warning(
                f"Maximum iterations ({max_iterations}) reached in"
                f" fix_overlapping_paragraphs for page {page.page_number}."
                " Some overlaps might remain."
            )

    def _sort_characters_in_lines(self, page: Page):
        """Sort characters in each line from left to right, top to bottom."""
        for paragraph in page.pdf_paragraph:
            for composition in paragraph.pdf_paragraph_composition:
                if composition.pdf_line:
                    line = composition.pdf_line
                    line.pdf_character.sort(key=self._get_char_sort_key)

    def _get_char_sort_key(self, char: PdfCharacter):
        """Get sort key for character positioning (top to bottom, left to right)."""
        visual_box = char.visual_bbox.box
        pdf_box = char.box

        # Use visual box if IoU with bbox is >= 0.1, otherwise use bbox
        if calculate_iou_for_boxes(visual_box, pdf_box) >= 0.1:
            box = visual_box
        else:
            box = pdf_box

        # Sort by y coordinate first (top to bottom), then x coordinate (left to right)
        # Note: In PDF coordinate system, y increases upward, so we negate y for top-to-bottom sorting
        return (box.x, -box.y)
