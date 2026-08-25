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
- **后续提交**: `e7b5d11` 拆分被 layout 模型合并进同一段落的多个目录
  条目（如 Figure 28+29、Figure 108+109+110、Table 19+20）——
  `_iter_entry_spans` 从左到右扫描，标题内嵌"点线+页码+下一条目"时在
  第一个内嵌点线处截断；JESD238B.01 三处合并全部拆开
- **后续提交**: `29974d2` 目录页标题对齐+字号优化——`_split_entry` 剥离
  所有数字编号（含带点的 `6.5`/`13.5.4`，用 `m3.start("title")` 跳过前导
  空格），标题段 box 从标题列 x=112 开始；`normalize_boxes` 扩展 `box.x2`
  到布局段左边界前 2pt。验证：13.5.4 从 sz=4 恢复正常 sz=9，标题统一 x=112
- **后续提交**: `7cfed40` 目录页标题重合修复——`normalize_boxes` 对单行标题
  不再强制覆盖 `layout_para.box.y`，保持原文 y，避免长标题折行后与相邻条目重叠
- **后续提交**: `4e4dada` 目录续页识别（多页目录中不带 Contents marker 的续页）——
  原 `is_toc_page` 只认"页内含 Contents/List of Tables/List of Figures marker 且
  点线行数>=3"的页；JESD238B 的目录续页恰好每页重复 `Contents (cont'd)` 所以被覆盖，
  但 NsightCompute 第3页、NsightSystems 第3-7页的目录续页**不重复任何 marker**，
  导致续页不被识别为目录页，条目被当普通正文整体送 LLM 翻译（点线丢失、页码不再
  右对齐、编号混进标题，如 `6.3.指标与单位.58`）。修复：给 `TOCProcessor` 增加
  跨页目录状态 `_in_toc`（由 `ParagraphFinder` 持有，参照参考文献跨页方案补丁9），
  `process()` 维护状态：本页点线行数 >= `_TOC_MIN_DOTTED_LINES`(3) 且（命中 marker
  或上一页已在目录模式）→ 判为目录页并延续模式；点线行数 < 3 → 退出目录模式。
  双保险避免正文误判：无 marker 的续页必须以上一页在目录模式为前提。验证：
  NsightCompute 第3页（32/32）、NsightSystems 第3-7页（含点线=4 的目录尾页第7页）
  点线保留、页码右对齐 x≈540；回归 JESD238B.01 十页全部正常
- **上游价值**: 属上游普适缺陷（任何"多页目录续页不带重复标题"的文档，
  如 NVIDIA 系列 User Guide），可提 PR

## 7. LLM 把富文本标签 <style> 翻译成 <样式>，导致标签无法解析

- **Commit**: `e69ecec`
- **症状**: 使用中文模型（SiliconFlowFree 等）翻译时，BabelDOC 用
  `<style id='N'>` 作为富文本占位标签，LLM 把 "style" 译成 "样式"，
  输出 `<样式id='N'>`/`</样式>`，引擎无法识别标签，富文本（粗体等）
  样式失效，且标签名泄漏到译文
- **根因**: `il_translator_llm_only.py` 的翻译结果后处理只清理了
  超长标点，没有防护标签被本地化
- **修复**: 翻译结果后处理（`il_translator_llm_only.py` L753 附近）追加
  `re.sub(r"<样式\s*", "<style ", ...)` + `replace("</样式>", "</style>")`
- **验证**: v2-verify2 全产物无 `<样式` 泄漏
- **上游价值**: 属上游普适缺陷（任何会把 style 本地化的 LLM），可提 PR

## 8. 表格同列多行单元格被重新并段，导致列内容挤压（配合开关）

- **背景**: `--no-merge-mid-sentence` 开关由 PDFMathTranslate-next fork
  新增（见该仓库 LOCAL_PATCHES.md），BabelDOC 侧无需改代码——
  `translation_config.py` 本就持有 `merge_mid_sentence_paragraphs` 配置，
  `paragraph_finder.py:307` 也已按配置跳过合并
- **症状**: 表3/表6 这类表格，同列多行的时序参数（如 `t_RC...`/`t_CCDL...`
  /`t_RFC...`）被 `merge_mid_sentence_continuation_paragraphs` 并回一段，
  译文流式重排后全部挤到表格顶部，不再对齐所属行标签
- **根因**: 合并条件是"上段无句末标点 + 下段小写开头 + 垂直相邻"，
  表格单元格行恰好满足（`t_xxx` 开头、无句号），被误判为"句中续接"
- **修复**: 关闭该合并（CLI 开关），配合 `--split-short-lines` 让表格行
  保持独立段落、保留原始 y 对齐
- **验证**: JESD238B.01 表3/表6 行对齐恢复，问题2 bullet 列表换行也顺带修复
- **注意**: 副作用是"图片旁正文被句中切断"场景（补丁5 修复目标）不再自动
  合并；若需两者兼顾，应实现 table_processor 精确方案（仅对表格区域拆段）

## 9. 引擎级 References/Bibliography 参考文献不翻译

- **Commit**: `31249b4`
- **症状**: 学术论文的 References 章节被翻译成中文（如 jun2017：
  `[1] JEDEC标准高带宽内存...`、electronics-14-02682：
  `1. Jun, H.; ...` 被译成中文）。common_rules.md 已有"References
  不翻译"提示词规则，但弱模型 LLM（SiliconFlowFree 等）常忽略
- **根因**: 提示词规则依赖模型遵守，免费弱模型不可靠；引擎没有
  "参考文献"概念，参考文献段与普通正文走相同翻译链路
- **修复**（`paragraph_finder.py` + 两条 translator 路径）:
  - `ParagraphFinder._mark_reference_paragraphs`：检测
    `REFERENCES`/`References`/`BIBLIOGRAPHY` 标题段后，其下方
    （IL y 更小）以 `[N]`（IEEE 风格）或 `N.`（MDPI/Elsevier 风格）
    开头的段落标记 `skip_translate=True`
  - 跨页延续：`ParagraphFinder._in_references` 实例状态跨页跟踪，
    上页处于参考文献模式时，后续页以 `[N]`/`N.` 开头的段落继续标记
  - `il_translator.py`（`get_translate_input`）与
    `il_translator_llm_only.py`（`process_page`）跳过 `skip_translate`
    段落，保持原文 passthrough
  - `il_version_1.py`：`PdfParagraph` 新增 `skip_translate` 运行时标记
  - 注意：调用时机在 `update_paragraph_data(update_unicode=True)` 之前，
    段落 unicode 未填充，需用 `_para_text` 从 composition 拼文本
- **验证**: jun2017 REFERENCES 页 `[1]`/`[3]` 标记成功；electron
  文档 References（`1.` 格式）单页及跨页（33-37）全部标记，正文/
  作者贡献/Disclaimer 不受影响
- **上游价值**: 属上游普适缺陷（任何学术论文），可考虑提 PR

## 10. 清理 LLM 输出的空 <sub>/<sup> 标签 + 度符号归一化 + 下标公式占位符回填

- **Commit**: `c9ee0bf`
- **症状**: 译文正文出现 `SiO<sub></sub>`/`mm<sup></sup>` 空上下标标签
  （MDPI 论文第 9/10 页），以及 `14 ◦℃`/`°C℃` 双度符号
- **根因（部分）**:
  1. 源文档用 `◦`(U+25E6) 表示度；`styles_and_formulas` 把 `◦C`/下标数字
     （如 SiO2 的 "2"）误判为公式占位符 `{vN}`
  2. 模型输出 `<sub>{vN}</sub>` 时，`parse_translate_output` 把内层 `{vN}`
     当富文本内容，经 `remove_placeholder` 删除 -> 回填变空，留下 `<sub></sub>`
- **修复**（`il_translator.py` + `paragraph_finder.py`）:
  - `paragraph_finder.process_page`：源字符 `◦`→`°` 归一化
  - `il_translator.get_translate_input`：同上（占位符回填/未译直出路径）
  - `il_translator.parse_translate_output`：新增 `inner_formula`——富文本标签
    内部若为公式占位符 `{vN}`，回填公式内容而非删除（修复 `SiO2`→`SiO` 丢下标）
  - `il_translator.post_translate_paragraph`：新增清理管线
    1) strip 空 `<sub></sub>`/`<sup></sup>`
    2) `◦℃`/`◦C`/`°℃`→`℃` 归一化
    3) 兜底循环：删空 composition、跨 composition 空标签剔除、度符号合并
    4) 最终兜底：完整拼接文本检测到 `◦℃` 等度符号双写则重建纯文本
  - `paragraph_finder.merge_title_caption_and_table_fragments`（新增）：
    合并同一行水平切分的标题/图题片段，解决英文残留/译文逐词碎片化
- **验证**: electronics 全量 `sub_empty`/`sup_empty` = 0；单页 p9 IL 层
  `formula '2'` 保留、渲染 `SiO2` 完整
- **已知未解决（重要）**:
  1. **标签对含内容时仍以字面量显示**：`<sub>2</sub>`（非空）不会转为
     真下标，而是作为字面量 `<sub>2</sub>` 渲染在文档中，不符合要求
  2. **空标签根因未完全消除**：`<sub>{vN}</sub>` 的回填在
     `parse_translate_output`（清理之前）发生，空标签的"是否保留下标内容"
     依赖模型是否输出下标数字；模型随机漏掉下标时（`SiO2`→`SiO`）无法恢复。
     属于引擎级 + 模型质量双重因素，后续需在 typesetting 层面或占位符回填
     机制上根治
  3. **p32 `200 ◦℃与℃之间`**：模型随机垃圾输出，该段落会进
     `post_translate_paragraph`，清理逻辑理论上能兜住，但需多次全量验证稳定性
- **上游价值**: 空标签/双度符号属上游普适缺陷，可考虑提 PR；但"标签对有内容
  仍字面量显示"的根治需更深引擎改动，暂以本补丁缓解
- **与问题7的区别**（均为弱中文模型对 HTML 标签处理不可靠的上层现象，但机制不同）:
  - 问题7 = 标签**被改名**：引擎占位符 `<style id='N'>` 被 LLM 译成 `<样式id='N'>`，
    是"破坏引擎已有标签"
  - 问题10 = 标签**被伪造**：引擎并无 `<sub>/<sup>` 占位符，`<sub>/<sup>` 是 LLM
    凭空生成，且下标 `{vN}` 占位符回填变空。真正相关的是**占位符回填机制**，
    而非问题7的"标签本地化"。修复位置也不同：问题7在 llm_only 后处理，
    问题10在 parse_translate_output 回填 + post_translate 清理管线

## 附：相关但未修改的上游问题

- `warmup()` 一次性预下载全部字体，慢网环境拖慢启动--已在
  PDFMathTranslate-next fork 中禁用调用（`pdf2zh_next/main.py`），
  BabelDOC 本体未改
- ~~`pdf2zh-next` 的 `high_level.py` 未把 `save_auto_extracted_glossary`
  传给 BabelDOC config~~ --已根治：在 PDFMathTranslate-next fork 的
  `high_level.py` 补转发该参数（详见该仓库 LOCAL_PATCHES.md 第 2 条，
  双向验证通过）。BabelDOC 本体无需改动
