import logging
import math
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
from pymupdf import Document

import babeldoc.format.pdf.document_il.utils.extract_char
from babeldoc.format.pdf.document_il import il_version_1
from babeldoc.format.pdf.document_il.utils.style_helper import GREEN
from babeldoc.format.pdf.translation_config import TranslationConfig

logger = logging.getLogger(__name__)


class LayoutParser:
    stage_name = "Parse Page Layout"

    def __init__(self, translation_config: TranslationConfig):
        self.translation_config = translation_config
        self.model = translation_config.doc_layout_model

    def _save_debug_image(self, image: np.ndarray, layout, page_number: int):
        """Save debug image with drawn boxes if debug mode is enabled."""
        if not self.translation_config.debug:
            return

        debug_dir = Path(self.translation_config.get_working_file_path("ocr-box-image"))
        debug_dir.mkdir(parents=True, exist_ok=True)

        # Draw boxes on the image
        debug_image = image.copy()
        for box in layout.boxes:
            x0, y0, x1, y1 = box.xyxy
            cv2.rectangle(
                debug_image,
                (int(x0), int(y0)),
                (int(x1), int(y1)),
                (0, 255, 0),
                2,
            )
            # Add text label
            cv2.putText(
                debug_image,
                layout.names[box.cls],
                (int(x0), int(y0) - 5),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 0),
                1,
            )
        img_bgr = cv2.cvtColor(debug_image, cv2.COLOR_RGB2BGR)

        # Save the image
        output_path = debug_dir / f"{page_number}.jpg"
        cv2.imwrite(str(output_path), img_bgr)

    def _save_debug_box_to_page(self, page: il_version_1.Page):
        """Save debug boxes and text labels to the PDF page."""
        if not self.translation_config.debug:
            return

        color = GREEN

        for layout in page.page_layout:
            # Create a rectangle box
            scale_factor = 1
            if layout.class_name == "fallback_line":
                scale_factor = 0.1
            rect = il_version_1.PdfRectangle(
                box=il_version_1.Box(
                    x=layout.box.x,
                    y=layout.box.y,
                    x2=layout.box.x2,
                    y2=layout.box.y2,
                ),
                graphic_state=color,
                debug_info=True,
                line_width=0.4 * scale_factor,
            )
            page.pdf_rectangle.append(rect)

            # Create text label at top-left corner
            # Note: PDF coordinates are from bottom-left,
            # so we use y2 for top position
            style = il_version_1.PdfStyle(
                font_id="base",
                font_size=4 * scale_factor,
                graphic_state=color,
            )
            page.pdf_paragraph.append(
                il_version_1.PdfParagraph(
                    first_line_indent=False,
                    box=il_version_1.Box(
                        x=layout.box.x,
                        y=layout.box.y2,
                        x2=layout.box.x2,
                        y2=layout.box.y2 + 5,
                    ),
                    vertical=False,
                    pdf_style=style,
                    unicode=layout.class_name,
                    pdf_paragraph_composition=[
                        il_version_1.PdfParagraphComposition(
                            pdf_same_style_unicode_characters=il_version_1.PdfSameStyleUnicodeCharacters(
                                unicode=layout.class_name,
                                pdf_style=style,
                                debug_info=True,
                            ),
                        ),
                    ],
                    xobj_id=-1,
                ),
            )

    def process(self, docs: il_version_1.Document, mupdf_doc: Document):
        """Generate layouts for all pages that need to be translated."""
        # Get pages that need to be translated
        total = len(docs.page)
        with self.translation_config.progress_monitor.stage_start(
            self.stage_name,
            total * 2,
        ) as progress:
            # Process predictions for each page
            for page, layouts in self.model.handle_document(
                docs.page,
                mupdf_doc,
                self.translation_config,
                self._save_debug_image,
            ):
                page_layouts = []
                for layout in layouts.boxes:
                    # Convert coordinate system from picture to il
                    # system to the il coordinate system
                    x0, y0, x1, y1 = layout.xyxy
                    # pix = get_no_rotation_img(mupdf_doc[page.page_number])
                    # pix = mupdf_doc[page.page_number].get_pixmap()
                    # h, w = pix.height, pix.width
                    box = mupdf_doc[page.page_number].mediabox_size
                    b_h = math.ceil(box.y)
                    b_w = math.ceil(box.x)
                    # if b_h != h or b_w != w:
                    #     logger.warning(f"page {page.page_number} mediabox is not correct, b_h: {b_h}, h: {h}, b_w: {b_w}, w: {w}")
                    h, w = b_h, b_w
                    x0, y0, x1, y1 = (
                        np.clip(int(x0 - 1), 0, w - 1),
                        np.clip(int(h - y1 - 1), 0, h - 1),
                        np.clip(int(x1 + 1), 0, w - 1),
                        np.clip(int(h - y0 + 1), 0, h - 1),
                    )
                    page_layout = il_version_1.PageLayout(
                        id=len(page_layouts) + 1,
                        box=il_version_1.Box(
                            x0.item(),
                            y0.item(),
                            x1.item(),
                            y1.item(),
                        ),
                        conf=layout.conf.item(),
                        class_name=layouts.names[layout.cls],
                    )
                    page_layouts.append(page_layout)

                page.page_layout = page_layouts
                # self.generate_fallback_line_layout_for_page(page)
                # self._save_debug_box_to_page(page)
                progress.advance(1)
            with ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
                for page in docs.page:
                    executor.submit(
                        self.generate_fallback_line_layout_for_page, page, progress
                    )
        return docs

    def generate_fallback_line_layout_for_page(self, page: il_version_1.Page, progress):
        try:
            exists_page_layouts = page.page_layout
            char_boxes = babeldoc.format.pdf.document_il.utils.extract_char.convert_page_to_char_boxes(
                page
            )
            if not char_boxes:
                return

            clusters = babeldoc.format.pdf.document_il.utils.extract_char.process_page_chars_to_lines(
                char_boxes
            )
            table_boxes = [
                layout.box
                for layout in exists_page_layouts
                if layout.class_name == "table"
            ]
            table_of_cluster = self._assign_clusters_to_tables(clusters, table_boxes)
            table_col_starts = self._collect_table_col_starts(
                clusters, table_of_cluster
            )
            for cluster, tidx in zip(clusters, table_of_cluster):
                if tidx >= 0 and len(cluster.chars) >= 3:
                    char_groups = self._split_table_line_chars(
                        cluster.chars, table_col_starts[tidx]
                    )
                else:
                    char_groups = [cluster.chars]
                for sub_chars in char_groups:
                    boxes = [c[0] for c in sub_chars]
                    min_x = min(b.x for b in boxes)
                    max_x = max(b.x2 for b in boxes)
                    min_y = min(b.y for b in boxes)
                    max_y = max(b.y2 for b in boxes)
                    page_layout = il_version_1.PageLayout(
                        id=len(exists_page_layouts) + 1,
                        box=il_version_1.Box(
                            min_x,
                            min_y,
                            max_x,
                            max_y,
                        ),
                        conf=1,
                        class_name="fallback_line",
                    )
                    exists_page_layouts.append(page_layout)
            self._save_debug_box_to_page(page)
        finally:
            progress.advance(1)

    def _assign_clusters_to_tables(
        self, clusters, table_boxes: list[il_version_1.Box]
    ) -> list[int]:
        """把每个 cluster 归到其完全落于的 table 框（-1 表示不在任何表格内）。

        一个 cluster（行）必须同时被 table 框的 x、y 范围完整包含，才认为它属于
        该表格，避免把表格框外的正文段落误当作表格单元格。
        """
        result = []
        for cluster in clusters:
            if not cluster.chars:
                result.append(-1)
                continue
            boxes = [c[0] for c in cluster.chars]
            min_x = min(b.x for b in boxes)
            max_x = max(b.x2 for b in boxes)
            min_y = min(b.y for b in boxes)
            max_y = max(b.y2 for b in boxes)
            tidx = -1
            for i, tb in enumerate(table_boxes):
                if (
                    tb.x <= min_x
                    and max_x <= tb.x2
                    and tb.y <= min_y
                    and max_y <= tb.y2
                ):
                    tidx = i
                    break
            result.append(tidx)
        return result

    def _collect_table_col_starts(self, clusters, table_of_cluster) -> dict[int, set]:
        """收集每个表格内各行的 x 起点，作为候选列边界。"""
        col_starts = {}
        for cluster, tidx in zip(clusters, table_of_cluster):
            if tidx < 0 or not cluster.chars:
                continue
            min_x = min(c[0].x for c in cluster.chars)
            col_starts.setdefault(tidx, set()).add(round(min_x, 1))
        return col_starts

    def _split_table_line_chars(self, chars, col_starts: set) -> list:
        """表格内按列边界拆分字符。

        当 DocLayout 未识别出表格单元格、fallback_line 兜底聚类把相邻列字符合并进
        同一行（如 WDQS Phase 与 DERR0 因字符中心距 < eps 被并成一个 cell）时，
        通过"无字符填充的 x 空隙 + 空隙右侧块起点命中表格列起点"识别列边界并拆分，
        使相邻列内容各自成为独立的 fallback_line。

        仅影响表格内 cluster；正文/普通文本无列起点可命中，保持原样不拆分。
        """
        if not chars:
            return [chars]
        ordered = sorted(chars, key=lambda c: c[0].x)
        subgroups = []
        cur = [ordered[0]]
        for i in range(1, len(ordered)):
            prev = ordered[i - 1][0]
            curr = ordered[i][0]
            gap = curr.x - prev.x2
            if gap > 2.0 and any(abs(curr.x - cs) <= 1.5 for cs in col_starts):
                subgroups.append(cur)
                cur = [ordered[i]]
            else:
                cur.append(ordered[i])
        subgroups.append(cur)
        return subgroups
