# 本地补丁记录（相对上游 funstory-ai/BabelDOC）

本 fork 基于 BabelDOC v0.6.4 源码，包含以下本地修复。
上游版本可能已修复，同步上游时需逐条核对。

## 1. 自动术语表 tgt_lng 列始终为空

- **Commit**: `2d0b508`
- **症状**: v2 翻译产物 `xxx.zh.glossary.csv` 的 `tgt_lng` 列全为空
- **根因**: `format/pdf/translation_config.py` 中
  `SharedContextCrossSplitPart` 不持有 `lang_out` 属性；
  `TranslationConfig.__init__` 创建它时未传入 `lang_out`；
  `finalize_auto_extracted_glossary()` 构造 `GlossaryEntry(src, tgt)` 时
  漏传第三个参数 `target_language`
- **修复**: 两处改动
  - `TranslationConfig.__init__` 创建 shared 对象后补
    `self.shared_context_cross_split_part.lang_out = lang_out`
  - `finalize_auto_extracted_glossary` 用
    `getattr(self, "lang_out", None)` 传入 `GlossaryEntry`
- **验证**: 翻译后 CSV 的 tgt_lng 列正确输出 `zh`

## 2. 资产缓存目录硬编码到用户主目录

- **Commit**: `9ec9119`
- **动机**: 自包含部署——字体/CMap/版面模型缓存（约 336MB）默认在
  `~/.cache/babeldoc`，整个项目目录搬迁后需手动恢复缓存
- **修复**: `babeldoc/const.py` 的 `CACHE_FOLDER` 改为
  `Path(__file__).resolve().parents[2] / "babeldoc_cache"`
  （即项目根下的 `babeldoc_cache/`，与 `.venv` 同级）
- **影响**: 属部署偏好而非上游 bug，**不应**提 PR 回上游；
  同步上游时需保留此改动
- **验证**: `from babeldoc.const import CACHE_FOLDER` 指向新路径；
  `get_font_and_metadata` 从新路径命中缓存，无需下载

## 3. 排版扩容缺少图片/矢量图阻挡检查，导致译文跨列压图

- **Commit**: `cdc3c5a`
- **症状**: 两列学术论文（如 jun2017 HBM 论文），某段中文译文不保持
  原列宽，横跨整页宽度（最高达页宽 81%），与相邻列图片重叠，
  文字被图片遮住不可读
- **根因**: `document_il/midend/typesetting.py` 中文译文缩放至 0.7 仍
  放不下时会向右/向下扩容 `paragraph.box`；扩容上限由
  `get_max_right_space` / `get_max_bottom_space` 计算，但二者只检查
  `pdf_paragraph` / `pdf_character` / `pdf_figure` 三类阻挡物——
  而图片 XObject 实际存于 `page.pdf_form`（`form_type="image"`，见
  `il_creater.py`），矢量图表存于 `page.pdf_curve`，均不在检查范围，
  扩容上限直达 `cropbox.x2 * 0.9`
- **修复**: 两个函数各补齐 `pdf_form`（跳过 `box is None`）与
  `pdf_curve` 的同构阻挡检查
- **验证**: jun2017 第 1 页问题段行宽从 318–499pt 降至 ≤254pt，
  与 Fig.2 图片 bbox 零重叠，文字正常换行回到左列；标题/作者等
  通栏元素不受影响
- **上游价值**: 属上游 bug，可提 PR（修复方式与现有 figure 检查同构，
  侵入性小）

## 附：相关但未修改的上游问题

- `warmup()` 一次性预下载全部字体，慢网环境拖慢启动--已在
  PDFMathTranslate-next fork 中禁用调用（`pdf2zh_next/main.py`），
  BabelDOC 本体未改
- ~~`pdf2zh-next` 的 `high_level.py` 未把 `save_auto_extracted_glossary`
  传给 BabelDOC config~~ --已根治：在 PDFMathTranslate-next fork 的
  `high_level.py` 补转发该参数（详见该仓库 LOCAL_PATCHES.md 第 2 条，
  双向验证通过）。BabelDOC 本体无需改动
