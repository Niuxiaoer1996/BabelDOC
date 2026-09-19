import math
import re

from babeldoc.format.pdf.document_il.il_version_1 import Box
from babeldoc.format.pdf.document_il.il_version_1 import Document
from babeldoc.format.pdf.document_il.il_version_1 import GraphicState
from babeldoc.format.pdf.document_il.il_version_1 import Page
from babeldoc.format.pdf.document_il.il_version_1 import PdfCharacter
from babeldoc.format.pdf.document_il.il_version_1 import PdfFormula
from babeldoc.format.pdf.document_il.il_version_1 import PdfLine
from babeldoc.format.pdf.document_il.il_version_1 import PdfParagraphComposition
from babeldoc.format.pdf.document_il.il_version_1 import PdfSameStyleCharacters
from babeldoc.format.pdf.document_il.il_version_1 import PdfStyle
from babeldoc.format.pdf.document_il.utils.fontmap import FontMapper
from babeldoc.format.pdf.document_il.utils.formular_helper import (
    collect_page_formula_font_ids,
)
from babeldoc.format.pdf.document_il.utils.formular_helper import (
    is_formulas_middle_char,
)
from babeldoc.format.pdf.document_il.utils.formular_helper import is_formulas_start_char
from babeldoc.format.pdf.document_il.utils.formular_helper import update_formula_data
from babeldoc.format.pdf.document_il.utils.layout_helper import LEFT_BRACKET
from babeldoc.format.pdf.document_il.utils.layout_helper import RIGHT_BRACKET
from babeldoc.format.pdf.document_il.utils.layout_helper import build_layout_index
from babeldoc.format.pdf.document_il.utils.layout_helper import calculate_iou_for_boxes
from babeldoc.format.pdf.document_il.utils.layout_helper import (
    calculate_y_true_iou_for_boxes,
)
from babeldoc.format.pdf.document_il.utils.layout_helper import is_bullet_point
from babeldoc.format.pdf.document_il.utils.layout_helper import (
    is_curve_in_figure_table_layout,
)
from babeldoc.format.pdf.document_il.utils.layout_helper import (
    is_curve_overlapping_with_paragraphs,
)
from babeldoc.format.pdf.document_il.utils.layout_helper import is_same_style
from babeldoc.format.pdf.document_il.utils.layout_helper import formular_height_ignore_char

# 严格 bullet 模式：仅包含真正的 bullet 字符（•、◦、■ 等），
# 不包含上标/下标数字和字母（¹²³⁴⁵⁶⁷⁸⁹⁰₁₂₃₄₅₆₇₈₉₀ᵃᵇᶜ...），
# 也不包含数学运算符（∗·‖†‡ 等），因为后者在数学公式中常见。
_BULLET_FOR_FORMULA_EXCLUSION = re.compile(
    r"[\u25a0\u2022\u26ab\u2b24\u25c6\u25c7\u25cb\u25cf\u25e6"
    r"\u2023\u2043\u25aa\u25ab\u00b6\u203b\u2042\u2055"
    r"\u204e\u205c\u2767\u2619\u204b"
    r"\uf09e\uf09f\uf0a7\uf0b7\uf0d8\uf0e0]"
)
# 普通文本里的"字面符号"：数字、加号、方括号、等号。它们在正文/目录里是普通文本
# （如 X16+ X13+... 的 16/+、[35,36] 的方括号、RW01[4] 的 [4]、PG70RW60[7] = 0 的 =），
# 不应被判成公式占位符 {vN}。若判成占位符，弱模型改写/丢弃后内容会被追到段尾或丢格式。
# 注意：仅限 ASCII 基础符号——× · − < > ~ ± µ ° 等符号在目标 CJK 字体里可能被映射成
# 错误字形（µ→×、×→·），必须仍按公式保留原字形，不进此列表。
# 真实的上下标数字由 is_corner_mark 命中（字号更小/基线偏移），不受此压制影响。
_PLAIN_INLINE_CHAR_RE = re.compile(r"[0-9+\]\[=]")
# 上下标的基线 y 偏移阈值（pt）。同字号但抬高/降低的上下标（如 m⁻¹ 中的 ⁻，字号比
# 0.79 临界漏判）用基线偏移识别。同一行普通文本 char.box.y 完全相同（0 偏移），故安全。
_CORNER_Y_RAISE_PT = 2.0
# 分式内部允许并入公式的数学符号（普通字体）：分子/分母被这些符号拆成独立公式时，
# 合并垂直分式需把夹在分子/分母之间的此类符号文本一并并入公式（relocate 保留其 rel_y）。
_FRACTION_SYMBOL_RE = re.compile(r"^[\\(\)\.\,\%\=\+\-\:\;×÷]$")
from babeldoc.format.pdf.document_il.utils.spatial_analyzer import (
    is_element_contained_in_formula,
)
from babeldoc.format.pdf.translation_config import TranslationConfig


class StylesAndFormulas:
    stage_name = "Parse Formulas and Styles"

    def __init__(self, translation_config: TranslationConfig):
        self.translation_config = translation_config
        self.font_mapper = FontMapper(translation_config)

    def update_formula_data(self, formula: PdfFormula):
        update_formula_data(formula)

    def process(self, document: Document):
        with self.translation_config.progress_monitor.stage_start(
            self.stage_name,
            len(document.page),
        ) as pbar:
            for page in document.page:
                self.translation_config.raise_if_cancelled()
                self.process_page(page)
                pbar.advance()

    def update_all_formula_data(self, page: Page):
        for para in page.pdf_paragraph:
            for comp in para.pdf_paragraph_composition:
                if comp.pdf_formula:
                    self.update_formula_data(comp.pdf_formula)

    def _calculate_element_formula_iou(
        self, element_box: Box, formula_box: Box, tolerance: float = 2.0
    ) -> float:
        """Calculate precise IoU between an element and a formula with tolerance.

        Args:
            element_box: Bounding box of the element (curve/form)
            formula_box: Bounding box of the formula
            tolerance: Tolerance to expand formula box for containment check

        Returns:
            IoU value between element and expanded formula box
        """
        if element_box is None or formula_box is None:
            return 0.0

        # Expand formula box by tolerance for more lenient containment check
        expanded_formula_box = Box(
            x=formula_box.x - tolerance,
            y=formula_box.y - tolerance,
            x2=formula_box.x2 + tolerance,
            y2=formula_box.y2 + tolerance,
        )

        return calculate_iou_for_boxes(element_box, expanded_formula_box)

    def _is_element_contained_exact(
        self,
        element_box: Box,
        formula_box: Box,
        containment_threshold: float = 0.95,
    ) -> bool:
        """Check if an element is contained within a formula with zero tolerance.

        Args:
            element_box: Bounding box of the element (curve/form)
            formula_box: Bounding box of the formula
            containment_threshold: Minimum IoU ratio to consider as contained

        Returns:
            True if the element is contained within the formula (exact match)
        """
        if element_box is None or formula_box is None:
            return False

        # Use formula box without any tolerance expansion
        iou = calculate_iou_for_boxes(element_box, formula_box)
        return iou >= containment_threshold

    def _calculate_element_formula_distance(
        self, element_box: Box, formula_box: Box
    ) -> float:
        """Calculate the shortest distance between an element and a formula.

        Args:
            element_box: Bounding box of the element (curve/form)
            formula_box: Bounding box of the formula

        Returns:
            Shortest distance between the element and formula boxes
        """
        if element_box is None or formula_box is None:
            return float("inf")

        # Calculate horizontal distance
        if element_box.x2 < formula_box.x:
            # Element is to the left of formula
            dx = formula_box.x - element_box.x2
        elif element_box.x > formula_box.x2:
            # Element is to the right of formula
            dx = element_box.x - formula_box.x2
        else:
            # Horizontal overlap
            dx = 0.0

        # Calculate vertical distance
        if element_box.y2 < formula_box.y:
            # Element is above formula
            dy = formula_box.y - element_box.y2
        elif element_box.y > formula_box.y2:
            # Element is below formula
            dy = element_box.y - formula_box.y2
        else:
            # Vertical overlap
            dy = 0.0

        # Return Euclidean distance
        return (dx * dx + dy * dy) ** 0.5

    def _collect_element_formula_candidates(
        self, page: Page
    ) -> tuple[list, dict, dict]:
        """Collect all potential assignments of elements to formulas.

        Uses two-level IoU matching strategy:
        1. Exact IoU matching (zero tolerance) - highest priority
        2. Tolerant IoU matching (2.0 tolerance, distance-sorted) - second priority

        Returns:
            Tuple of (all_formulas, curve_candidates, form_candidates) where:
            - all_formulas: list of (formula, paragraph_xobj_id) tuples
            - curve_candidates: dict mapping curve index to (curve, candidates) tuples
            - form_candidates: dict mapping form index to (form, candidates) tuples
            where candidates is a list of (formula_index, score, match_type) tuples
        """
        curve_candidates = {}
        form_candidates = {}

        # Configuration parameters
        max_tolerant_distance = 100.0  # Maximum distance for tolerant matching scoring

        if not page.pdf_paragraph:
            return [], curve_candidates, form_candidates

        # Collect all formulas from all paragraphs with their index
        all_formulas = []
        for paragraph in page.pdf_paragraph:
            for composition in paragraph.pdf_paragraph_composition:
                if composition.pdf_formula:
                    all_formulas.append((composition.pdf_formula, paragraph.xobj_id))

        # Check each curve against all formulas
        for curve_idx, curve in enumerate(page.pdf_curve):
            if not curve.box:
                continue

            candidates = []
            for formula_idx, (formula, paragraph_xobj_id) in enumerate(all_formulas):
                if not formula.box:
                    continue

                # Check xobj_id compatibility
                if paragraph_xobj_id is not None and curve.xobj_id != paragraph_xobj_id:
                    continue

                # Level 1: Exact IoU matching (zero tolerance) - highest priority
                if self._is_element_contained_exact(curve.box, formula.box):
                    iou = calculate_iou_for_boxes(curve.box, formula.box)
                    candidates.append((formula_idx, iou, "iou_exact"))
                # Level 2: Tolerant IoU matching (with tolerance) - distance sorted
                elif is_element_contained_in_formula(curve.box, formula.box):
                    distance = self._calculate_element_formula_distance(
                        curve.box, formula.box
                    )
                    # Convert distance to score (closer = higher score)
                    # Score range: 0.5-0.9 to ensure lower than exact IoU
                    distance_factor = max(0.0, 1.0 - distance / max_tolerant_distance)
                    score = 0.5 + 0.4 * distance_factor
                    candidates.append((formula_idx, score, "iou_tolerant"))

            if candidates:
                curve_candidates[curve_idx] = (curve, candidates)

        # Check each form against all formulas
        for form_idx, form in enumerate(page.pdf_form):
            if not form.box:
                continue

            candidates = []
            for formula_idx, (formula, paragraph_xobj_id) in enumerate(all_formulas):
                if not formula.box:
                    continue

                # Check xobj_id compatibility
                if paragraph_xobj_id is not None and form.xobj_id != paragraph_xobj_id:
                    continue

                # Level 1: Exact IoU matching (zero tolerance) - highest priority
                if self._is_element_contained_exact(form.box, formula.box):
                    iou = calculate_iou_for_boxes(form.box, formula.box)
                    candidates.append((formula_idx, iou, "iou_exact"))
                # Level 2: Tolerant IoU matching (with tolerance) - distance sorted
                elif is_element_contained_in_formula(form.box, formula.box):
                    distance = self._calculate_element_formula_distance(
                        form.box, formula.box
                    )
                    # Convert distance to score (closer = higher score)
                    # Score range: 0.5-0.9 to ensure lower than exact IoU
                    distance_factor = max(0.0, 1.0 - distance / max_tolerant_distance)
                    score = 0.5 + 0.4 * distance_factor
                    candidates.append((formula_idx, score, "iou_tolerant"))

            if candidates:
                form_candidates[form_idx] = (form, candidates)

        return all_formulas, curve_candidates, form_candidates

    def _resolve_assignment_conflicts(
        self, curve_candidates: dict, form_candidates: dict
    ) -> tuple[dict, list, list]:
        """Resolve assignment conflicts using prioritized matching strategy.

        Args:
            curve_candidates: dict mapping curve index to (curve, candidates) tuples
            form_candidates: dict mapping form index to (form, candidates) tuples
            where candidates is a list of (formula_index, score, match_type) tuples

        Returns:
            Tuple of (formula_assignments, curves_to_remove, forms_to_remove) where:
            - formula_assignments: dict mapping formula_index to (curves, forms) tuples
            - curves_to_remove: list of curves to remove from page level
            - forms_to_remove: list of forms to remove from page level
        """
        formula_assignments = {}
        curves_to_remove = []
        forms_to_remove = []

        def _get_best_candidate(candidates):
            """Get the best candidate using priority: Exact IoU > Tolerant IoU, then by score."""
            if not candidates:
                return None

            # Sort by match_type priority and then by score (descending)
            def sort_key(candidate):
                formula_idx, score, match_type = candidate
                # Exact IoU matches get priority 1, tolerant IoU matches get priority 2
                priority = 1 if match_type == "iou_exact" else 2
                # Return tuple for sorting: (priority, -score) for descending score within priority
                return (priority, -score)

            sorted_candidates = sorted(candidates, key=sort_key)
            return sorted_candidates[0]

        # Resolve curve assignments
        for _curve_idx, (curve, candidates) in curve_candidates.items():
            if not candidates:
                continue

            best_candidate = _get_best_candidate(candidates)
            if best_candidate:
                best_formula_idx, best_score, match_type = best_candidate

                # Add to assignments
                if best_formula_idx not in formula_assignments:
                    formula_assignments[best_formula_idx] = ([], [])
                formula_assignments[best_formula_idx][0].append(curve)
                curves_to_remove.append(curve)

        # Resolve form assignments
        for _form_idx, (form, candidates) in form_candidates.items():
            if not candidates:
                continue

            best_candidate = _get_best_candidate(candidates)
            if best_candidate:
                best_formula_idx, best_score, match_type = best_candidate

                # Add to assignments
                if best_formula_idx not in formula_assignments:
                    formula_assignments[best_formula_idx] = ([], [])
                formula_assignments[best_formula_idx][1].append(form)
                forms_to_remove.append(form)

        return formula_assignments, curves_to_remove, forms_to_remove

    def collect_contained_elements(self, page: Page):
        """Collect curves and forms that are contained within formulas.

        Uses two-phase assignment strategy to ensure each element is assigned
        to only one formula based on highest IoU value.
        """
        if not page.pdf_paragraph:
            return

        # Phase 1: Collect all potential element-formula assignments
        all_formulas, curve_candidates, form_candidates = (
            self._collect_element_formula_candidates(page)
        )

        # Phase 2: Resolve conflicts using IoU maximization
        formula_assignments, curves_to_remove, forms_to_remove = (
            self._resolve_assignment_conflicts(curve_candidates, form_candidates)
        )

        # Apply the resolved assignments using formula indices
        for formula_idx, (
            assigned_curves,
            assigned_forms,
        ) in formula_assignments.items():
            formula = all_formulas[formula_idx][0]  # Extract formula from tuple
            formula.pdf_curve.extend(assigned_curves)
            formula.pdf_form.extend(assigned_forms)

        # Remove assigned elements from page level
        for curve in curves_to_remove:
            if curve in page.pdf_curve:
                page.pdf_curve.remove(curve)

        for form in forms_to_remove:
            if form in page.pdf_form:
                page.pdf_form.remove(form)

    def process_page(self, page: Page):
        """处理页面，包括公式识别和偏移量计算"""
        self.process_page_formulas(page)
        # self.process_page_offsets(page)
        self.process_comma_formulas(page)
        self.merge_overlapping_formulas(page)
        # 清理公式中的孤立空格需在 process_page_offsets 之前执行：孤立空格（如
        # `2^16` 上标公式里混入的一个 y 位置异常、远离主字符行的空格，Page 407
        # 的 `16 ` box 被撑高到 20.3）会污染公式 box 高度，使 process_page_offsets
        # 用它算出的 y_offset 错误（如 `16` 上标被算成 -13.45，渲染时往下掉 13pt）。
        # 先清空格让 box 恢复真实高度，process_page_offsets 才能算出正确的 y_offset。
        self._remove_orphan_spaces(page)
        if not self.translation_config.skip_formula_offset_calculation:
            self.process_page_offsets(page)
        self.process_translatable_formulas(page)
        self.update_all_formula_data(page)
        if not self.translation_config.ocr_workaround:
            self.collect_contained_elements(page)

        if not self.translation_config.skip_formula_offset_calculation:
            self.process_page_offsets(page)
        self.update_all_formula_data(page)
        self.process_page_styles(page)
        # 垂直分式合并需在 process_page_styles 之后执行：此时 `=`/`(`/`)`/`.`/`%`
        # 等符号已被拆成 TEXT composition（此前为 EMPTY 占位），可并入合并公式。
        # 合并也放在 process_page_offsets 之后，以便 _merge_vertical_fractions 内
        # 设置的 y_offset（使分式垂直居中）不被 process_page_offsets 覆盖。
        # 这里再清一次孤立空格（幂等）：process_page_styles 之后若有新产生的孤立空格
        # 仍会在 _merge_vertical_fractions 前被移除，避免污染分式 box 高度、使
        # _is_vertical_fraction_pair 误判分子分母为同行。
        self._remove_orphan_spaces(page)
        self._merge_vertical_fractions(page)
        self.update_all_formula_data(page)

        # 处理剩余非公式线条：必须放在 _merge_vertical_fractions 之后。
        # 分式分数线（y≈581 的细长水平曲线）位于合并前分子/分母两个独立公式之间的
        # 空隙，collect_contained_elements 无法把它分配到任一公式，仍残留在
        # page.pdf_curve。若在此前执行 remove_non_formula_lines，会把它当作
        # "非公式线条"误删，导致译文中分式缺分数线。合并成一个公式后，分数线已
        # 被收进 merged.pdf_curve，这里再清理真正残留的非公式线条就不会误删。
        if self.translation_config.remove_non_formula_lines:
            self.remove_non_formula_lines_from_paragraphs(page)

    def update_line_data(self, line: PdfLine):
        min_x = min(char.visual_bbox.box.x for char in line.pdf_character)
        min_y = min(char.visual_bbox.box.y for char in line.pdf_character)
        max_x = max(char.visual_bbox.box.x2 for char in line.pdf_character)
        max_y = max(char.visual_bbox.box.y2 for char in line.pdf_character)
        line.box = Box(min_x, min_y, max_x, max_y)

    @staticmethod
    def _is_plain_inline_char(char: str) -> bool:
        if not char:
            return False
        return bool(_PLAIN_INLINE_CHAR_RE.match(char))

    def _classify_characters_in_composition(
        self,
        composition: PdfParagraphComposition,
        formula_font_ids: set[int],
        first_is_bullet_so_far: bool,
        line_index: int,
        prev_char: PdfCharacter | None = None,
    ) -> tuple[list[tuple[PdfCharacter, bool]], bool]:
        """
        Phase 1: Classify every character in a composition as either formula or text.
        This preserves the original logic, including the sticky `first_is_bullet` flag.
        """
        tagged_chars = []
        is_formula_tags = []

        line = composition.pdf_line
        if not line or not line.pdf_character:
            return [], first_is_bullet_so_far

        first_is_bullet = first_is_bullet_so_far
        in_formula_state = False
        in_corner_mark_state = False
        corner_mark_info = []

        # Determine the `is_formula` tag for each character
        for i, char in enumerate(line.pdf_character):
            # The original logic for `first_is_bullet`: it is set if any segment starts with a bullet.
            # A "segment" started when `current_chars` was empty.
            # We determine the start of a segment by looking at the previous char's tag.
            is_start_of_segment = i == 0 or (
                len(is_formula_tags) > 0 and is_formula_tags[-1] != in_formula_state
            )
            if not first_is_bullet and is_start_of_segment and is_bullet_point(char):
                first_is_bullet = True

            # line 首字符用上一 composition 的最后一个字符作参考（跨 composition 角标检测，
            # 如 "Adc" 中 A 与 dc 被 layout 拆成不同 composition，dc 的基线/字号差异需与 A 比较）
            is_cross_prev = i == 0 and prev_char is not None
            previous_char = (
                line.pdf_character[i - 1] if i > 0 else prev_char
            )
            next_char = (
                line.pdf_character[i + 1] if i < len(line.pdf_character) - 1 else None
            )
            isspace = char.char_unicode.isspace() if char.char_unicode else False
            prev_is_space = (
                previous_char.char_unicode.isspace()
                if previous_char and previous_char.char_unicode
                else False
            )

            is_formula_start = is_formulas_start_char(
                char.char_unicode,
                self.font_mapper,
                self.translation_config,
            )
            is_formula_middle = is_formulas_middle_char(
                char.char_unicode,
                self.font_mapper,
                self.translation_config,
            )

            # 上下标（角标）判定：
            # ① 字号明显更小（原逻辑，0.79/1.1 阈值区分角标与大写首字母）；
            # ② 同字号但基线 y 明显抬高/降低（如 m⁻¹ 中的 ⁻，字号比 0.79 临界漏判，
            #    用 char.box.y 与前一字符的基线偏移识别——同一行普通文本 box.y 完全相同）。
            # 跨 composition 的 previous_char（line 首字符）须与当前字符**真正同行**，
            # 否则上一行末尾字符会被误判为角标参考（如 B32i3 段，行间距约 14pt，
            # 上一行末字符 y 与本行首字符 y 差 13.9，若阈值过宽会误判为角标）。
            # 同行角标的 y 偏移（如 Adc 的 dc，2.5pt）远小于字符高度，跨行 y 差
            # （约行高）大于字符高度，故用字符高度作阈值可区分。
            _cross_guard = not is_cross_prev or (
                previous_char is not None
                and previous_char.box is not None
                and char.box is not None
                and abs(char.box.y - previous_char.box.y)
                < (char.box.y2 - char.box.y)
            )
            is_corner_mark = _cross_guard and (
                (
                    previous_char is not None
                    and not isspace
                    and not prev_is_space
                    and not first_is_bullet
                    # 角标字体，有 0.76 的角标和 0.799 的大写，这里用 0.79 取中，同时考虑首字母放大的情况
                    and char.pdf_style.font_size
                    < previous_char.pdf_style.font_size * 0.79
                    and not in_corner_mark_state
                )
                or (
                    previous_char is not None
                    and not isspace
                    and not prev_is_space
                    and char.pdf_style.font_size
                    < previous_char.pdf_style.font_size * 1.1
                    and in_corner_mark_state
                )
                or (
                    # 检查段落开始的角标：当没有前一个字符时，通过下一个字符判断
                    previous_char is None
                    and next_char is not None
                    and not isspace
                    and not prev_is_space
                    and not first_is_bullet
                    # 当前字符字体大小明显小于下一个字符，判定为角标
                    and char.pdf_style.font_size < next_char.pdf_style.font_size * 0.79
                    and not in_corner_mark_state
                )
                or (
                    # 基线 y 偏移：相对前一字符基线明显抬高（上标）或降低（下标）。
                    # 仅作为"角标起始"触发（not in_corner_mark_state），避免从角标回落到
                    # 正文基线的字符（如 m⁻¹ 后面的 K）被误判为下标。
                    # 注：此判定不受 first_is_bullet 抑制——bullet 列表项（如 "• Adc"）内
                    # 的 Adc/P1/P2 下标是真实上下标（y 偏移是强信号），不应因以 bullet 开头
                    # 而被误判为普通文本。字号角标起始判定仍受 first_is_bullet 抑制。
                    previous_char is not None
                    and previous_char.box is not None
                    and char.box is not None
                    and not isspace
                    and not prev_is_space
                    and not in_corner_mark_state
                    and (
                        char.box.y - previous_char.box.y > _CORNER_Y_RAISE_PT
                        or previous_char.box.y - char.box.y > _CORNER_Y_RAISE_PT
                    )
                )
            )

            # 普通文本字面符号（数字/[ ]/=×·−<>~±µ° 等）压制：
            # 若不是上下标、也不是公式对象（formula_layout_id），则当作普通文本，不生成 {vN}
            # 占位符。修复 X16+ / [35,36] / RW01[4] / PG70RW60[7]=0 / W/m·K / µm / °C 等
            # 被弱模型改写占位符后内容跑到段尾或丢失的问题。
            # µ/° 在 LaTeX PDF 里常落在数学字体(CMSY10/EURM10)，故此处对安全字符一并压制
            # 公式字体的触发。
            is_in_formula_font = char.pdf_style.font_id in formula_font_ids
            if self._is_plain_inline_char(char.char_unicode) and not is_corner_mark:
                is_formula_start = False
                is_formula_middle = False
                is_in_formula_font = False

            is_formula = (
                (  # 区分公式开头的字符&公式中间的字符。主要是逗号不能在公式开头，但是可以在中间。
                    char.formula_layout_id
                    or (is_formula_start and not in_formula_state)
                    or (is_formula_middle and in_formula_state)
                )  # 公式字符
                or is_in_formula_font  # 公式字体
                or char.vertical  # 垂直字体
                or (
                    #   如果是程序添加的 dummy 空格
                    char.char_unicode is None and in_formula_state
                )
                or (
                    # 如果字符的视觉框和实际框不一致，则认为是公式字符
                    # 下划线 _ 除外：其视觉框是字符框底部的细线（y 方向天然不一致，
                    # 如 ALERT_n 的下划线），并非公式特征，避免被误判为公式占位符
                    # 后 LLM 改写为 {n} 导致回填失败。
                    char.char_unicode != "_"
                    and (
                        char.box.x > char.visual_bbox.box.x2
                        or char.box.x2 < char.visual_bbox.box.x
                        or char.box.y > char.visual_bbox.box.y2
                        or char.box.y2 < char.visual_bbox.box.y
                    )
                )
            )

            is_formula = is_formula or is_corner_mark

            # bullet 点字符不应被当作公式处理。
            # bullet 字符（•、\uf09e 等）常在 Symbol/Wingdings 特殊字体中，
            # 会被上方的 "char.pdf_style.font_id in formula_font_ids" 误判为公式，
            # 替换成 {vN} 占位符后 LLM 翻译时可能丢弃，导致无序列表 bullet 丢失。
            # 注意：使用严格 bullet 模式，不排除上标/下标数字（¹²³₁₂₃ 等），
            # 因为后者在数学公式中常见。
            if _BULLET_FOR_FORMULA_EXCLUSION.match(char.char_unicode or ""):
                is_formula = False

            if char.char_unicode == " ":
                is_formula = in_formula_state

            # This simulates the state change for the next iteration
            if is_formula != in_formula_state:
                in_formula_state = is_formula

            in_corner_mark_state = is_corner_mark
            is_formula_tags.append(is_formula)
            corner_mark_info.append(is_corner_mark)

        for char, is_formula, is_corner_mark in zip(
            line.pdf_character, is_formula_tags, corner_mark_info, strict=False
        ):
            tagged_chars.append((char, is_formula, is_corner_mark))

        return tagged_chars, first_is_bullet

    def _group_classified_characters(
        self,
        tagged_chars: list[tuple[PdfCharacter, bool, bool]],
        line_index: int,
    ) -> list[PdfParagraphComposition]:
        """
        Phase 2: Group consecutive characters with the same tag into new compositions.
        """
        if not tagged_chars:
            return []

        new_compositions = []
        current_chars = []
        current_tag = tagged_chars[0][1]
        current_corner_mark_flags = []

        for char, is_formula_tag, is_corner_mark in tagged_chars:
            if is_formula_tag == current_tag:
                current_chars.append(char)
                current_corner_mark_flags.append(is_corner_mark)
            else:
                # Check if any character in current group is a corner mark
                has_corner_mark = any(current_corner_mark_flags)
                new_compositions.append(
                    self.create_composition(
                        current_chars, current_tag, line_index, has_corner_mark
                    ),
                )
                current_chars = [char]
                current_tag = is_formula_tag
                current_corner_mark_flags = [is_corner_mark]

        if current_chars:
            # Check if any character in final group is a corner mark
            has_corner_mark = any(current_corner_mark_flags)
            new_compositions.append(
                self.create_composition(
                    current_chars, current_tag, line_index, has_corner_mark
                ),
            )

        return new_compositions

    def process_page_formulas(self, page: Page):
        if not page.pdf_paragraph:
            return

        page_level_formula_font_ids, xobj_specific_formula_font_ids = (
            collect_page_formula_font_ids(
                page, self.translation_config.formular_font_pattern
            )
        )

        for paragraph in page.pdf_paragraph:
            if not paragraph.pdf_paragraph_composition:
                continue

            current_formula_font_ids: set[int]
            if (
                paragraph.xobj_id
                and paragraph.xobj_id in xobj_specific_formula_font_ids
            ):
                current_formula_font_ids = xobj_specific_formula_font_ids[
                    paragraph.xobj_id
                ]
            else:
                current_formula_font_ids = page_level_formula_font_ids

            new_paragraph_compositions = []
            # This flag is carried through all compositions in a paragraph, as in the original implementation.
            first_is_bullet = False
            # 跨 composition 的角标检测参考：上一 composition 的最后一个字符
            # （layout 模型可能把 "Adc" 拆成 A + dc 两个 composition，dc 需与 A 比较基线/字号）
            prev_line_last_char: PdfCharacter | None = None

            for line_index, composition in enumerate(
                paragraph.pdf_paragraph_composition
            ):
                (
                    tagged_chars,
                    first_is_bullet,
                ) = self._classify_characters_in_composition(
                    composition,
                    current_formula_font_ids,
                    first_is_bullet,
                    line_index,
                    prev_line_last_char,
                )
                _prev_line = composition.pdf_line
                if _prev_line and _prev_line.pdf_character:
                    prev_line_last_char = _prev_line.pdf_character[-1]

                if not tagged_chars:
                    new_paragraph_compositions.append(composition)
                    continue

                grouped_compositions = self._group_classified_characters(
                    tagged_chars, line_index
                )
                new_paragraph_compositions.extend(grouped_compositions)

            paragraph.pdf_paragraph_composition = new_paragraph_compositions

    def process_translatable_formulas(self, page: Page):
        """将需要正常翻译的公式（如纯数字、数字加逗号等）转换为普通文本行"""
        if not page.pdf_paragraph:
            return

        for paragraph in page.pdf_paragraph:
            if not paragraph.pdf_paragraph_composition:
                continue

            new_compositions = []
            for composition in paragraph.pdf_paragraph_composition:
                if (
                    composition.pdf_formula is not None
                    and not composition.pdf_formula.is_corner_mark
                    and self.is_translatable_formula(
                        composition.pdf_formula,
                    )
                ):
                    # 将可翻译公式转换为普通文本行
                    new_line = PdfLine(
                        pdf_character=composition.pdf_formula.pdf_character,
                    )
                    self.update_line_data(new_line)
                    new_compositions.append(PdfParagraphComposition(pdf_line=new_line))
                else:
                    new_compositions.append(composition)

            paragraph.pdf_paragraph_composition = new_compositions

    def process_page_styles(self, page: Page):
        """处理页面中的文本样式，识别相同样式的文本"""
        if not page.pdf_paragraph:
            return

        for paragraph in page.pdf_paragraph:
            if not paragraph.pdf_paragraph_composition:
                continue

            # 计算基准样式（除公式外所有文字样式的交集）
            base_style = self._calculate_base_style(paragraph)
            paragraph.pdf_style = base_style

            # 重新组织段落中的文本，将相同样式的文本组合在一起
            new_compositions = []
            current_chars = []
            current_style = None

            for comp in paragraph.pdf_paragraph_composition:
                if comp.pdf_formula is not None:
                    if current_chars:
                        new_comp = self._create_same_style_composition(
                            current_chars,
                            current_style,
                        )
                        new_compositions.append(new_comp)
                        current_chars = []
                    new_compositions.append(comp)
                    continue

                if not comp.pdf_line:
                    new_compositions.append(comp)
                    continue

                for char in comp.pdf_line.pdf_character:
                    char_style = char.pdf_style
                    if current_style is None:
                        current_style = char_style
                        current_chars.append(char)
                    elif is_same_style(char_style, current_style):
                        current_chars.append(char)
                    else:
                        if current_chars:
                            new_comp = self._create_same_style_composition(
                                current_chars,
                                current_style,
                            )
                            new_compositions.append(new_comp)
                        current_chars = [char]
                        current_style = char_style

            if current_chars:
                new_comp = self._create_same_style_composition(
                    current_chars,
                    current_style,
                )
                new_compositions.append(new_comp)

            paragraph.pdf_paragraph_composition = new_compositions

    def _calculate_base_style(self, paragraph) -> PdfStyle:
        """计算段落的基准样式（除公式外所有文字样式的交集）"""
        styles = []
        for comp in paragraph.pdf_paragraph_composition:
            if isinstance(comp, PdfFormula):
                continue
            if not comp.pdf_line:
                continue
            for char in comp.pdf_line.pdf_character:
                styles.append(char.pdf_style)

        if not styles:
            return None

        # 返回所有样式的交集
        base_style = styles[0]
        for style in styles[1:]:
            # 更新基准样式为所有样式的交集
            base_style = self._merge_styles(base_style, style)

        # 如果 font_id 或 font_size 为 None，则使用众数
        if base_style.font_id is None:
            base_style.font_id = self._get_mode_value([s.font_id for s in styles])
        if base_style.font_size is None:
            base_style.font_size = self._get_mode_value([s.font_size for s in styles])

        return base_style

    def _get_mode_value(self, values):
        """计算列表中的众数"""
        if not values:
            return None
        from collections import Counter

        counter = Counter(values)
        return counter.most_common(1)[0][0]

    def _merge_styles(self, style1, style2):
        """合并两个样式，返回它们的交集"""
        if style1 is None or style1.font_size is None:
            return style2
        if style2 is None or style2.font_size is None:
            return style1

        return PdfStyle(
            font_id=style1.font_id if style1.font_id == style2.font_id else None,
            font_size=(
                style1.font_size
                if math.fabs(style1.font_size - style2.font_size) < 0.02
                else None
            ),
            graphic_state=self._merge_graphic_states(
                style1.graphic_state,
                style2.graphic_state,
            ),
        )

    def _merge_graphic_states(self, state1, state2):
        """合并两个 GraphicState，返回它们的交集"""
        if state1 is None:
            return state2
        if state2 is None:
            return state1

        return GraphicState(
            passthrough_per_char_instruction=(
                state1.passthrough_per_char_instruction
                if state1.passthrough_per_char_instruction
                == state2.passthrough_per_char_instruction
                else None
            ),
        )

    def _create_same_style_composition(
        self,
        chars: list[PdfCharacter],
        style,
    ) -> PdfParagraphComposition:
        """创建具有相同样式的文本组合"""
        if not chars:
            return None

        # 计算边界框
        min_x = min(char.visual_bbox.box.x for char in chars)
        min_y = min(char.visual_bbox.box.y for char in chars)
        max_x = max(char.visual_bbox.box.x2 for char in chars)
        max_y = max(char.visual_bbox.box.y2 for char in chars)
        box = Box(min_x, min_y, max_x, max_y)

        return PdfParagraphComposition(
            pdf_same_style_characters=PdfSameStyleCharacters(
                box=box,
                pdf_style=style,
                pdf_character=chars,
            ),
        )

    @staticmethod
    def _compute_formula_char_box(formula: PdfFormula) -> Box:
        """用公式字符的 char.box（非 visual_bbox）计算参考框，用于上下标同行判断。

        formula.box 由 update_formula_data 用 visual_bbox（含字体 descent，随字号
        成正比）计算。大字号主字符（如 V）descent 偏移大、小字号下标（如 REF）偏移
        小，导致 formula.box 中上下标相对位置失真（下标被抬高）。这里改用 char.box
        （不含 descent）计算，保留真实的上下标相对位置，供 process_page_offsets
        兜底找回同行主字符、正确计算 y_offset。过滤规则与 update_formula_data 一致。
        """
        chars = [
            ch
            for ch in formula.pdf_character
            if not formular_height_ignore_char(ch)
        ]
        if not chars:
            chars = list(formula.pdf_character)
        return Box(
            min(ch.box.x for ch in chars),
            min(ch.box.y for ch in chars),
            max(ch.box.x2 for ch in chars),
            max(ch.box.y2 for ch in chars),
        )

    def process_page_offsets(self, page: Page):
        """计算公式的 x 和 y 偏移量"""
        if not page.pdf_paragraph:
            return

        for paragraph in page.pdf_paragraph:
            if paragraph.debug_id is None:
                continue
            if not paragraph.pdf_paragraph_composition:
                continue

            # 计算该段落的行间距，用其 80% 作为容差
            # line_spacing = self.calculate_line_spacing(paragraph)
            # y_tolerance = line_spacing * 0.8

            for i, composition in enumerate(paragraph.pdf_paragraph_composition):
                if not composition.pdf_formula:
                    continue

                formula = composition.pdf_formula

                def _find_adjacent_text(
                    ref_box,
                ) -> tuple[PdfCharacter | None, PdfCharacter | None]:
                    """查找公式左右最近的同一行文本字符（基于 y 重叠 IOU）。

                    上下标（如 VREF 的 REF、MR16 OP[6]=1B 的 B）字号小、垂直偏移大，
                    用 formula.box（基于 visual_bbox，含字体 descent）算 y 重叠时
                    IOU 常 < 0.6 匹配不到同行主字符，导致 y_offset=0、下标方向丢失、
                    渲染成上标。用公式字符本身的 char.box（不含 descent）作为参考框
                    再找一次即可命中（见 _compute_formula_char_box）。
                    """
                    left = None
                    right = None
                    left_iou = 0.0
                    right_iou = 0.0
                    # 查找左边最近的同一行的文本
                    for j in range(i - 1, -1, -1):
                        comp = paragraph.pdf_paragraph_composition[j]
                        if comp.pdf_line:
                            for char in reversed(comp.pdf_line.pdf_character):
                                if not char.pdf_character_id:
                                    continue
                                # 检查 y 坐标是否接近，判断是否在同一行
                                left_iou = calculate_y_true_iou_for_boxes(
                                    ref_box, char.box
                                )
                                if left_iou > 0.6:
                                    left = char
                                    break
                        break
                    # 查找右边最近的同一行的文本
                    for j in range(i + 1, len(paragraph.pdf_paragraph_composition)):
                        comp = paragraph.pdf_paragraph_composition[j]
                        if comp.pdf_line:
                            for char in comp.pdf_line.pdf_character:
                                if not char.pdf_character_id:
                                    continue
                                # 检查 y 坐标是否接近，判断是否在同一行
                                right_iou = calculate_y_true_iou_for_boxes(
                                    ref_box, char.box
                                )
                                if right_iou > 0.6:
                                    right = char
                                    break
                        break
                    # If both text segments exist, keep the one with higher IOU
                    if left and right:
                        if left_iou < right_iou:
                            left = None
                        elif right_iou < left_iou:
                            right = None
                    return left, right

                left_char, right_char = _find_adjacent_text(formula.box)
                if left_char is None and right_char is None:
                    # 兜底：用公式字符的 char.box（不含 descent）参考框再找一次，
                    # 修复上下标（小字号）因 visual_bbox 偏移导致 IOU 不足、方向丢失。
                    left_char, right_char = _find_adjacent_text(
                        self._compute_formula_char_box(formula)
                    )

                # 计算 x 偏移量（相对于左边文本）
                if left_char:
                    formula.x_offset = formula.box.x - left_char.box.x2
                else:
                    formula.x_offset = 0  # 如果左边没有文字，x_offset 应该为 0
                if abs(formula.x_offset) < 0.1:
                    formula.x_offset = 0
                if formula.x_offset > 10:
                    formula.x_offset = 0
                # if formula.x_offset > 0:
                #     formula.x_offset = 0
                if formula.x_offset < -5:
                    formula.x_offset = 0

                # 计算 y 偏移量
                if left_char:
                    # 使用底部坐标计算偏移量
                    formula.y_offset = formula.box.y - left_char.box.y
                elif right_char:
                    formula.y_offset = formula.box.y - right_char.box.y
                else:
                    formula.y_offset = 0

                if abs(formula.y_offset) < 0.1:
                    formula.y_offset = 0

                if max(abs(formula.y_offset), abs(formula.x_offset)) > 10:
                    pass
                    # logging.debug(
                    #     f"公式 {formula.box} 的偏移量过大：{formula.x_offset}, {formula.y_offset}"
                    # )

    def calculate_line_spacing(self, paragraph) -> float:
        """计算段落中的平均行间距"""
        if not paragraph.pdf_paragraph_composition:
            return 0.0

        # 收集所有文本行的 y 坐标
        line_y_positions = []
        for comp in paragraph.pdf_paragraph_composition:
            if comp.pdf_line:
                line_y_positions.append(comp.pdf_line.box.y)

        if len(line_y_positions) < 2:
            return 10.0  # 如果只有一行或没有行，返回一个默认值

        # 计算相邻行之间的 y 差值
        line_spacings = []
        for i in range(len(line_y_positions) - 1):
            spacing = abs(line_y_positions[i] - line_y_positions[i + 1])
            if spacing > 0:  # 忽略重叠的行
                line_spacings.append(spacing)

        if not line_spacings:
            return 10.0  # 如果没有有效的行间距，返回默认值

        # 使用中位数来避免异常值的影响
        median_spacing = sorted(line_spacings)[len(line_spacings) // 2]
        return median_spacing

    def create_composition(
        self,
        chars: list[PdfCharacter],
        is_formula: bool,
        line_index: int,
        is_corner_mark: bool = False,
    ) -> PdfParagraphComposition:
        if is_formula:
            formula = PdfFormula(pdf_character=chars, line_id=line_index)
            formula.is_corner_mark = is_corner_mark
            self.update_formula_data(formula)
            return PdfParagraphComposition(pdf_formula=formula)
        else:
            new_line = PdfLine(pdf_character=chars)
            self.update_line_data(new_line)
            return PdfParagraphComposition(pdf_line=new_line)

    def is_translatable_formula(self, formula: PdfFormula) -> bool:
        """判断公式是否只包含需要正常翻译的字符（数字、空格和英文逗号）"""
        if all(char.formula_layout_id for char in formula.pdf_character):
            return False

        text = "".join(char.char_unicode for char in formula.pdf_character)
        if formula.y_offset > 0.1:
            return False
        return bool(re.match(r"^[0-9, .]+$", text))

    def should_split_formula(self, formula: PdfFormula) -> bool:
        """判断公式是否需要按逗号拆分（包含逗号且有其他特殊符号）"""

        if all(x.formula_layout_id for x in formula.pdf_character):
            return False

        text = "".join(char.char_unicode for char in formula.pdf_character)
        # 必须包含逗号
        if "," not in text:
            return False
        # 检查是否包含除了数字和 [] 之外的其他符号
        text_without_basic = re.sub(r"[0-9\[\],\s]", "", text)
        return bool(text_without_basic)

    def split_formula_by_comma(
        self,
        formula: PdfFormula,
    ) -> list[tuple[list[PdfCharacter], PdfCharacter]]:
        """按逗号拆分公式字符，返回 (字符组，逗号字符) 的列表，最后一组的逗号字符为 None。
        只有不在括号内的逗号才会被用作分隔符。支持的括号对包括：
        - (cid:8) 和 (cid:9)
        - ( 和 )
        - (cid:16) 和 (cid:17)
        """
        result = []
        current_chars = []
        bracket_level = 0  # 跟踪括号的层数

        for char in formula.pdf_character:
            # 检查是否是左括号
            if char.char_unicode in LEFT_BRACKET:
                bracket_level += 1
                current_chars.append(char)
            # 检查是否是右括号
            elif char.char_unicode in RIGHT_BRACKET:
                bracket_level = max(0, bracket_level - 1)  # 防止括号不匹配的情况
                current_chars.append(char)
            # 检查是否是逗号，且不在括号内
            elif char.char_unicode == "," and bracket_level == 0:
                if current_chars:
                    result.append((current_chars, char))
                    current_chars = []
            else:
                current_chars.append(char)

        if current_chars:
            result.append((current_chars, None))  # 最后一组没有逗号

        return result

    def merge_formulas(self, formula1: PdfFormula, formula2: PdfFormula) -> PdfFormula:
        """合并两个公式，保持字符的相对位置"""
        # 合并所有字符
        all_chars = formula1.pdf_character + formula2.pdf_character
        # 按 y 坐标和 x 坐标排序，确保字符顺序正确
        # sorted_chars = sorted(
        #     all_chars, key=lambda c: (c.visual_bbox.box.y, c.visual_bbox.box.x))

        # 继承第一个公式的行 ID
        merged_formula = PdfFormula(pdf_character=all_chars, line_id=formula1.line_id)
        self.update_formula_data(merged_formula)
        return merged_formula

    def _remove_orphan_spaces(self, page: Page):
        """移除公式中的孤立空格，避免污染公式 box 高度。

        分式（分子/分母）内部偶有一个 y 位置异常的空格（如 Page 408 分子下方
        y=370.7 的空格，与主字符中心差约 16pt），它把公式 box 高度撑大（21.9），
        使 _is_vertical_fraction_pair 误判分子分母为"同行"而放弃合并。

        规则：对每个公式，取所有非空格字符的 y 中心（主字符行），移除那些
        y 中心与主字符行差 > 阈值（默认 6pt）的空格字符。行内正常空格
        （y 与主字符一致）保留。
        """
        if not page.pdf_paragraph:
            return
        for paragraph in page.pdf_paragraph:
            for comp in paragraph.pdf_paragraph_composition:
                formula = comp.pdf_formula
                if formula is None or formula.pdf_character is None:
                    continue
                chars = formula.pdf_character
                # 收集非空格字符的 y 中心
                non_space_cy = []
                for ch in chars:
                    u = ch.char_unicode
                    if u is not None and u.isspace():
                        continue
                    b = ch.visual_bbox.box if ch.visual_bbox else ch.box
                    if b is None:
                        continue
                    non_space_cy.append((b.y + b.y2) / 2)
                if not non_space_cy:
                    continue
                main_cy = sum(non_space_cy) / len(non_space_cy)
                removed = 0
                kept = []
                for ch in chars:
                    u = ch.char_unicode
                    if u is not None and u.isspace():
                        b = ch.visual_bbox.box if ch.visual_bbox else ch.box
                        cy = (b.y + b.y2) / 2 if b else main_cy
                        if abs(cy - main_cy) > 6.0:
                            removed += 1
                            continue  # 孤立空格：移除
                    kept.append(ch)
                if removed:
                    formula.pdf_character = kept
                    if kept:
                        self.update_formula_data(formula)

    def _is_fraction_symbol_text(self, comp: PdfParagraphComposition) -> bool:
        """判断 composition 是否为纯分式符号文本（`(`/`)`/`.`/`=` 等），可并入分式公式。

        仅当所有非空白字符都是分式符号白名单成员、且无公式时返回 True。
        空白（分子/分母间的空格）跳过，不影响判定。
        """
        if comp.pdf_formula is not None:
            return False
        ssc = comp.pdf_same_style_characters
        if not ssc or not ssc.pdf_character:
            return False
        has_symbol = False
        for ch in ssc.pdf_character:
            u = ch.char_unicode
            if u is None:
                continue
            if u.isspace():
                continue
            if not _FRACTION_SYMBOL_RE.match(u):
                return False
            has_symbol = True
        return has_symbol

    def _is_vertical_fraction_pair(
        self, formula1: PdfFormula, formula2: PdfFormula
    ) -> bool:
        """判断两个公式是否为垂直分式：x 轴重叠、y 轴垂直相邻（一个在上一个在下）。

        分式分子/分母（如 WCK(1.66m66)/WCK200ns）垂直排列：x 中心对齐、y 相距约一行。
        同行公式中心 y 差 ≈ 0，不满足"垂直相邻"；相隔多行的公式中心 y 差过大，也不满足。
        """
        b1, b2 = formula1.box, formula2.box
        if b1 is None or b2 is None:
            return False
        # x 轴有重叠（分子/分母中心大致对齐）
        if b1.x2 < b2.x - 1 or b2.x2 < b1.x - 1:
            return False
        # 中心 y 差：垂直相邻（约一行高），而非同行（≈0）或相隔过远
        cy1 = (b1.y + b1.y2) / 2
        cy2 = (b2.y + b2.y2) / 2
        y_diff = abs(cy1 - cy2)
        avg_h = (b1.y2 - b1.y + b2.y2 - b2.y) / 2
        if avg_h <= 0:
            return False
        return 0.5 * avg_h <= y_diff <= 2.5 * avg_h

    def _merge_vertical_fractions(self, page: Page):
        """把垂直排列的分式（分子/分母公式 + 中间的符号文本）合并成一个 PdfFormula。

        背景：分式分子/分母（数学字体）被普通字体的 `=`/`(`/`)`/`.` 拆成多个独立公式，
        relocate 时各公式水平排列导致塌陷。这里把"垂直相邻的分子公式 + 符号文本 + 分母公式"
        的所有字符合并成一个公式，relocate 会用各字符 rel_y 保留垂直位置，分式不塌陷。
        """
        if not page.pdf_paragraph:
            return
        for paragraph in page.pdf_paragraph:
            comps = paragraph.pdf_paragraph_composition
            if not comps:
                continue
            i = 0
            while i < len(comps):
                comp1 = comps[i]
                if comp1.pdf_formula is None:
                    i += 1
                    continue
                formula1 = comp1.pdf_formula
                # 向后找垂直相邻的分母公式，中间只允许公式/分式符号文本。
                # 方案B（Page 408）：composition 顺序并非阅读顺序（行内 x 排序被禁用），
                # 分式分子常被排在文本前、分母排在文本后（如 `tWCKosc(T) : ... = `
                # 位于 x=112，分子 x=250、分母 x=252）。若中间夹着"与分子无 x 重叠"
                # 的普通文本（几何上在分式侧面，非分子/分母所在列），应跳过它继续找
                # 分母，而不是直接 BREAK。分子与分母的 x 重叠 + 垂直相邻由
                # _is_vertical_fraction_pair 把关，不会误并无关公式。
                j = i + 1
                end = None
                skipped_text = []  # 被跳过的侧面普通文本索引（合并时须保留，不并入分式）
                while j < len(comps):
                    compj = comps[j]
                    if compj.pdf_formula is not None:
                        _is_pair = self._is_vertical_fraction_pair(
                            formula1, compj.pdf_formula
                        )
                        if _is_pair:
                            end = j
                            break
                    elif not self._is_fraction_symbol_text(compj):
                        # 空占位 composition（符号字符尚未被 styles 拆出）跳过；
                        # 有实际文本字符的普通文本则视为分式结束
                        ssc = compj.pdf_same_style_characters
                        if not (ssc and ssc.pdf_character):
                            j += 1
                            continue
                        # 方案B：若该普通文本与分子 box 无 x 重叠（在分式侧面，如
                        # `tWCKosc(T)` 位于分子左侧），跳过它继续找分母；记录其索引
                        # 以便合并时保留（不并入分式）。若与分子 x 重叠（同列/相邻），
                        # 视为真正阻断分式的文本，BREAK 结束扫描。
                        _ssc_box = ssc.box
                        _skip = (
                            formula1.box is not None
                            and _ssc_box is not None
                            and not self.is_x_axis_contained(formula1.box, _ssc_box)
                            and not self.is_x_axis_adjacent(_ssc_box, formula1.box, 0.0)
                        )
                        if _skip:
                            skipped_text.append(j)
                            j += 1
                            continue
                        # 遇到普通文本，不是分式
                        break
                    j += 1
                if end is not None:
                    # 合并分子(comp[i])与分母(comp[end]) 的公式字符 + 与分子同一 x 列
                    # 的中间分式符号文本（如 `=`/`(`/`)`），并保留被合并公式的曲线
                    # （分数线）和 form（collect_contained_elements 已把分数线 curve
                    # 关联到分子/分母公式的 pdf_curve，必须一并保留）。
                    # 方案B：被跳过的侧面普通文本（skipped_text）不并入分式、保留原样。
                    # 方案B-2：中间的**非分子非分母**成分（无论公式还是文本符号）只有
                    #   与分子同一 x 列（x 重叠或紧邻）才并入分式。否则会误并入侧面
                    #   文本的公式（如 Page 408 的 `MWCW OMM`(comp6)/`T`(comp8)/`:`），
                    #   使合并 box 向左延伸、把 `tWCKosc(T) :` 吞进公式导致错位。
                    _mb = formula1.box
                    # 分式列 = 分子与分母 x 的并集范围（407 的 `= value` 常在分子
                    # 右侧、仍属分式；408 的侧面文本 `MWCW OMM` 在分式列远处）。
                    _col = None
                    if _mb is not None:
                        _db = (
                            comps[end].pdf_formula.box
                            if comps[end].pdf_formula is not None
                            else None
                        )
                        if _db is not None:
                            _col = Box(
                                x=min(_mb.x, _db.x),
                                y=min(_mb.y, _db.y),
                                x2=max(_mb.x2, _db.x2),
                                y2=max(_mb.y2, _db.y2),
                            )
                        else:
                            _col = _mb
                    all_chars = []
                    all_curves = []
                    all_forms = []
                    # 记录真正并入分式的中间索引（合并后这些 comp 会被删除，
                    # 但被跳过的侧面文本/未被并入的中间成分须保留）。
                    merged_side = set()
                    for k in range(i, end + 1):
                        if k in skipped_text:
                            continue
                        compk = comps[k]
                        if k != i and k != end:
                            # 中间成分：仅在"分子∪分母"的分式列内才并入分式
                            _cb = (
                                compk.pdf_formula.box
                                if compk.pdf_formula is not None
                                else (
                                    compk.pdf_same_style_characters.box
                                    if compk.pdf_same_style_characters is not None
                                    else None
                                )
                            )
                            if (
                                _col is None
                                or _cb is None
                                or (
                                    not self.is_x_axis_contained(_cb, _col)
                                    and not self.is_x_axis_adjacent(_cb, _col, 0.0)
                                )
                            ):
                                continue
                            merged_side.add(k)
                        if compk.pdf_formula is not None:
                            all_chars.extend(compk.pdf_formula.pdf_character)
                            all_curves.extend(compk.pdf_formula.pdf_curve)
                            all_forms.extend(compk.pdf_formula.pdf_form)
                        elif compk.pdf_same_style_characters:
                            all_chars.extend(
                                compk.pdf_same_style_characters.pdf_character
                            )
                    if all_chars:
                        merged = PdfFormula(
                            pdf_character=all_chars, line_id=formula1.line_id
                        )
                        merged.pdf_curve = all_curves
                        merged.pdf_form = all_forms
                        self.update_formula_data(merged)
                        # 收集 page.pdf_curve 中被合并分式 box 包含的曲线（分数线）。
                        # collect_contained_elements 在合并前运行时，分子/分母还是独立公式，
                        # 分数线曲线位于两者之间的空隙、与任一公式的 IoU 都为 0，因而未被
                        # 分配到任何公式，仍残留在 page.pdf_curve。合并成一个公式后，把被
                        # merged.box 包含的曲线收进 merged.pdf_curve，使分数线随分式一起
                        # relocate（含 y_offset 垂直居中），避免与分子分母错位。
                        # 注意不能用 is_element_contained_in_formula（按 IoU 判断）：分数线
                        # 曲线面积远小于公式面积，IoU 极低永远不达标。这里用带容差的
                        # 完全包含判断（曲线 box 整体落在公式 box 内）。
                        if page.pdf_curve and merged.box is not None:
                            _tol = 2.0
                            _mb = merged.box
                            for crv in list(page.pdf_curve):
                                if not crv.box:
                                    continue
                                _cb = crv.box
                                # 分数线：y 与分式重叠（位于分子/分母之间）、x 与分式
                                # 大致对齐。注意分数线常比合并后的分式 box 略宽（如
                                # Page 407 分数线 x2=430.3，而合并 box x2=421.4），
                                # 因此不能要求完全包含；只要 y 重叠 + x 有交集即可，
                                # 收集后扩展 merged.box 以包含该分数线。
                                if not (
                                    _cb.y >= _mb.y - _tol
                                    and _cb.y2 <= _mb.y2 + _tol
                                    and _cb.x <= _mb.x2 + _tol
                                    and _cb.x2 >= _mb.x - _tol
                                ):
                                    continue
                                merged.pdf_curve.append(crv)
                                page.pdf_curve.remove(crv)
                                # 扩展 merged.box 以包含分数线
                                if _cb.x < _mb.x or _cb.x2 > _mb.x2:
                                    _mb = Box(
                                        x=min(_mb.x, _cb.x),
                                        y=_mb.y,
                                        x2=max(_mb.x2, _cb.x2),
                                        y2=_mb.y2,
                                    )
                                    merged.box = _mb
                        # 分式所在行是否含"可翻译字母文本"（如 Page 408 的 `tWCKosc(T)`）：
                        # 若含，分式不能做等号左右合并（避免吞可翻译文本），且 y_offset 应
                        # 让分式中心与同行文本中心对齐（而非 -半高 对齐 current_y，否则
                        # 分式相对文本偏下）；Page 407 分式行无字母文本，用 -半高。
                        _row_has_letter = False
                        _row_text_box = None
                        if merged.box is not None:
                            # 选"中心 y 最接近分式中心、且含实际字母文本"的对齐基准
                            # （如 Page 408 的 `tWCKosc(T)`），避免误选中 `_`/bullet `?  `
                            # 等边缘字符导致分式 y_offset 算错、与文本错位。
                            _frac_cy = (merged.box.y + merged.box.y2) / 2
                            _best_dist = None
                            for _ck in comps:
                                _ssc = _ck.pdf_same_style_characters
                                if _ssc is None:
                                    continue
                                if self._is_fraction_symbol_text(_ck):
                                    continue
                                # 排除以空格/bullet 为主的文本（如 `?  `），只选
                                # 含真实字母的文本作为垂直对齐基准。
                                _letters = sum(
                                    1
                                    for _ch in _ssc.pdf_character or []
                                    if (_ch.char_unicode or "").isalnum()
                                )
                                if not _letters:
                                    continue
                                if _ssc.box is None or not self.has_y_intersection(
                                    _ssc.box, merged.box
                                ):
                                    continue
                                _tc = (_ssc.box.y + _ssc.box.y2) / 2
                                _d = abs(_tc - _frac_cy)
                                if _best_dist is None or _d < _best_dist:
                                    _best_dist = _d
                                    _row_has_letter = True
                                    _row_text_box = _ssc.box
                        if merged.box is None:
                            merged.y_offset = 0
                        elif _row_has_letter and _row_text_box is not None:
                            # 让分式中心 = 同行文本中心：
                            # current_y + 分式半高 + yoff = current_y + 文本半高
                            # → yoff = 文本半高 - 分式半高 = (h_text - h_frac)/2
                            _h_frac = merged.box.y2 - merged.box.y
                            _h_text = _row_text_box.y2 - _row_text_box.y
                            merged.y_offset = (_h_text - _h_frac) / 2
                        else:
                            # 让合并分式垂直居中：relocate 用 formula.box.y（分母位置）
                            # 作为 rel_y 基准，导致分子 rel_y 大、被推到文本行上方。
                            # 把 box 中心对齐锚点（y_offset = -半高），使分子/分母对称居中。
                            merged.y_offset = -(merged.box.y2 - merged.box.y) / 2
                        # 用合并公式替换 comp[i]；删除分母(comp[end]) + 真正并入分式的
                        # 中间成分(merged_side)。保留被跳过的侧面普通文本（skipped_text，
                        # 如 `tWCKosc(T)`），也保留未被并入的侧面公式（如 `MWCW OMM`/`T`），
                        # 否则会把 `tWCKosc(T) : MWCW OMM(T)` 误删导致文本错位。
                        comps[i] = PdfParagraphComposition(pdf_formula=merged)
                        _to_remove = {end} | merged_side
                        for _k in sorted(_to_remove, reverse=True):
                            del comps[_k]
                        # 方案B-3：合并分式后按 x 坐标重排到正确位置。
                        # composition 顺序并非阅读顺序（行内 x 排序被禁用），分式分子
                        # 常被排在最前、分母在最后、中间隔文本。合并后分式继承了分子的
                        # 最前位置，导致译文里分式跑到行首（如 Page 408 的 `tWCKosc(T)
                        # : ... = [Run Time/2*Count]`，分式 x=250 却排第 1）。
                        # 这里把合并分式插到"最后一个 x2 <= 分式.x 的 comp"之后，
                        # 恢复阅读顺序（文本在左、分式在右）。对 Page 407（分式已在
                        # 正确位置）无变化。
                        if merged.box is not None:
                            _frac_comp = PdfParagraphComposition(pdf_formula=merged)
                            del comps[i]
                            _insert = -1
                            _fx = merged.box.x
                            for _k, _ck in enumerate(comps):
                                _cb = (
                                    _ck.pdf_formula.box
                                    if _ck.pdf_formula is not None
                                    else (
                                        _ck.pdf_same_style_characters.box
                                        if _ck.pdf_same_style_characters is not None
                                        else None
                                    )
                                )
                                if _cb is not None and _cb.x2 <= _fx + 0.5:
                                    _insert = _k
                            comps.insert(_insert + 1, _frac_comp)
                            i = _insert + 1
                        # 方案B-4：分式所在行若含"可翻译字母文本"（如 Page 408 的
                        # `tWCKosc(T)`，前面计算 _row_has_letter），不做等号左右合并——
                        # 否则会把 `MWCW OMM(T)` 等可翻译内容吞进分式公式、丢失翻译。
                        # Page 407 的分式行只有公式 + 纯分式符号文本（无字母），照常合并。
                        # 合并同行等号前后的公式+符号成一个整体（像分式一样），统一 y_offset。
                        # 等号前后行的内容由公式（comp0/4/6）和普通文本符号（`=`/`.`/`%`）
                        # 混排而成：分式合并后分式 box 中心对齐 current_y（y_offset=-半高），
                        # 但等号前后的公式和文本符号各自为独立 composition，文本符号没有
                        # y_offset、不跟随公式下移，导致它们相对分式偏上、互相错位。
                        # 这里把分式左右两侧同行（y 区间与分式重叠）的连续"公式 + 文本符号"
                        # 分别合并成一个 PdfFormula，并统一 y_offset=-(height/2)，
                        # 使整行内容的 box 中心都对齐 current_y，一起下移。
                        if merged.box is not None and not _row_has_letter:
                            try:
                                for _side, _start, _step in (
                                    ("left", i - 1, -1),
                                    ("right", i + 1, 1),
                                ):
                                    _chars = []
                                    _k = _start
                                    _first = _k
                                    while 0 <= _k < len(comps):
                                        _ck = comps[_k]
                                        _box = None
                                        _chs = []
                                        if _ck.pdf_formula is not None:
                                            _box = _ck.pdf_formula.box
                                            _chs = list(
                                                _ck.pdf_formula.pdf_character
                                            )
                                        elif _ck.pdf_same_style_characters:
                                            _box = _ck.pdf_same_style_characters.box
                                            # 方案B 防护：分式侧面的**含字母普通文本**
                                            # （如 `tWCKosc(T)` 位于分式左侧 x=112）不应被
                                            # 并入分式公式，仅当其与分式 x 轴重叠或紧邻
                                            # （同一分式行的等号/数值）时才并入。
                                            # 纯分式符号文本（`=`/`(`/`)`/`.`/`%`，Page 407
                                            # 的等号前后）**始终并入**：它们就是分式等号，
                                            # 且必须与分式一起设 y_offset=-半高 才能整体
                                            # 垂直居中（否则分式与等号前后内容错位偏上）。
                                            if not self._is_fraction_symbol_text(_ck) and (
                                                _box is None
                                                or (
                                                    not self.is_x_axis_contained(
                                                        _box, merged.box
                                                    )
                                                    and not self.is_x_axis_adjacent(
                                                        _box, merged.box, 3.0
                                                    )
                                                )
                                            ):
                                                break
                                            _chs = list(
                                                _ck.pdf_same_style_characters.pdf_character
                                            )
                                        else:
                                            break
                                        if (
                                            _box is None
                                            or not self.has_y_intersection(
                                                _box, merged.box
                                            )
                                        ):
                                            break  # 不同行，停止
                                        _chars.extend(_chs)
                                        _k += _step
                                    if _chars:
                                        _sm = PdfFormula(
                                            pdf_character=_chars,
                                            line_id=merged.line_id,
                                        )
                                        self.update_formula_data(_sm)
                                        _sm.y_offset = (
                                            -(_sm.box.y2 - _sm.box.y) / 2
                                            if _sm.box is not None
                                            else 0
                                        )
                                        _new_comp = PdfParagraphComposition(
                                            pdf_formula=_sm
                                        )
                                        if _side == "left":
                                            # 替换 _first..i-1 为单个合并公式
                                            del comps[_first:i]
                                            comps.insert(_first, _new_comp)
                                            # 分式索引左移
                                            i = _first + 1
                                        else:
                                            # 替换 i+1.._k-1 为单个合并公式
                                            del comps[i + 1 : _k]
                                            comps.insert(i + 1, _new_comp)
                            except Exception:
                                pass
                        # 继续从 i 检查是否还有可合并的分式
                        continue
                i += 1

    def is_x_axis_contained(self, box1: Box, box2: Box) -> bool:
        """判断 box1 的 x 轴是否完全包含在 box2 的 x 轴内，或反之"""
        return (box1.x >= box2.x and box1.x2 <= box2.x2) or (
            box2.x >= box1.x and box2.x2 <= box1.x2
        )

    def has_y_intersection(self, box1: Box, box2: Box) -> bool:
        """判断两个 box 的 y 轴是否有交集"""
        tolerance = 1.0
        return not (box1.y2 < box2.y - tolerance or box2.y2 < box1.y - tolerance)

    def is_x_axis_adjacent(self, box1: Box, box2: Box, tolerance: float = 2.0) -> bool:
        """判断两个 box 在 x 轴上是否相邻或有交集"""
        # 检查是否有交集
        has_intersection = not (box1.x2 < box2.x or box2.x2 < box1.x)

        # 检查 box1 是否在 box2 左边且相邻
        left_adjacent = abs(box1.x2 - box2.x) <= tolerance
        # 检查 box2 是否在 box1 左边且相邻
        right_adjacent = abs(box2.x2 - box1.x) <= tolerance

        return has_intersection or left_adjacent or right_adjacent

    def calculate_y_iou(self, box1: Box, box2: Box) -> float:
        """计算两个 box 在 y 轴上的 IOU (Intersection over Union)"""
        # 计算交集
        intersection_start = max(box1.y, box2.y)
        intersection_end = min(box1.y2, box2.y2)
        intersection_length = max(0, intersection_end - intersection_start)

        # 计算并集
        box1_height = box1.y2 - box1.y
        box2_height = box2.y2 - box2.y
        union_length = box1_height + box2_height - intersection_length

        # 避免除零错误
        if union_length <= 0:
            return 0.0

        return intersection_length / union_length

    def merge_overlapping_formulas(self, page: Page):
        """
        合并符合以下条件的公式：
        1. x 轴重叠且 y 轴有交集的相邻公式，或者
        2. x 轴相邻且 y 轴 IOU > 0.5 的相邻公式，或者
        3. 所有字符的 layout id 都相同的相邻公式，或者
        4. 任意两个公式的 IOU > 0.8
        角标可能会被识别成单独的公式，需要合并
        """
        if not page.pdf_paragraph:
            return

        for paragraph in page.pdf_paragraph:
            if not paragraph.pdf_paragraph_composition:
                continue

            # 重复执行合并过程，直到没有更多可以合并的公式
            merged = True
            while merged:
                merged = False
                for i in range(len(paragraph.pdf_paragraph_composition)):
                    if merged:
                        break
                    comp1 = paragraph.pdf_paragraph_composition[i]
                    if comp1.pdf_formula is None:
                        continue

                    for j in range(i + 1, len(paragraph.pdf_paragraph_composition)):
                        comp2 = paragraph.pdf_paragraph_composition[j]
                        if comp2.pdf_formula is None:
                            continue

                        formula1 = comp1.pdf_formula
                        formula2 = comp2.pdf_formula

                        # 检查合并条件：
                        # 0. 必须在同一行（line_id 相同），以及
                        # 1. x 轴重叠且 y 轴有交集，或者
                        # 2. x 轴相邻且 y 轴 IOU > 0.5，或者
                        # 3. 所有字符的 layout id 都相同，或者
                        # 4. 任意两个公式的 IOU > 0.8

                        # 检查是否在同一行
                        same_line = formula1.line_id == formula2.line_id

                        should_merge = same_line and (
                            (
                                j == i + 1
                                and (
                                    (
                                        self.is_x_axis_contained(
                                            formula1.box, formula2.box
                                        )
                                        and self.has_y_intersection(
                                            formula1.box, formula2.box
                                        )
                                    )
                                    or (
                                        self.is_x_axis_adjacent(
                                            formula1.box, formula2.box
                                        )
                                        and self.calculate_y_iou(
                                            formula1.box, formula2.box
                                        )
                                        > 0.5
                                    )
                                )
                            )
                            or (self._have_same_layout_ids(formula1, formula2, page))
                            or (
                                calculate_iou_for_boxes(formula1.box, formula2.box)
                                > 0.8
                            )
                            or (
                                calculate_iou_for_boxes(formula2.box, formula1.box)
                                > 0.8
                            )
                        )

                        if should_merge:
                            # 合并公式
                            merged_formula = self.merge_formulas(formula1, formula2)
                            paragraph.pdf_paragraph_composition[i] = (
                                PdfParagraphComposition(
                                    pdf_formula=merged_formula,
                                )
                            )
                            # 删除第二个公式
                            del paragraph.pdf_paragraph_composition[j]
                            merged = True
                            break

    def _have_same_layout_ids(
        self, formula1: PdfFormula, formula2: PdfFormula, page: Page
    ) -> bool:
        """检查两个公式的所有字符是否具有相同的 layout id"""
        # 获取 formula1 中所有字符的 layout id
        formula1_layout_ids = set()
        for char in formula1.pdf_character:
            if char.char_unicode == " ":
                continue
            layout = char.formula_layout_id
            if layout:
                formula1_layout_ids.add(layout)

        # 获取 formula2 中所有字符的 layout id
        formula2_layout_ids = set()
        for char in formula2.pdf_character:
            if char.char_unicode == " ":
                continue
            layout = char.formula_layout_id
            if layout:
                formula2_layout_ids.add(layout)

        # 如果任一公式没有有效的 layout id，则不合并
        if not (len(formula1_layout_ids) == len(formula2_layout_ids) == 1):
            return False

        # 检查两个公式的 layout id 集合是否相同
        return formula1_layout_ids == formula2_layout_ids

    def process_comma_formulas(self, page: Page):
        """处理包含逗号的复杂公式，将其按逗号拆分"""
        if not page.pdf_paragraph:
            return

        for paragraph in page.pdf_paragraph:
            if not paragraph.pdf_paragraph_composition:
                continue

            new_compositions = []
            for composition in paragraph.pdf_paragraph_composition:
                if composition.pdf_formula is not None and self.should_split_formula(
                    composition.pdf_formula,
                ):
                    # 按逗号拆分公式
                    char_groups = self.split_formula_by_comma(composition.pdf_formula)
                    for chars, comma in char_groups:
                        if chars:  # 忽略空组（连续的逗号）
                            # 继承原公式的行 ID
                            formula = PdfFormula(
                                pdf_character=chars,
                                line_id=composition.pdf_formula.line_id,
                            )
                            self.update_formula_data(formula)
                            new_compositions.append(
                                PdfParagraphComposition(pdf_formula=formula),
                            )

                            # 如果有逗号，添加为文本行
                            if comma:
                                comma_line = PdfLine(pdf_character=[comma])
                                self.update_line_data(comma_line)
                                new_compositions.append(
                                    PdfParagraphComposition(pdf_line=comma_line),
                                )
                else:
                    new_compositions.append(composition)

            paragraph.pdf_paragraph_composition = new_compositions

    def remove_non_formula_lines_from_paragraphs(self, page: Page):
        """Remove non-formula lines from paragraphs.

        This method processes curves that remain in page.pdf_curve after
        collect_contained_elements() has assigned formula-related curves to formulas.
        All remaining curves are non-formula lines, but we need to be careful
        not to remove lines from figure/table areas.

        Args:
            page: The page to process
        """
        if not page.pdf_curve:
            return

        # Build layout index for efficient spatial queries
        layout_index, layout_map = build_layout_index(page)

        curves_to_remove = []

        # Get configuration thresholds
        protection_threshold = getattr(
            self.translation_config, "figure_table_protection_threshold", 0.9
        )
        overlap_threshold = getattr(
            self.translation_config, "non_formula_line_iou_threshold", 0.9
        )

        for curve in page.pdf_curve:
            # Skip if curve is in figure/table layout areas
            if is_curve_in_figure_table_layout(
                curve, layout_index, layout_map, protection_threshold
            ):
                continue

            # Only remove if curve overlaps with text paragraph areas
            if is_curve_overlapping_with_paragraphs(
                curve, page.pdf_paragraph, overlap_threshold
            ):
                curves_to_remove.append(curve)

        # Remove identified curves
        removed_count = 0
        for curve in curves_to_remove:
            if curve in page.pdf_curve:
                page.pdf_curve.remove(curve)
                removed_count += 1

        if removed_count > 0:
            import logging

            logger = logging.getLogger(__name__)
            logger.debug(f"Removed {removed_count} non-formula lines from paragraphs")
