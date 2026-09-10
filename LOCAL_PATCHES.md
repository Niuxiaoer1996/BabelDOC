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

- **Commit**: 2ff2271（另含 69cc002/e7b5d11/29974d2/7cfed40/4e4dada）
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

## 11. post_translate_paragraph 统一清理管线增强（覆盖 fallback 路径 + Markdown + sub/sup + 公式丢失）

- **Commit**: `91c6699`（该补丁代码合并于此批量提交）
- **症状**: 多文档翻译后出现四类问题：
  1. `<样式 id='1'></样式>` 标签泄漏（Hopper GPU 论文第9页）--问题7修复在
     llm_only 路径，但 fallback 到传统路径时无修复
  2. `**`/`****` Markdown 符号泄漏（electronics-14 第11-12页、jun2017 第4页）--
     LLM 把 `<style>` 富文本标签误转为 Markdown 粗体
  3. `<sup>[53]</sup>` 等有内容标签字面量显示（electronics-14 第19页）--
     问题10只清理空标签，有内容标签未处理
  4. 图标题中公式丢失（Hopper GPU 第4页 `D = A × B + C`）--LLM 丢弃 `{vN}` 占位符
- **根因**:
  1. `<样式>` 修复只在 `il_translator_llm_only.py:764-771`（LLM-only 成功路径），
     fallback 到 `il_translator.translate_paragraph`（传统路径）时无此修复。
     `post_translate_paragraph` 是两条路径的公共出口，但之前未在此处修复
  2. `post_translate_paragraph` 无 Markdown 符号清理；`common_rules.md` 有提示词
     禁止但弱模型不可靠
  3. `post_translate_paragraph:1034` 只清理空 `<sub></sub>`/`<sup></sup>`，
     有内容的 `<sup>[53]</sup>` 保留为字面量。`<sub>`/`<sup>` 不是引擎占位符
     （引擎用 `<style id='N'>`），是 LLM 凭空生成
  4. LLM 丢弃 `{vN}` 公式占位符后，`parse_translate_output` 回填时找不到匹配，
     公式内容丢失，且无检测机制
- **修复**（`il_translator.py` `post_translate_paragraph`，四处改动）:
  1. 在清理管线开头加入 `<样式>` -> `<style>` 修复（`re.sub` + `replace`），
     统一覆盖两条路径
  2. 加入 Markdown 符号清理：`re.sub(r"\*{2,}", ...)` / `_{2,}` / `~{2,}`，
     只清理 2+ 连续符号，单个 `*`（乘号）、`_`（变量名）不受影响
  3. 将空标签清理 `r"<(?:sub|sup)>\s*</(?:sub|sup)>"` 改为全标签清理
     `r"</?(?:sub|sup)>"`（提取内容，去掉标签壳）
   4. 在度符号归一化之后、`translated_text == translate_input` 检查之前，
      检测 `translate_input.placeholders` 中的 `FormulaPlaceholder` 是否在
      `translated_text` 中丢失（使用与 `parse_translate_output` 相同的 regex
      模式，允许空格/大小写变体）。丢失时仅记录 `logger.warning`，**不回退**--
      整段不翻译比丢失个别公式对用户体验更差。
- **验证**: 待翻译验证
- **上游价值**: 1-3 属上游普适缺陷（弱模型标签/符号处理不可靠），可考虑提 PR；
  4 属上游普适缺陷（公式占位符丢失无检测），可考虑提 PR

## 12. table-aware 合并保护增强：IOU 检测

- **Commit**: `91c6699`（该补丁代码合并于此批量提交）
- **症状**: JESD238B.01 表4/表6/表38/表39 表格内容挤压、无序列表乱行
  （第15/92/129页），单页测试正常但整篇翻译时复现
- **根因**: `_is_in_table_layout` 用段落中心点是否在 table 版面框内判定。
  整篇翻译时版面模型的 table 检测精度与单页不同，部分单元格行中心点
  落在 table 框外（如单元格行偏窄），被漏标为非表格段落，随后被
  `merge_mid_sentence_continuation_paragraphs` 误并段
- **修复**（`paragraph_finder.py` `_is_in_table_layout`）:
  保留原中心点检测，新增 IOU 检测：段落 box 与 table 框重叠面积超过
  段落面积 50% 也标记为 `in_table_layout=True`
- **验证**: 待翻译验证
- **上游价值**: 属上游普适缺陷（表格检测精度不足），可考虑提 PR

## 13. 提示词增强：Note 翻译规则 + 代码/函数名保护

- **Commit**: 无（位于 skill 仓库 `pdf2zh-domain/prompts/base/common_rules.md`，不在 BabelDOC）
- **症状**:
  1. JESD238B.01 图42/43/45 的 NOTE 部分乱行，"NOTE" 译法不统一
  2. NsightSystemsUserGuide 中 API 函数名、命令行工具名被翻译
- **根因**:
  1. 提示词无 NOTE 统一译法规则
  2. 提示词虽有"代码不翻译"规则但不够具体，弱模型将 camelCase/snake_case
     标识符当普通英文翻译
- **修复**（`pdf2zh-domain/prompts/base/common_rules.md`）:
  1. "参考内容处理"部分加入：NOTE/NOTE 1 -> 注/注 1，Note 内容逐行翻译不合并
  2. "不翻译的内容"部分加入：API 函数名、命令行工具名及参数、代码块内容、
     配置文件键名、camelCase/snake_case 标识符、文件路径和 URL
- **验证**: 翻译验证通过
- **上游价值**: 属领域提示词优化，不回提上游

## 14. 图表 NOTE 多条目段落拆分

- **Commit**: `91c6699`（该补丁代码合并于此批量提交）
- **症状**: JESD238B.01 图42/43/45 的 NOTE 部分（NOTE 1~7）格式乱行，
  所有 NOTE 条目被翻译为一个连续段落，丢失逐行格式
- **根因**: 版面模型将图表 Note 区域的多行 NOTE 条目归为同一布局块，
  `_group_characters_into_paragraphs` 将它们合并为一个段落。LLM 翻译
  整段后，逐行格式丢失。行分段（`_split_paragraph_into_lines`）已正确
  将每行拆为独立 `PdfLine`，但段落级别未拆分
- **修复**（`paragraph_finder.py`）:
  - 新增 `split_note_paragraphs`：在行分段后（第三步）、合并步骤前执行
  - 检测段落中以 `^NOTE\s+\d+` 开头的行（`PdfLine`），按行拆分为独立段落
  - 每个 NOTE 条目（含续行）成为独立段落，独立翻译、保留原始行格式
  - 新增 `_create_split_paragraph` 辅助函数：从原段落的 composition 子集
    创建新段落，继承 `pdf_style`/`layout_id`/`layout_label`/`xobj_id`
  - 仅当段落含 2+ 个 NOTE 行时才拆分，避免误拆单 NOTE 段落
- **验证**: 翻译验证通过
- **上游价值**: 属上游普适缺陷（任何图表 NOTE 多条目场景），可考虑提 PR

## 15. bullet 点字符误判为公式导致无序列表格式乱

- **Commit**: `91c6699`（该补丁代码合并于此批量提交）
- **症状**: JESD238B.01 正文无序列表（第15页第2章特性、第129页 6.9.1
  HBM3 ECC features 的两级列表）格式乱行——部分列表项的 bullet 点丢失，
  子 bullet（`\uf09e`）也丢失，导致列表项内容与 bullet 错位
- **根因**: bullet 字符（`•` U+2022、`\uf09e`/`\uf09f` 等 Wingdings 私有区字符）
  在 Symbol/Wingdings 特殊字体中，被 `styles_and_formulas.py` 的公式检测
  （`char.pdf_style.font_id in formula_font_ids`）误判为公式，替换成 `{vN}`
  占位符送 LLM 翻译。LLM 不一致地保留/丢弃这些占位符（如 `自动裸片错误清理机制`
  丢失 `•`），导致 bullet 丢失
- **修复**（两处）:
  1. `layout_helper.py`：`BULLET_POINT_PATTERN` 追加 `\uf09e\uf09f\uf0a7\uf0b7\uf0d8\uf0e0`
     等 Wingdings/Symbol 常用 bullet 私有区字符
  2. `styles_and_formulas.py`：在公式/角标判定之后、空格处理之前，增加
     `if is_bullet_point(char): is_formula = False`，强制 bullet 字符不作为公式
- **验证**: 待翻译验证
- **上游价值**: 属上游普适缺陷（任何用特殊字体的 bullet 列表），可考虑提 PR

## 16. 参考文献标题正则误匹配单数 "Reference"（表格列标题）导致章节标题被跳过

- **Commit**: `91c6699`（该补丁代码合并于此批量提交）
- **症状**: electronics-14-02682-v2 第3页第2章标题 `2. Background and Research
  Approaches in Hybrid Bonding` 未翻译，保留英文原文
- **根因**: `_REFERENCE_HEADER_RE` 正则 `^\s*(?:REFERENCES?|BIBLIOGRAPHY)\s*$`
  中 `REFERENCES?` 的 `S?` 使 S 可选，配合 `re.IGNORECASE` 导致单数
  `Reference`（表1列标题）也匹配。误触发参考文献模式后，同页以 `数字.`
  开头的段落（如 `2. Background...`）被 `_REFERENCE_ENTRY_RE` 匹配，
  标记 `skip_translate=True`
- **修复**（`paragraph_finder.py`）: `REFERENCES?` 改为 `REFERENCES`（仅复数），
  保留 `re.IGNORECASE`。`REFERENCES` 匹配 "references"/"References"/"REFERENCES"，
  不匹配 "reference"/"Reference"
- **验证**: 翻译验证通过
- **上游价值**: 属上游普适缺陷（表格含 "Reference" 列名的文档），可考虑提 PR

## 17. 章节标题未翻译（LLM 将全大写连写词当标识符）+ 跨页翻译内容重复

- **Commit**: `91c6699`（该补丁代码合并于此批量提交）
- **症状**:
  1. LightRAG 第3页标题 `3 THELIGHTRAG ARCHITECTURE` 未翻译，LLM 原样返回
  2. Evolution of GPU 第1-2页跨页段落翻译内容有重复
- **根因**:
  1. PDF 提取时 `THE LIGHTRAG` 缺空格变成 `THELIGHTRAG`，LLM 将其当作
     标识符/代码不翻译（提示词有"代码不翻译"规则）
  2. 跨页 batch 翻译时，LLM 在两个段落的翻译中包含了重叠内容
- **修复**:
  1. `common_rules.md`：加入"章节标题必须翻译"规则，明确即使全大写缩写或
     连写词也要翻译
  2. `il_translator_llm_only.py` PROMPT_TEMPLATE：在 Structure Rules 第2条
     增加 "Each output paragraph must contain only the translation of its
     corresponding input. Do NOT repeat or overlap content from other paragraphs."
- **验证**: 翻译验证通过
- **上游价值**: 1 属领域提示词优化；2 属上游普适缺陷（跨页 batch 翻译），
  可考虑提 PR

## 18. 页眉/页脚（abandon）段落翻译后多行布局丢失

- **Commit**: `91c6699`（该补丁代码合并于此批量提交）
- **症状**: JESD238B.01 页眉原文为两行（"JEDEC Standard No. 238B.01" +
  "Page 1"），翻译后合并为一行
- **根因**: `layout_label="abandon"` 的页眉段落被正常翻译，翻译后
  `post_translate_paragraph` 重建 composition 时丢失原始 `PdfLine` 行结构，
  排版引擎将所有文字渲染在一行
- **修复**（`il_translator.py` + `il_translator_llm_only.py`）:
  两条翻译路径均跳过 `layout_label="abandon"` 段落，保持原文 composition
  和行结构 passthrough
- **验证**: 翻译验证通过，保持原文样式
- **上游价值**: 属上游普适缺陷（页眉/页脚翻译后布局丢失），可考虑提 PR

## 19. 排版引擎不避让图片，译文覆盖作者照片

- **Commit**: `91c6699`（该补丁代码合并于此批量提交）
- **症状**: Evolution of GPU 第9-10页作者简介文字渲染在作者照片上方，
  文字压住图片
- **根因**: 原文段落 box 包含照片区域（原文文字绕排照片），排版引擎在
  段落 box 内渲染译文时不检测图片位置，直接覆盖。`get_max_right_space`/
  `get_max_bottom_space` 仅在扩容时检查图片，初始渲染不检查
- **修复**（`typesetting.py` `render_page`）:
  在段落位置调整后、渲染前，检测每个段落 box 与 `pdf_form`/`pdf_figure`
  的重叠。若图片在段落上半部，缩段落 y2 至图片底部；若在下半部，缩段落 y
  至图片顶部。简化处理（不支持文字绕排），但避免直接覆盖
- **验证**: 待翻译验证
- **上游价值**: 属上游普适缺陷（任何文字与图片重叠的布局），可考虑提 PR

## 20. 参考文献检测扩展：支持无编号格式（arXiv 论文）

- **Commit**: `91c6699`（该补丁代码合并于此批量提交）
- **症状**: LightRAG 论文（arXiv 2410.05779v3）第11-12页参考文献被翻译，
  未保持原文。该论文参考文献条目以作者名开头（如 "Yichuan Li, Kaize Ding,
  and Kyumin Lee. Grenade:..."），不以 "[N]" 或 "N." 开头，不匹配
  `_REFERENCE_ENTRY_RE`
- **根因**: `_mark_reference_paragraphs` 仅标记匹配 `_REFERENCE_ENTRY_RE`
  （`^\s*(?:\[\d+\]|\d+\.\s)`）的段落。arXiv 论文常用无编号格式（plainnat/
  apalike 样式），条目以作者名开头，不匹配该正则
- **修复**（`paragraph_finder.py` `_mark_reference_paragraphs`）:
  两阶段策略：
  - 阶段一：用 `_REFERENCE_ENTRY_RE` 匹配 `[N]`/`N.` 格式（IEEE/MDPI/Elsevier），
    行为与旧代码一致
  - 阶段二：仅当阶段一无匹配时（arXiv 无编号格式），回退到标记 REFERENCES 标题
    下方所有段落为 `skip_translate`
  - 例外：`layout_label=="title"` 的新 section 标题 -> 退出参考文献模式；
    `layout_label=="abandon"` 的页眉/页脚 -> 跳过
  - 两阶段策略避免误标记双栏论文中参考文献后的作者简介（如 dally2021：
    IEEE 格式条目在阶段一匹配，阶段二不触发，右栏作者简介不受影响）
  跨页续接：未标记任何条目且无标题时退出模式
- **验证**: 待翻译验证
- **上游价值**: 属上游普适缺陷（任何无编号参考文献格式），可考虑提 PR

## 附：相关但未修改的上游问题

- `warmup()` 一次性预下载全部字体，慢网环境拖慢启动--已在
  PDFMathTranslate-next fork 中禁用调用（`pdf2zh_next/main.py`），
  BabelDOC 本体未改
- ~~`pdf2zh-next` 的 `high_level.py` 未把 `save_auto_extracted_glossary`
  传给 BabelDOC config~~ --已根治：在 PDFMathTranslate-next fork 的
  `high_level.py` 补转发该参数（详见该仓库 LOCAL_PATCHES.md 第 2 条，
  双向验证通过）。BabelDOC 本体无需改动

## 21. 参考文献合并后重新标记 skip_translate + 表格内水平重叠段落合并 + 公式占位符追加 + 水印禁用 + 标题翻译加强

- **Commit**: `91c6699`（该补丁代码合并于此批量提交）
- **症状**:
  1. electronics-14 参考文献34-37页挤在一起被翻译（只有第33页保持原文）
  2. JESD238B.01 表21/23/26/28 第一行表头单元格挤压（如"Bits"拆成"Bi"+"ts"两个段落，排版后"ts"被压扁）
  3. Hopper GPU 第4页图2标题公式丢失（LLM 丢弃 {v3}{v4}）
  4. jun2017 水印出现在文档中间位置
  5. LightRAG 第3页标题 `3 THELIGHTRAG ARCHITECTURE` 未翻译
- **根因**:
  1. `_mark_reference_paragraphs` 在合并步骤前执行，合并后段落结构改变但 skip 标记未更新
  2. 表格内水平拆分的段落（box x 重叠）被排版引擎压缩 y 范围导致挤压
  3. 弱模型 LLM 丢弃公式占位符，之前策略仅警告不恢复
  4. BabelDOC 默认添加水印，水印段落 box 覆盖整页导致排版位置异常
  5. 弱模型将全大写连写词（THELIGHTRAG）当标识符不翻译
- **修复**:
  1. `paragraph_finder.py`：合并步骤后重新调用 `_mark_reference_paragraphs`（重置 `_in_references` 重新检测）
  2. `paragraph_finder.py`：新增 `merge_overlapping_table_cells`，合并表格内同一行 x 重叠的短段落
  3. `il_translator.py`：公式占位符丢失时追加到译文末尾（`translated_text += ph.placeholder`）
  4. `v2_run.py`：默认传 `--watermark-output-mode no_watermark` 禁用水印
  5. `il_translator_llm_only.py`：PROMPT_TEMPLATE 加入"章节标题必须翻译"+ "不要将全大写连写词当代码"规则
- **验证**: 待翻译验证
- **已知未解决**: dally2021 跨页翻译内容重复--弱模型 LLM 在跨页 batch 中重叠内容，提示词约束无效，需引擎级后处理（检测输出重叠度）

## 22. 参考文献跨页标记被 `_in_references = False` 重置破坏（全文翻译复现）

- **Commit**: `91c6699`（该补丁代码合并于此批量提交）
- **症状**: electronics-14 参考文献 33-37 页，全文翻译时只有第 33 页（含
  References 标题）保持原文，34-37 页（无标题续页）又被合并翻译；拆页
  翻译单页时正常。用户反馈"拆页好，全文坏"
- **根因**: 补丁21 的"合并后重新标记"方案在 `process_page` 每次处理页面时
  执行 `self._in_references = False`，然后重新调用 `_mark_reference_paragraphs`。
  该重置破坏了 `_in_references` 的跨页延续状态——34-37 页无 References 标题，
  重置后 `_in_references=False`，`_mark_reference_paragraphs` 直接 return，
  不标记任何段落。同时合并步骤会把参考文献条目合并成超长段落，
  导致阶段一正则只能匹配合并后段落的第一个 `N.`
- **修复**（`paragraph_finder.py`）:
  1. 删除"合并后重新标记"块（含 `self._in_references = False` 重置），
     恢复 `_in_references` 跨页延续
  2. 三个合并函数（`merge_alternating_line_number_paragraphs` /
     `merge_title_caption_and_table_fragments` /
     `merge_mid_sentence_continuation_paragraphs`）均跳过
     `skip_translate=True` 的段落——参考文献条目不参与合并，保持独立、
     skip 标记保留，无需合并后重新标记
- **验证**: 待翻译验证（全文翻译 electronics-14 参考文献页）
- **上游价值**: 属上游普适缺陷（参考文献跨页标记），可考虑提 PR

## 23. 参考文献无编号格式 + 表格行几何兜底 + URL 拆字修复

- **Commit**: `91c6699`（该补丁代码合并于此批量提交）
- **症状**（Qwen 模型全文翻译复现）:
  1. electronics-14 第1页版权声明 URL 被 LLM 拆成逐字符（"h\nt\nt\nt\np\ns\n://"）
  2. 部分文档参考文献无编号开头（既非 [N]/N. 也非 arXiv 作者名），漏标
  3. JESD238B.01 表4/6/10 第一行数值单元格（2Gb/4Gb/6Gb/8Gb）挤到同一格
     ——拆页翻译正常、全文翻译复现
- **根因**:
  1. LLM 输出时把长 URL 逐字符换行拆开
  2. 参考文献标记两阶段策略（阶段二仅在"阶段一无匹配"触发），混合格式
     （同一章节既有编号又有无编号条目）时无编号条目漏标
  3. `in_table_layout` 依赖版面模型 table 框，全文翻译时版面模型对某些页
     table 检测不准确，导致漏标、表格单元格被误合并
- **修复**:
  1. `il_translator.py` `post_translate_paragraph`：新增 URL 修复正则，
     合并被拆成逐字符的 http/https 前缀
  2. `paragraph_finder.py` `_mark_reference_paragraphs`：重构为直接标记
     标题下方所有段落（不再依赖 `_REFERENCE_ENTRY_RE` 分阶段），覆盖
     [N]/N./无编号/混合格式；排除 title（新章节）、abandon（页眉/页脚）、
     作者简介（连续大写字母开头如 "WILLIAM J. DALLY is..."）
  3. `paragraph_finder.py`：新增 `_mark_table_row_paragraphs` 几何兜底——
     同一行 >= 3 个短段落（< 30 字符）识别为表格行，补充 in_table_layout
     标记（不依赖版面模型 table 框）
- **验证**: 待翻译验证
- **上游价值**: 属上游普适缺陷（无编号参考文献、表格漏标、URL 拆字），可提 PR

## 24. 参考文献标题上方的 section 标题误触发"退出参考文献模式"，破坏跨页延续

- **Commit**: `91c6699`（该补丁代码合并于此批量提交）
- **症状**: electronics-14 参考文献 34-37 页被合并翻译（只有第 33 页含
  References 标题的保持原文）；拆页单页翻译正常，全文/跨页翻译复现。用户
  反馈"第 33 页正常，34-37 页合并翻译"。
- **根因**: `_mark_reference_paragraphs` 的标记循环中，"遇到新的 section
  标题（`layout_label=="title"`）退出参考文献模式"的检查位于
  `_below_header` 判断**之前**。参考文献章节**上方**的正文小节标题
  （如 electronics-14 第 33 页的 "Structural Design."、"Integrated
  Perspective."）也满足 `layout_label=="title"`，被误判为"References
  之后的新章节"，将 `self._in_references` 复位为 `False`。由于该页恰是
  含 References 标题的页，复位后第 34-37 页（无标题续页）进入时
  `_in_references=False`，直接 return，不标记任何段落，参考文献被当正文
  合并翻译。
- **修复**（`paragraph_finder.py` `_mark_reference_paragraphs`）:
  将 `if not _below_header(para): continue` 提前到 `layout_label=="title"`
  检查**之前**。这样：
  - 参考文献标题**上方**的段落（含 section 标题）先被 `_below_header`
    排除，不触发"退出参考文献模式"；
  - 仅当标题位于 References 标题**下方**（视觉下方，y<=header_y，即真正
    的"参考文献之后的新章节"）时才退出模式；
  - 跨页续接页（`found_header=False` 时 `_below_header` 恒 True）行为不变，
    新的 section 标题仍能正常退出参考文献模式。
- **验证**: electronics-14 `--split --pages 33-37` 重译，dbg 确认
  `_in_references` 在 33-37 全部保持 True，34-37 页参考文献均保持原文
  （不翻译、不合并）。第 33 页 `exited` 由 True 变为 False。
- **上游价值**: 属上游普适缺陷（任何"参考文献标题上方含 section 标题"的
  双栏/单栏论文），可提 PR

## 25. LINE_BREAK_REGEX 未转义连字符构成字符范围，误吞 . / : 等标点，导致 URL 逐字符折行

- **Commit**: `91c6699`（该补丁代码合并于此批量提交）
- **症状**: electronics-14 第 1 页版权声明长 URL（如
  `https://creativecommons.org/licenses/by/4.0/`）在窄栏中被**逐字符拆到
  单独一行**（渲染为 "h/t/t/p/s/:///creativecommons..."），排版极乱。
  补丁 23 的 URL 修复正则无效（LLM 输出的 URL 本就连贯无换行，问题出在
  排版阶段而非翻译阶段）。
- **根因**: `typesetting.py` 的 `LINE_BREAK_REGEX`（用于判断"不可断行"的
  单词字符）中，字面连字符写成未转义的 `r"-"`，且位于 `r"'"` 与
  `r"·"`（U+00B7）之间。在正则字符类中，未转义的 `-` 与前后字符构成范围
  `'-·`（U+0027 至 U+00B7），把 `.`（U+002E）、`/`（U+002F）、`:`
  （U+003A）及 `@` 等标点一并纳入"不可断行"集合。长 URL 整串被视为一个
  不可断行的单词，`_get_width_before_next_break_point` 返回整串宽度，超过
  窄栏宽度后每个字符都被迫换行到单独一行。
- **修复**（`typesetting.py`）: `r"-"` 改为 `r"\-"`（转义连字符）。这样
  `-` 仍为不可断行（语义不变），但不再与 `·` 构成范围，`.` `/` `:` `@`
  恢复为可断行标点，URL 可在 `/` 与 `.` 处自然折行。
- **验证**: electronics-14 `--split --pages 1` 重译，版权 URL 由逐字符拆分
  变为按 `/`/`.` 断行（`https://creativecommons.org/licenses/by/4.0/`
  在 `/` 后换行）。
- **上游价值**: 属上游普适缺陷（字符类中未转义 `-` 构成意外范围，影响
  URL/文件路径等长无空格串的断行），可提 PR

## 26. `--debug` 模式下 write_json 用 orjson 一次性序列化整篇 IL 触发 MemoryError

- **Commit**: `91c6699`（该补丁代码合并于此批量提交）
- **症状**: 对密集大文档（JESD238B.01）执行 `--debug` 时，在
  `_do_translate_single` 的 `xml_converter.write_json(...)` 处抛
  `MemoryError` 直接中断整个翻译。命令行：
  `run.cmd --v2 JESD238B.01.pdf -p 1-50 --split --debug -o ...`
- **根因**: `xml_converter.py` 的 `write_json` 用
  `orjson.dumps(document, option=OPT_INDENT_2, ...).decode()` **一次性在内存
  中构建完整 JSON 字符串**再写入文件。IL 对象树解析阶段已占用 ~1.5GB 内存，
  `OPT_INDENT_2` 又把 JSON 体积膨胀数倍（50 页密集规范的单份 debug JSON 达
  120-170MB，整串字符串再加缩进可超 500MB）。在既有对象树之上再分配整串
  JSON 字符串即超内存上限 → MemoryError。该 `write_json` 在 debug 管线中被
  调用 ~8 次（create_il/detect_scanned/layout/paragraph_finder/styles/
  il_translated/add_debug/typsetting），任一处崩溃都会中断翻译。
- **修复**（`xml_converter.py`）:
  - 新增 `_json_default`：用 `dataclasses.is_dataclass` + `dataclasses.fields`
    展开 IL 的 `@dataclass(slots=True)` 对象（slots 无 `__dict__`，不能用
    `__dict__`），并处理 `LazyPassthroughInstruction.materialize()`。
  - `write_json` 改用标准库 `json.dump(...)` **流式写入文件**——`iterencode`
    逐块产出 JSON，不再在内存中构建整串。内存占用仅与对象树本身相当，不再
    叠加整串 JSON。
  - 序列化异常时 `except Exception` 记录 `logger.warning` 并跳过该次 debug
    转储，**不再中断翻译**（debug 转储仅为诊断用，失败不应中止主流程）。
  - `to_json` 同步改为 `json.dumps` + `_json_default`；移除不再使用的
    `orjson` 依赖。
- **验证**: JESD238B.01 `-p 1-50 --split --debug` 不再报 MemoryError，debug
  管线各阶段 JSON 全部成功写出（create_il=122MB、paragraph_finder=169MB、
  styles_and_formulas=171MB 等）。代价是流式 `json.dump` 比 orjson 慢
  （每 120MB 约 1min），debug 模式本就近重，可接受。
- **上游价值**: 属上游普适缺陷（`--debug` 对密集大文档内存溢出），可提 PR

## 27. `--debug` 模式下 .decompressed.pdf 解压版产物污染输出目录

- **Commit**: `91c6699`（该补丁代码合并于此批量提交）
- **症状**: 使用 `--debug`（尤其配合 `--split`）翻译后，输出目录除了正常产物
  （`.zh.mono.pdf`/`.zh.dual.pdf`/`.zh.glossary.csv`）外，还残留两个
  `*.decompressed.pdf`（mono/dual 各一）解压诊断文件；配合 `--split` 时还带
  `_split_` 前缀，且 `rename_split_outputs` 会跳过 `.decompressed` 不重命名，
  导致输出目录多出 `_split_xxx.decompressed.pdf`。
- **根因**: `pdf_creater.py` 在 debug 模式下把解压版 PDF 直接存到
  `f"{mono_out_path}.decompressed.pdf"` / `f"{dual_out_path}.decompressed.pdf"`
  （即输出目录），与正式产物混在一起。这些是诊断中间文件，不应进输出目录。
- **修复**（`pdf_creater.py`）: 把两处 debug 解压产物改写到
  `translation_config.get_working_file_path(f"{basename}.mono/dual.decompressed.pdf")`
  （工作目录，与 `*.debug.json` 同类），不再进入输出目录。
- **验证**: LightRAG `-p 9 --split --debug` 后输出目录仅含 3 个正式产物 + 正常
  `_bak` 备份，`.decompressed.pdf` 出现在工作目录。
- **上游价值**: 属上游行为偏好（诊断文件进输出目录），可考虑提 PR 或保留本地。

## 28. 正文段落与表格重叠：排版时正文不绕开表格区域

- **Commit**: `91c6699`（该补丁代码合并于此批量提交）
- **症状**: LightRAG 第 9 页第 4.5 节，正文段落（"我们从两个关键角度…"）的译文
  渲染在右侧表格（Figure 2）上方，两者重叠，正文覆盖表格内容。源文档该节为
  "左栏正文 + 右栏表格 + 表格下方通栏续接"的混合布局，正文段落框被版面模型判为
  通栏（x=108-505），导致译文跨到右侧栏盖住表格。
- **根因**: `_layout_typesetting_units` 排版正文时，右缘固定为 `box.x2`，不感知
  表格区域。当正文段落框横跨表格所在栏（版面模型把"左栏正文 + 表格下方通栏续接"
  并为一个通栏段落框）时，译文逐行流到表格上方。`get_max_bottom_space`/
  `get_max_right_space` 只检查 pdf_form/pdf_figure/pdf_curve，不检查表格
  （`in_table_layout` 单元格），且此场景段落并不扩容（框已通栏），故扩容阻挡也
  不生效。
- **修复**（`typesetting.py`）:
  - `_layout_typesetting_units` 新增可选参数 `table_barriers`（`in_table_layout`
    段落的框列表）。排版每个单元前，若当前行 y 区间与某个表格单元格重叠，则把
    该行右缘 `line_right` 收窄到最左的表格单元格左缘；换行判定改用 `line_right`。
    效果：正文行在与表格重叠的 y 区间内绕排到表格左侧，表格上方/下方保持通栏。
  - `_find_optimal_scale_and_layout` 新增 `table_barriers` 参数，从
    `page.pdf_paragraph` 收集 `in_table_layout` 单元格框（非表格段落才收集），
    并透传给 `_layout_typesetting_units`（含去英文换行限制的递归调用）。
  - **表格标题（caption）补入障碍**：布局模型常把表格标题判为 `plain text`
    （而非 `in_table_layout`），正文首行若与其同 y 带且通栏会覆盖标题。故收集
    in_table 单元格后，额外把"与任一 in_table 单元格在 x 上重叠 > 50%、且在 y
    上紧邻（< 6pt）"的段落视为该表格标题，一并纳入 `table_barriers`，使正文
    首行也绕开标题所在列（LightRAG 第9页正文首行覆盖"图：法律数据集…"标题）。
  - 表格自身段落（`in_table_layout=True`）不参与自身绕排，避免影响表格内布局。
- **验证**: LightRAG `-p 9 --split` 重译，正文在表格及表格标题 y 区间（537-624）
  均绕到左栏（x=107-326），表格标题（x=325-496）与正文首行（x=107-323）不再重叠，
  表格上方/下方通栏。回归 `--layer1` 15 通过；electronics-14 参考文献 33-37 页仍
  保持英文原文。
- **上游价值**: 属上游普适缺陷（任何"正文跨表格栏"场景），可考虑提 PR。

## 29. 表格表头"描述"列被误归入 table_caption，导致表头错位（Bit描述/描述列空白）

- **Commit**: `064295f`
- **症状**: JESD238B.01 多个表格（表11/18/21/23/26/27/28/29/31）的**表头行**
  （Field/Bits/Description/Notes）译文错位：表头第2列（Bits）内容变成"Bit描述"
  （Bits 与 Description 挤在一起），第3列（描述）空白。同一文档中有的表格表头
  正常，有的异常，取决于 layout 模型生成的标题框与"描述"列是否在边界上相交。
- **根因**: `paragraph_finder` 分组时用 `get_character_layout`（layout_helper.py）
  决定每个字符归属哪个布局框。该函数找出所有与字符有交集（IoU>0）的布局框后，
  按 `(优先级, -IoU)` 排序并返回第一个。`table_caption`（表格标题）优先级为 8
  （很高），`fallback_line`（表格单元格）优先级为 725（几乎最低）。当表格标题框
  （table_caption）与表头"描述"列字符在**边界上轻微相交**（IoU 极小，如标题框底
  与"描述"字符顶在 y 边界相交 1pt）时，因 table_caption 优先级高，"描述"字符被误
  归入 table_caption，被当作标题处理：label 变 table_caption、box 变成标题框范围
  （如 x=216-395），与 Bits 重叠成"Bit描述"，描述列空白。
- **修复**（`layout_helper.py` `get_character_layout`）:
  在选择布局时，优先看**字符中心点**是否落在某布局框内：只有中心点真正位于某框
  内才按优先级（+IoU）决定归属；若中心点不落在任何框内（仅边缘相交），改按 IoU
  选字符大部分所在的框。这样高优先级布局（table_caption）不能因边缘微小重叠而吞
  掉相邻表格单元格字符。对正常情况（字符中心明确在某个框内）行为不变。
- **验证**: JESD238B.01 `-p 32-35 --split` 重译，表 11/18 等表头由"Bit描述"修复为
  "字段/Bits/描述/注释"4 列正常；数据行列不合并。LightRAG `-p 9 --split` 表格绕排
  布局无回归。`--layer1` 15 通过。
- **上游价值**: 属上游普适缺陷（高优先级标题布局因边缘 IoU 吞相邻单元格字符），
  可考虑提 PR。

## 30. 表格相邻列被 fallback_line 聚类合并进同一 cell（WDQS Phase 与 DERR0 挤在一格）

- **Commit**: `f812808`
- **症状**: JESD238B.01 表 28（"表8 — 相位检测器和DERR信号行为"）第 1 行第 2、3 列
  错位：原文第 2 列 `WDQS Phase`、第 3 列 `DERR0`/`DERR1`，译文第 2 列变成
  `WDQS 相位 DERR0`（DERR0 被并进第 2 列），第 3 列只剩 `DERR1`。用上一版修复
  （补丁 29）后仍复现。
- **根因**: DocLayout 对表 28 只检测出 `table` 框，未检测出任何 `table_cell`，表格
  单元格全靠 `fallback_line` 兜底生成（`layout_parser.py` 的
  `generate_fallback_line_layout_for_page`）。兜底聚类用 `_cluster_by_axis`
  （extract_char.py），其 `LINE_CLUSTERING_EPS_MULTIPLIER = 3.5`，DBSCAN 的
  `eps = 平均字符宽 × 3.5 ≈ 25pt`。表 28 第 4 行 `WDQS Phase`（x 至 322）与 `DERR0`
  （x 自 332）字符中心距仅约 12pt < eps，被并进同一 cluster → 生成一个横跨第 2、3
  列的 cell（x=261.1-367.8）。此时根本没有"第 3 列"的独立布局框，补丁 29 的
  `get_character_layout`（在既有框之间做归属选择）无从拆分，故仍复现。
- **修复**（`layout_parser.py` `generate_fallback_line_layout_for_page`）:
  生成 fallback_line 时，对**完全落在某 table 框内**（x、y 均被包含）的 cluster，用
  该表格内所有行的 x 起点作为候选**列边界**集合；若 cluster 内部存在**无字符填充的
  x 空隙**（>2pt）且**空隙右侧块起点命中列边界**（±1.5pt），则在此处把 cluster 拆成
  多个 fallback_line，使相邻列内容各自独立。
  - `_assign_clusters_to_tables`：判断 cluster 归属的 table 框（需 x、y 均完整包含，
    排除表格框外的正文）。
  - `_collect_table_col_starts`：收集各表格内行的 x 起点作为列边界候选。
  - `_split_table_line_chars`：按"空隙 + 右侧块起点命中列边界"拆分 cluster 字符。
- **误拆风险评估**: 扫描 4 份文档（hopper/dally/jun2017/JESD）所有"位于表格框内"的
  cell 内部 x 空隙分布。纯按"空隙>2pt"会误拆 18.8%（长单词如 `hiddensize`、正文句子
  词间距）。加入"空隙右侧块起点命中列边界"后：hopper 长单词（右侧块起点 351/338/343
  不对齐列起点）不误拆，JESD 正文列表（y 在表格框外）被排除，LightRAG 表格绕排页
  75 个 cell 全部保持 1 块、0 误拆。正常表格 cell 内部词间距的右侧块起点几乎不可能
  恰好等于列边界起点。
- **验证**: JESD238B.01 `-p 42 --split` 重译，表 28 第 1 行修复为第 2 列 `WDQS相位`
  （x261）、第 3 列 `DERR0`+`DERR1`（x332），列对齐正确且单元格全部翻译。LightRAG
  `-p 9 --split` 表格绕排 75 cell 无回归；`--layer1` 15 通过。
- **上游价值**: 属上游普适缺陷（表格 cell 检测缺失时，fallback_line 兜底聚类把相邻
  列字符合并），可考虑提 PR。

## 31. 图表标题/目录页标题分隔符被 LLM 改写，导致同文档内不统一

- **Commit**: `be5531a`
- **症状**: JESD238B.01 正文图表标题与目录页（List of Tables/Figures）标题段，原文
  分隔符统一为 em-dash `—`/en-dash `–`（如 `Table 1 – Single Channel`），译文里被
  LLM 不稳定地改写为 `.`、`。`、`：` 等（如 `表1. 单通道信号计数`、`图39。时钟至WDQS`），
  同文档内分隔符不统一。跨引擎都存在（免费引擎 0827 有 90 处 `。`，Qwen 更多），
  只是概率不同，非引擎差异。
- **根因**: 正文图表标题（figure_caption/table_caption）与目录页标题段
  （toc_role="title"）都是普通段落，整体送 LLM 翻译（toc_processor 只剥离
  toc_role="layout" 的点线/页码，toc_role="title" 保留 `Table N <sep> Title` 走
  il_translator 正常流程）。LLM 翻译时把标题与编号间的分隔符随意改写。
- **修复**（`il_translator.py`）:
  1. 模块级正则 `_TOC_TITLE_SEP_RE` 匹配 `^(Figure|Table)\s+\d+\s*([—–\-.:,])\s+`
     开头的段落（单 composition 分支，即 `get_translate_input` 中
     `len(pdf_paragraph_composition)==1` 处），把分隔符替换为受保护占位符 `{vSEP}`，
     并记录原文分隔符到 `translate_input.toc_sep`。
  2. `translate_paragraph` 在 `parse_translate_output` 之前恢复：若译文中含
     `{vSEP}` 则 replace 回原文 sep；若 LLM 丢弃占位符，用兜底正则
     `_TOC_TITLE_SEP_FIX_RE`（`((?:图|表)\s*\d+)\s*[.。:：、,，·•]`）修正为原文 sep。
  3. 注意：兜底正则**不能用 lookbehind**（Python re 不支持可变宽度 lookbehind，
     会运行时报错 `re.error: look-behind requires fixed-width pattern`），改用捕获组
     group(1) 保留"图/表+编号"。
- **验证**: 正则单测通过（匹配保护 + LLM 保留占位符恢复 + LLM 丢弃占位符兜底修正）。
  真实翻译验证因环境内存不足（`ONNXRuntimeError Failed to allocate memory`，机器同时
  跑多个翻译进程）未完成，待验证。
- **上游价值**: 属上游普适缺陷（任何文档图表标题分隔符被 LLM 改写），可考虑提 PR。
- **注意**: 与 `pdf2zh-domain` 的 common_rules.md 原"图表标题编号后用英文句点 `表1.`"
  规则冲突，已在 common_rules.md 同步改为"严格跟随原文分隔符"（引擎层保护优先级更高，
  提示词规则仅为兜底）。

## 32. LLM-only 批量翻译路径无段落级缓存，中断/超时后需全文重翻

- **Commit**: `b0e8685`
- **症状**: 走 LLM-only 路径（`il_translator_llm_only.py`，LLM 批量翻译）翻译整篇大文档时，
  每跑一次所有段落都要重新调用 LLM；中途超时/被杀（如 Qwen 超时被 kill）后，已翻译内容全丢，
  重跑需从头再翻，耗时且浪费 token。传统路径（`il_translator.py`）有缓存，LLM-only 路径原本没有。
- **根因**: `ILTranslatorLLMOnly.translate_paragraph` 没有复用翻译缓存，翻译前不查缓存、
  翻译后不落库。
- **修复**（`il_translator_llm_only.py` `translate_paragraph`）:
  1. 翻译前按 (engine, params, original_text) 查 SQLite `cache.v1.db`，命中则跳过该段；
  2. 翻译后写入缓存落库；全部命中则跳过 LLM 请求；fresh id 重映射。
- **验证**: 段落级缓存生效，重跑只翻译未缓存部分（断点续传）。
- **上游价值**: 属通用增强（大文档断点续传），可考虑提 PR。

## 33. 排版修复批次：列表分段 / 嵌套碎片合并 / 句中连词续接 / 下标续接 / 表标题字号

- **Commit**: `b0e8685`
- **症状**: 多类排版问题（`paragraph_finder.py` + `typesetting.py`）：
  - 有序/无序列表项被并成一行，`1.2.3.` 编号与多 bullet 项不独立；
  - 布局模型偶发把父段内字符切成分段，译文叠印；
  - 句中切分后以下段大写/连词续接（如 "static LOW and / HIGH levels"）无法合并；
  - 下标片段（如 "t" + "INIT2"）被切分，独立段渲染时下标起始定位偏移产生大间距；
  - 图表/节标题布局框只包住 cap-height，渲染需字号×line_skip，高度不足导致 scale 被压小、
    标题字号过小。
- **修复**（`paragraph_finder.py`）:
  - `_split_bullets_in_merged_lines`：多 bullet 并行项拆行后再走行级拆段；
    `_is_list_item_start`/`_paragraph_is_list_item_start` 保护列表项独立。
  - `merge_nested_fragment_paragraphs`：合并 box 嵌套/行级交叠的碎片段落（按视觉行重排）。
  - `merge_mid_sentence_continuation_paragraphs`：连词/逗号结尾放宽（`_CONTINUATION_CONJUNCTIONS`），
    允许下段大写续接；排除列表项。
  - `merge_title_caption_and_table_fragments`：下标续接（b 行高 < a×0.75 且紧贴）。
  - （`typesetting.py`）`_expand_caption_box_height` + `_paragraph_font_size`：
    caption/title 类段落 box 高度扩展到单行渲染所需高度，避免 scale 压小。
- **验证**: 列表项独立（第26页 `1.2.3.`、第15页 18 个 bullet 项）、句中续接合并、
  下标间距修复、表7/8标题字号恢复正常。
- **上游价值**: 属上游普适缺陷（布局模型碎片化/标题字号），可考虑提 PR。

## 34. 图表标题字号过小：标题排版受表格绕排收窄右缘，一行放不下导致 scale 被压小

- **Commit**: `7bc39e2`
- **症状**: JESD238B.01 第21页表5、表6，第24页表7、表8 的标题字号明显过小（看不清楚），
  而同一文档其他图表标题（第20页表4、第22页图3、第25页图4/表9、第26页图5）正常。
  表5-8 标题 scale 被压到 0.4-0.5，正常标题 scale=1.0。
- **根因**: 排版引擎的表格绕排逻辑（`_layout_typesetting_units`）把当前行右缘收窄到
  与行 y 区间重叠的表格单元格左缘（`line_right = _tb.box.x`）。表5-8 标题经补丁33的
  `_expand_caption_box_height` 扩展 box 高度后，box 底部下探与下方表格重叠，触发绕排
  收窄：表5 标题行右缘从 392 被收窄到 263，一行放不下译文 -> 换行 -> scale 从 1.0 降到
  0.4。而表4/图3 等因扩展后 box 未触发收窄（或收窄不足），保持 scale=1.0。
- **修复**（`typesetting.py` `_layout_typesetting_units`）:
  表格绕排收窄右缘的逻辑，对标题/图题类段落（`layout_label` 在 `_CAPTION_LIKE_LABELS`，
  即 table_caption/figure_caption 等）**不生效**——标题应横跨表格上方完整宽度居中显示，
  不该绕开下方表格而收窄右缘。正文段落保留绕排收窄逻辑。
- **验证**: JESD238B.01 `-p 20-26 --split --debug` 重译，表5/6/7/8 标题 scale 全部恢复
  scale=1.0（box 宽度也恢复原始值，不再向右扩展），表4/图3/图4/图5/表9 保持 1.0 无回归；
  肉眼确认表5-8 标题字号正常可读。
- **上游价值**: 属上游普适缺陷（表格绕排误伤标题段落），可考虑提 PR。

## 35. 表格相邻列被聚类合并（表36 Level A/B/C 行的第2/3/4列）

- **Commit**: `2d1bbc0`
- **症状**: JESD238B.01 第68页表36 第3/4/5行（Level A/B/C）的第2/3/4列内容被合并成一个
  单元格翻译：原文 `Level A`（第2列）、`RFM is required`（第3列）、`RAAIMT_A`（第4列）
  被并成 `Level A RFM is required RAAIMT_A` 一起翻译。第4行（Default 行）正常（各列独立）。
- **根因**: 表格单元格检测缺失时 fallback_line 兜底聚类（`_cluster_by_axis`）用 DBSCAN
  `eps = 平均字宽 × 3.5`（约 35pt）。Level A/B/C 行的列间空隙约 13-14pt < eps，被聚成
  同一个 cluster（cell）。补丁30 的拆分逻辑 `_split_table_line_chars` 依赖"空隙 > 2pt 且
  右侧块起点命中其他行列起点"，但 Level 行的第3/4列起点（x≈172.8、x≈254.4）与其他行的
  列起点（177.7、259.4）不对齐，col_starts 不命中，拆分失效。且 RFM 与 RAAIMT_A 之间的
  列分隔是一个**宽度 13pt 的异常空格字符**（正常空格约 2.5pt），把空隙填充导致 gap 检测
  不到。
- **修复**（`layout_parser.py` `_split_table_line_chars`）:
  拆分信号扩展为三种（任一命中即拆）：
  1. `gap > 8pt`：无字符填充的大空隙（明显大于正常字间距 ~0）；
  2. 保留原有 `gap > 2pt 且命中 col_starts`；
  3. **异常宽空格**（`curr_uni==' '` 且空格宽度 > 8pt）：列分隔常以异常宽空格
     （制表/填充）占据，识别为列边界。
  阈值 8pt 经验证：表格内正常 cell（Default/RAAIMT/RAAMMT_A 等）内部无 >8pt 的空格或
  空隙，仅 Level A/B/C 行触发拆分，无误拆。
- **验证**: JESD238B.01 `-p 68 --split --debug` 重译，表36 第3/4/5行拆成独立的
  `Level A`/`RFM is required`/`RAAIMT_A` 三列，各自独立翻译；表36 其他 cell 无回归。
- **上游价值**: 属上游普适缺陷（fallback_line 聚类合并相邻列，含异常宽空格），可考虑提 PR。

## 36. merge_mid_sentence 增加括号续接豁免（问题7的部分增强）

- **Commit**: `cb47644`
- **症状**: 问题7（JESD238B 第23页初始化步骤第2小点译文叠印）的碎片中，续接段常以 `)` 等
  右括号开头且 `first_line_indent=True`、与上一段垂直重叠，导致 `merge_mid_sentence_continuation_paragraphs`
  的常规判定（要求小写字母开头 / 无缩进 / 垂直相邻 gap）全部拦截，无法合并。
- **根因**: 布局把含括号内容的句子切成两段（如 `"2. RESET...VDDQ"` + `") before..."`），
  续接段以 `)` 开头（非字母），且布局对续接段误设 `first_line_indent`，垂直重叠（gap 为负）。
- **修复**（`paragraph_finder.py` `merge_mid_sentence_continuation_paragraphs`）:
  新增 `is_paren_cont`：当 a 文本末尾存在未闭合左括号（`(` 数 > `)` 数）且 b 以 `)`/`]` 开头时，
  豁免"小写字母开头"与 `first_line_indent`，并允许 b 与 a 垂直重叠（gap 为负）。
  仅在这种明确括号续接形态下放宽，不影响普通续接判定。
- **验证**: 单测：用真实段落（`"2. RESET...VDDQ"` + `") before..."`，b 带 `first_line_indent=True`）
  跑 `merge_mid_sentence_continuation_paragraphs`，两段成功合并成一段。对正常段落无误合并。
  **注意**: 该修复对**已聚合的完整文本段**有效；但问题7（全篇翻译叠印）的根因是 merge 执行时
  第 2 小点还是未聚合的碎片（见 README 问题7），此改动未能完全解决全篇叠印，作为合理增强保留，
  问题7整体挂起待深排。
- **上游价值**: 属上游普适缺陷（布局把括号内容切段），可考虑提 PR。

## 37. 段落 xobj_id 为 None 时兜底为 0，修复 "Xobj id must be provided when unicode is provided"

- **Commit**: `a090ce4`
- **症状**: 翻译 NB25036-MRCD02_Spec.pdf（421 页）时，在 Typesetting 阶段报错
  `Xobj id must be provided when unicode is provided`，翻译中断。
- **根因**: `create_typesetting_units` 创建译文字符的 `TypesettingUnit` 时用
  `xobj_id=paragraph.xobj_id`（typesetting.py），构造器在 `unicode` 提供时断言
  `xobj_id is not None`。个别段落（如幽灵段/纯译文段）的 `xobj_id` 为 None
  （`PdfParagraph.xobj_id` 默认 None，`update_paragraph_data` 在 chars 为空时提前
  return 不设置 xobj_id），导致触发断言错误。
- **修复**:
  - `typesetting.py` `create_typesetting_units`：开头若 `paragraph.xobj_id is None`
    则兜底为 0（主页面内容，安全）。
  - `paragraph_finder.py` `_group_characters_into_paragraphs`：创建段落时从当前字符
    继承 `xobj_id`（None 则用 0），从源头减少 None。
- **验证**: NB25036-MRCD02_Spec.pdf（421 页）完整翻译通过，不再报错。
- **上游价值**: 属上游普适缺陷（幽灵/纯译文段 xobj_id 缺失），可考虑提 PR。

## 38. 目录页 TOC 增强：孤立 layout 配对 + 相邻 title 合并 + 折行标题 box.x/译文缩进修复

- **Commit**: `0d76b7e`（toc_processor.py + typesetting.py）
- **症状**（NB25036-MRCD02_Spec 目录页全篇翻译 `-p 13-38 --debug`）：
  1. 第8章 `Absolute Maximum Ratings` 译文被拆成"绝对最大值"+"母体评分"（标题横切碎片未合并）
  2. 图/表目录标题重叠、长标题消失（孤立点线 layout 段与标题残片未配对）
  3. 7.22.2~7.29.7 等折行标题译文比单行标题（7.22.1）靠右 2-3 字（首行缩进误判）
- **根因**:
  1. 布局模型把长标题横切成两个**同行 x 相邻**的独立布局块（如 `Absolute Maxi`+`mum Ratings`），
     TOCProcessor 各自识别为 title+layout 条目；且 `mum Ratings` 含 y=593.4 离群字符，
     使 y 比 `Absolute Maxi`(595.8) 小 2.4pt，按 (y,x) 全局排序后顺序颠倒、x 相邻判定失败。
  2. 布局模型把标题与点线完全分离（无完整"标题+点线+页码"结构），点线驱动无法识别，
     标题残片走普通翻译，点线段成为孤立 layout。
  3. `paragraph_finder.py:166-176` 按"首字符 x - box.x > 1"判 `first_line_indent`，
     折行标题 box.x 被续接字符（`cients` x=108）拉低而首字符 x=144，差 36>1 误判 True，
     触发 typesetting 首行缩进 4 空格。
- **修复**:
  - `toc_processor.py`：
    - 新增 `_pair_orphan_layouts`（末尾 post-pass）：为"未进入 _pairs 的孤立点线 layout 段"
      收集同行、从 layout 左缘向左 x 相邻成链的未配对残片段，合并为 title 并配对。
      并把 `_DOTS_PAGE_RE` 判断移到 `_NUMBER_RE` 之前（否则 "......32" 被当纯编号截走）。
    - 新增 `_merge_adjacent_titles`：按 **y 分簇**（容差半行高 `max(line_h*0.5,3.0)`，避免
      整行高把相邻多行链式并簇）+ 簇内按 **x 排序**，合并同行 x 相邻（gap<=2pt）的两个
      已配对 title 段及其 layout 为单条目。修复第8章 `Absolute Maximum Ratings`。
    - `normalize_boxes`：新增**安全 box.x 修复**——把 title 段 box.x 提升到"字符最多的
      y 行簇（标题起始行）"的 min x，**不改变 y**（避免标题偏移）。修复折行标题 box.x
      被续接字符拉低到编号列导致与章节号重合。聚类容差用字符行高（不能用 layout 高度）。
  - `typesetting.py` `_layout_typesetting_units`：对 `toc_role=="title"` 段**跳过
    first_line_indent 首行缩进**（目录标题应顶格），修复折行标题译文偏移。
- **验证**: 用户重跑 `-p 13-38 --debug` 确认：第8章合并为单个标题、译文与 7.22.1 对齐、
  11.1.8 字体/标题下偏/标题过长重合全部正常。自测：图目录/表目录/JESD238B 均 0 误合并。
- **上游价值**: 属上游普适缺陷（布局模型目录碎片化：横切标题/标题与点线分离），可考虑提 PR。

## 39. 目录页 TOC 增强：段首页码残片前移误判修复 + 点线缺失条目页码恢复

- **Commit**: `cbd2b50`（toc_processor.py）
- **症状**（NB25036-MRCD02_Spec 目录页）：
  1. 部分条目的**页码消失**（图118/171、表257/319 等），点线后只有空 debug 框无页码数字
  2. 章节条目**合并**：7.29.2/7.29.3、5.3.1/5.4 标题合并，前一章节页码被吞（如 `...Word3187.29.3`）
- **根因**:
  1. **段首页码残片前移误判**：layout 模型把上一行**跨行条目**的页码残片并进本段段首且无空格
     （如 `313Table 257:...`，313 是上一行 Table 256 的页码，y 坐标不同行）。`_iter_entry_spans`
     的 `leading_page = re.search(r"(\d+)\s*$", text[:first])` 匹配到 313，误判为"前移结构"，
     走 `_iter_entry_spans_shifted` 只取段首残片当页码，**点线后的真实页码(314)被忽略丢失**。
  2. **章节条目点线缺失**：章节号（`7.29.x`/`5.3.x`）不被 `_ENTRY_TOKEN_RE`(Figure/Table)匹配，
     走点线驱动 `_iter_entry_spans_dotted`；但布局模型缺失该条目点线时（`...Word3187.29.3`），
     无点线可切分，上一条目吞掉下一条目、页码错配。
- **修复**:
  - `toc_processor.py`：
    - 新增 `_leading_page_same_line`：用字符 y 坐标验证段首数字(leading_page)是否与标题
      **同一行**（差异 > 半行高即为上一行残片）。不同行则不判定为前移结构，走点线解析，
      真实页码保留。
    - 新增 `_ATTACHED_SEC_RE = (?<![.\d])\d{2,}(?=\d+\.\d+(?:\.\d+)*\s)`：识别"页码紧贴章节号"
      边界（`Word3187.29.3`：318 是上一条目页码，7.29.3 是新条目）。要求页码至少 2 位且
      前面非点号/数字，避免误伤章节号自身（如 `7.29.1` 的 7）。
    - 新增 `_INLINE_ATTACHED_NUM_RE = ^\d+(?=[^\d.\s])`：`_split_entry` 剥离段首裸整数紧贴标题
      的残片（`313Table 257` → `Table 257`）。
  - `_iter_entry_spans` 签名新增 `chars` 参数（供 `_leading_page_same_line` 取 y 坐标）。
- **验证**: 用户重跑确认 7.29.2/7.29.3、5.3.1/5.4 恢复正常、目录整体正常；已恢复页码
  图118=345、图171=407、表319=389、表257=314、表238=299。自测：正常条目（6.5.9）无回归。
  曾误判"段末尾条目页码被吞进下一段(14条)"为待修，实测确认是**假问题**——页码虽归入下一段
  layout 段，但渲染按字符原始 y 坐标放置，正确显示在原文位置（如 Table 256 的 313 显示在其
  下一行开头，与英文原文一致），无需修复。
- **上游价值**: 属上游普适缺陷（布局模型把跨行条目页码残片并进段首、章节条目点线缺失），可考虑提 PR。

## 40. 目录尾页单条点线误退出 + 下划线判公式误判

- **Commit**: `toc_processor.py` + `styles_and_formulas.py`
- **症状**（NB25036-MRCD02_Spec）：
  1. **Page cxliv（表目录尾页）**：只有最后一条目录标签（`Table 332: ... 402`）时，页码没在
     点线后原本位置（点线丢失、页码挤在标题后不右对齐）。
  2. **`ALERT_n` → `ALERT{n}`**：下划线 `_` 被误判为公式占位符，LLM 改写后回填失败。
- **根因**:
  1. **目录尾页单条点线被误判为目录结束**：目录尾页只剩最后一条点线条目（T332）时，
     `_count_dotted_lines=1 < _TOC_MIN_DOTTED_LINES(3)`，toc_processor 在 `process` 提前
     return False 退出目录模式，T332 未拆分，点线/页码未保留。
  2. **下划线 `_` 判公式**：下划线视觉框是字符框底部的细线（y 方向天然不一致，
     `char.box.y > visual_bbox.box.y2`），命中 styles_and_formulas.py box 一致性检查
     （"视觉框和实际框不一致"）被判为公式，生成 `{vN}` 占位符。
- **修复**:
  - `toc_processor.py`：
    - `is_toc_page`：目录续页（prev_in_toc）时点线数门槛从 `_TOC_MIN_DOTTED_LINES(3)` 降到 `1`，
      只要还有 >= 1 条点线即视为目录续页（尾页最后一条）。
    - `process` 主循环：`dotted_lines == 0` 才退出目录模式（原 `<3` 就退出）。
  - `styles_and_formulas.py`：box 一致性检查排除下划线 `_`（`char.char_unicode != "_"`）。
- **验证**: 用户重跑确认 Page cxliv 的 T332 页码 402 与点线同框、右对齐正常（原文 402 被 layout
  横切成 `40`+`2` 两框，译文保持同框结构，视觉对齐，无需额外处理）；`ALERT_n` 显示正常、目录
  其他地方正常、无新问题。子集自测：p37 processed=True(title1+layout1)、p29 正确退出、p24-36 无回归；
  `_` is_formula True→False。
- **上游价值**: 目录尾页单条点线误判 + 下划线判公式误判均属上游普适缺陷，可考虑提 PR。



