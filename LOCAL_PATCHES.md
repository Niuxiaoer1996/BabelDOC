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
- **后续发现**: 修复后该段字号缩至 6.97pt 且与上文出现空隙，根因是
  版面模型把原连续段落句中切成两块（详见第 5 条），非本补丁副作用；
  第 5 条落地后该段以 9.46pt 统一字号连续排版
- **上游价值**: 属上游 bug，可提 PR（修复方式与现有 figure 检查同构，
  侵入性小）

## 4. 自动术语提取包含人名/地名/机构名/日期

- **Commit**: `42e32e9`
- **症状**: 自动提取的 `*.zh.glossary.csv` 混入作者名（Hongshin Jun
  等）、城市（Icheon）、公司（SK hynix）、国名（Korea），回灌术语库
  后成为噪音
- **根因**: `automatic_term_extractor.py` 的 `LLM_PROMPT_TEMPLATE`
  规则 1 明确要求提取 `named entities (people, orgs, locations, ...,
  dates)`，人名地名是"按设计"进来的
- **修复**: 规则 1 改为只提取定理/算法名与领域专用名词短语，并显式
  加排除指令 `Exclude person names, organization names, locations,
  and dates`
- **验证**: 重翻 jun2017 第 1 页，新 glossary.csv 58 条全部为技术
  术语，人名/地名/公司名零泄漏
- **上游价值**: 属行为偏好（上游可能有意保留命名实体用于翻译一致性），
  不回提

## 5. 版面模型把图片旁连续段落在句中切成多块，独立排版后字号缩小/出现空隙

- **Commit**: `ba1017a`
- **症状**: jun2017 第 1 页左栏一个连续段落（原文 TimesNewRoman
  9.95pt 12 行无间隙）被切成 2 块，各自独立翻译排版后：上块中文
  4 行装完留下 2 行空白、下块被 Fig.2 挤压缩到 6.97pt，且译文在句中
  截断
- **根因**: DocLayout 模型受相邻图片（Fig.2 bbox y585-678）干扰，
  把该段切成两个布局块；`_group_characters_into_paragraphs` 遇到
  不同 layout id 即开新段落，两块各自走缩放/扩容逻辑
- **修复**: `paragraph_finder.py` 新增
  `merge_mid_sentence_continuation_paragraphs` 后处理（配置开关
  `merge_mid_sentence_paragraphs`，默认开启），在
  `merge_alternating_line_number_paragraphs` 之后运行。严格条件下
  合并"句中续接"块：
  - a 段末尾无句末标点（`. : ; ! ? 。：；！？`）
  - b 段以小写字母开头、无首行缩进
  - b 在 a 正下方（垂直间距 ≤1.3 倍行距，容忍 -2pt）
  - 水平重叠 > 较窄段宽 50%、同布局标签、同 xobj、首尾行高比 0.6~1.6
  - 双栏渲染顺序中列表相邻 ≠ 几何相邻，需全页搜索几何最近下方候选
    （诊断确认候选在列表下标 8 而非 i+1=7）
- **验证**: 重翻 jun2017 第 1 页，该段合并为 9 行连续排版、统一
  9.46pt、译文含完整句尾（"…3D IC）。"）、与 Fig.2 零重叠；
  真正的下一段（首行缩进+句号结尾）不受影响
- **上游价值**: 属上游普适缺陷（任何"图片旁正文被句中切断"场景），
  修复侵入性中等，可考虑提 PR

## 6. 目录页（TOC / List of Tables / List of Figures）条目结构化处理

- **Commit**: `2ff2271`
- **症状**: 目录行被当作普通段落整体送 LLM 翻译，四个问题：
  1. 章节/表/图编号被误译：裸整数 "1" 被并进标题段后 LLM 译成
     "页码1"（`merge_alternating_line_number_paragraphs` 只识别
     纯数字段，带点的 "3.1" 反而不合并，行为不一致）
  2. 点引导线丢失/错乱：LLM 随意处理点线，且两条翻译路径都有
     `re.sub(r"[. 。…，]{20,}", ".", ...)`（`il_translator.py`
     L1263 / `il_translator_llm_only.py` L754），把保留的点线压成单点
  3. 页码不再右对齐：`typesetting.py` 是纯流式布局，无右对齐机制
  4. 页眉（"Contents (cont'd)"、"Page" 等）被 layout 模型合并进条目
- **根因**: 引擎没有"目录页"概念，目录行与正文走完全相同的
  "段落合并 -> LLM 翻译 -> 流式排版"链路
- **修复**（方案 B，引擎内结构化处理）:
  - 新增 `midend/toc_processor.py`（`TOCProcessor`）：
    - 按 markers（Contents / List of Figures / List of Tables）+
      点线行密度检测目录页
    - 把每个目录条目拆成**标题段**（`toc_role="title"`，含 inline 的
      "Table N -"/"6.2.1.1" 前缀，LLM 可可靠本地化/保留）与
      **布局段**（`toc_role="layout"`：点引导线 + 页码 + 被剥离的
      裸整数编号，不送 LLM，typesetting passthrough 按原始字符坐标
      渲染 -> 点线保留、页码右对齐天然成立）
    - 多行标题续接合并（目录标题折行时把上方同列续接行并入标题段）
    - "Contents (cont'd)"/"Page(s)" 等页眉前缀从被 layout 合并的
      条目中剥离为独立普通段（正常翻译）
  - `il_version_1.py`：`PdfParagraph` 新增 `toc_role` 字段
  - `paragraph_finder.py`：目录页跳过
    `merge_alternating_line_number_paragraphs` 与
    `merge_mid_sentence_continuation_paragraphs`；
    `fix_overlapping_paragraphs` 跳过 toc 段；
    处理末尾 `normalize_boxes()` 统一标题段/布局段 y 基准
  - `il_translator.py`（`get_translate_input`，两条路径共用）与
    `automatic_term_extractor.py`：跳过 `toc_role="layout"` 段落
  - `typesetting.py`：`render_page` 的段落避让调整跳过 toc 段
  - `translation_config.py` / `main.py`：新增 `fix_toc` 开关
    （默认开启，CLI `--no-fix-toc`）；pdf2zh-next fork 的
    `high_level.py` 已转发该参数（`no_fix_toc`）
- **验证**: JESD238B.01 目录 10 页重译：章节编号原样（裸整数在布局段
  passthrough、"Table/Figure N" 由 LLM 本地化为 表N/图N）、点引导线
  完整、页码右对齐到原右缘（x2≈540）、"Contents (cont'd)" 等页眉
  正常翻译为"目录（续）"；`fix_toc.py` 后处理脚本与 v2_run.py 的
  `restore_toc_pages` 贴回逻辑随即退役
- **后续提交**: `69cc002` 精化 `normalize_boxes` y 基准——单行标题框
  复用同行布局段的 y（译文标题基线与点线/页码严格对齐，渲染基线偏差
  <0.1pt），折行续接的标题锚定最上方行（IL 底上坐标取 y 最大者，
  修正先前误锚到点线行的问题）
- **上游价值**: 属上游普适缺陷（任何带点引导线的目录页），修复侵入性
  中等，可提 PR；注意与 `merge_alternating_line_number_paragraphs`
  的交互需保留目录页跳过逻辑

## 附：相关但未修改的上游问题

- `warmup()` 一次性预下载全部字体，慢网环境拖慢启动--已在
  PDFMathTranslate-next fork 中禁用调用（`pdf2zh_next/main.py`），
  BabelDOC 本体未改
- ~~`pdf2zh-next` 的 `high_level.py` 未把 `save_auto_extracted_glossary`
  传给 BabelDOC config~~ --已根治：在 PDFMathTranslate-next fork 的
  `high_level.py` 补转发该参数（详见该仓库 LOCAL_PATCHES.md 第 2 条，
  双向验证通过）。BabelDOC 本体无需改动
