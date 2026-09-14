# 进度记录：公式问题收口 + 后续任务（压缩上下文后凭此恢复）

> 更新日期：2026-09-14。分支 `fix/toc-formula-digits`。此文件与 `MEMORY_TOC_ISSUE.md` 互补
> （后者是 MRCD02 目录页 TOC 专项，本文档是公式问题的完整进度）。

> **2026-09-14 收口状态**：本批公式/布局/TOC 问题全部验证通过或已登记接受，debug 插桩已清理，
> 已本地提交。详见 §二十四。

## 〇、当前 git 状态
- 分支：`fix/toc-formula-digits`（基于 main）。
- BabelDOC 本批改动（公式/布局①②③⑥/page80角标/IfD/目录长标题页码/Form XObject box/截断防御/
  标题分隔符保护）**已清理 debug 插桩并本地提交**，**未 push**。
- `PROGRESS_FORMULA.md` 已纳入提交。
- pdf2zh-domain 已提交（本地，未 push）：`7cc7882`（命名+hint 标签 + 提示词化学式/µm 约束）、
  `b7e2c13`（术语表新增）。

## 一、公式问题的根因（两类文档共性）
弱模型（SiliconFlowFree）不可靠地处理 `{vN}` 公式占位符：丢弃、改写为 `<sub>/<sup>`、或自造正文内容。
原代码"追加到末尾"兜底把丢弃的公式塞到段尾 → 符号/上下标跑到段尾；且 `µ→×`、`×→·` 是目标字体映射错误。

## 二、已落地修复（BabelDOC，未提交）

### 1) `styles_and_formulas.py` —— 公式分类（②）
- `_PLAIN_INLINE_CHAR_RE = re.compile(r"[0-9+\]\[=]")`：数字、`+`、`[`、`]`、`=` 判为普通文本。
  - ⚠️ **重要：`× · − < > ~ ± µ °` 已从该集合移除（回退）**——它们转文本会在目标 CJK 字体被映射成错误字形（µ→×、×→·），必须保持公式保原字形。用户实测确认。
- `_classify_characters_in_composition`：新增
  - `is_corner_mark` 的 **y 偏移检测**（`_CORNER_Y_RAISE_PT=2.0`）：同字号但基线抬高/降低的上下标（如 `m⁻¹` 的 `⁻` 字号 7.89 恰被 0.79 阈值漏判）也能识别；仅作角标起始触发（`not in_corner_mark_state`），避免回落正文误判。
  - 安全字符压制：`_is_plain_inline_char` 命中且非角标时，`is_formula_start/middle` 置 False，**并压制公式字体触发**（`is_in_formula_font=False`，用于 µ/° 落在 CMSY10/EURM10 数学字体的情况——但 µ° 已回退为公式，此分支对它们不再生效，仅对 ASCII 集合生效）。
  - 强信号（`formula_layout_id`/公式字体/vertical/box 错位）不受压制影响。

### 2) `il_translator.py` —— ③ 回填
- 新增 `_recover_missing_formula_placeholders`：把 LLM 丢弃的公式占位符**按原文顺序插回"下一个幸存占位符"之前**（用富文本 `<style>` 与幸存占位符作锚点），而非一律追加到段尾。替换原 append-to-end 逻辑。
- 新增 `_convert_llm_subsup_to_placeholders`：把 LLM 自造的 `<sub>X</sub>`/`<sup>X</sup>`（内容 X == 某丢弃公式的内容）**替换回 `{vN}` 占位符**，使 `parse_translate_output` 原位回填成真上下标（修复 SiO₂ 下标）。
- 两处均挂在 `post_translate_paragraph`，先 `<sub>/<sup>` 映射、再 ③ 回插（在 `<sub>/<sup>` strip 之前）。

### 3) `il_translator_llm_only.py` —— hint 开关生效
- `PROMPT_TEMPLATE` "Do NOT Modify" 段新增规则：若输入带 `formula_placeholders_hint`，说明 `{vN}` 对应真实内容，必须原样保留占位符、不丢不改写。

## 三、`add_formula_placehold_hint` 开关
- 用法：`run.cmd --v2 paper.pdf --add-formula-placehold-hint`（默认关）。
- 链路已通：run.cmd → v2_run.py（透传 `--` flag）→ pdf2zh2(main.py `--add-formula-placehold-hint`) → TranslationConfig → ILTranslatorLLMOnly。
- 作用：提示词带 `{vN}=内容` 对照表，帮弱模型不丢上下标/符号占位符。
- **实测结论（2026-09）**：hint 对 **SiliconFlowFree 无效**（弱模型无视对照表，占位符丢失位置与
  不开 hint 完全一致）；对 Qwen3.8 影响很小（Qwen3.8 本身就能保住占位符）。**hint 价值有限。**

## 四、自测（临时脚本，位于 `%TEMP%\opencode\`，非仓库文件）
- `test_plain_formula.py`：m⁻¹ 上标保留、[35,36]/RW01[4]/PG70RW60[7]=0 全文本、O2 下表。PASS。
- `test_recover.py`：③ 锚点回插 4 用例。PASS。
- `<sub>/<sup>` 映射、µ/° 公式字体压制：PASS。
- ⚠️ 注意：µ° 转文本的自测曾 PASS，但用户实测 µ→×/×→· 是回归，故已回退。**自测过 ≠ 真实过**（AGENTS.md 铁律）。

## 五、用户反馈时间线（electronics-14-02682-v2.pdf）
- 旧版（无 ③）：符号（µ/×/°）丢段尾。
- round-2（符号转文本版）：µ→×、×→·、−2→−1、SiO₂ 下标丢、`39]` 重复、`◦C` 重复、µm→m。已回退符号转文本。
- round-3（免费引擎 + 修复）：仍有 `39]` 重复、`−2→−1`、`◦C` 重复（弱模型自身，根治不了）。
- **round-4（Qwen3.8，2026-09-13）**：公式效果明显更好，绝大多数公式正常，仅个别偶发偏差：
  ① 第11页一段 `µm` 偶发多一个 `µ`（`40 µm`→`40 µμm`，styles_and_formulas 把 µm 拆成
  `{vN}`(µ)+`m`(文本)，Qwen 偶发把 `m` 写成 `μm`，全文 74 段仅此 1 段）；② 化学式被翻译成中文
  （`Cu`→铜、`SiO2`→氧化硅）。→ **已通过提示词加化学式保留 + µm 占位符规则缓解**（pdf2zh-domain
  `7cc7882`，见 common_rules.md）。

## 六、debug 日志位置
- 工作目录：`C:\Users\Administrator\.config\opencode\skills\pdf2zh-next\babeldoc_cache\working\`
  - `electronics-14-02682-v2\`（含 Qwen3.8 版）、`_split_NB25036-MRCD02_Spec\`
- 已拷到 `%TEMP%\opencode\debug_hint\` / `debug_nohint\`（免费引擎 hint 对比，结论：hint 无效）。
- Qwen3.8 版日志仍在 working 目录。

## 七、待办（按优先级，2026-09-14 收口更新）
> 问题8（上角标间距）已在公式问题中一并解决（角标 y 偏移检测覆盖上/下标），登记为已解决。
0. ~~问题8（上角标间距）~~：**已解决**（随公式问题 `_CORNER_Y_RAISE_PT` y 偏移检测一起收口）。
1. ~~布局①②③~~：**已解决**（①参数解释逐行合并 `split_parameter_explanation_paragraphs`；③ Form
   XObject box 修复 `il_creater_active.py:1484`；⑥标题切块 `merge_title_caption_and_table_fragments`
   放宽）。用户重跑确认正常。
2. ~~MRCD02 目录长标题合并~~：**已解决**（`toc_processor.py` inner_tokens 切分 + 尾部独立页码剥离）。
   用户重跑确认 Figure 57/58、73/74 拆分正常、页码保留。
3. **问题7（正文初始化步骤叠印）**：README 未解决问题7，碎片化叠印，根因复杂。**暂缓**（非本批范围）。
4. **提交清理（已完成）**：清理 `paragraph_finder.py`/`toc_processor.py`/`typesetting.py` debug 插桩；
   补 `LOCAL_PATCHES.md`/`MEMORY_TOC_ISSUE.md`；经用户确认后已本地提交。

## 八、已知根治不了（弱模型自身，引擎级控不住）
- `39]` 重复、`−2→−1`（模型猜错上标数字）、`◦C` 重复。只能靠换更强模型（如 Qwen3.8）缓解。
- 目录页缩写展开（`DFE`→`判决反馈均衡 (Decision Feedback Equalization, DFE)`）致文字变小：
  提示词有规则但强模型偶发不遵守，比例低不影响阅读，**暂可接受**（根治需引擎级后处理）。

## 九、布局问题详细分析（2026-09-13，electronics + MRCD02）

### 布局①（参数解释逐行合并）— ✅ 已修复，用户确认两页正常
- 根因：公式下方 where 后参数解释（每行一个参数）被版面模型合并成一个多行段落，LLM 翻译后合并成一行。
- 修复：`paragraph_finder.py` 新增 `split_parameter_explanation_paragraphs`，识别"短符号 : 描述"条目行
  （`_PARAM_ENTRY_RE`），按行拆成独立段落，并设 `is_parameter_explanation_line=True` 标记；
  `PdfParagraph` 加该字段；`merge_mid_sentence_continuation_paragraphs` 跳过标记段（a/b 判断）。
- 关键：第2页参数符号是字母（V/f）会被 merge 合并回去，第21页希腊字母（∆α）天然不被合并，
  所以必须加标记让 merge 跳过。

### 布局③（译文字号过小/挤一行）— 需调试定位
- 现象：第7页 Kikuchi 段 scale=0.3（195字符挤在 y=651-659 单行）；第28页 3.4.1 段 scale=1.0 但 h=11.4。
- 数据：paragraph_finder 阶段 Kikuchi box y=562.25/y2=658.60(h=96)，typsetting 阶段 box.y=648.96(h=9.6)。
  box.y 在 typsetting 被改到 648.96（≈fallback_line[21] y2=646.96 + 2）。
- 关键线索：Kikuchi box 内部有 6 个 fallback_line 幽灵段（[21]-[26]，debug_id=None 空行）。
- 状态：box.y 变化的确切修改点未锁定（排查了 render_page 避让 1317、_find_optimal_scale_and_layout
  扩展 1129/1148、_expand_caption_box_height，均非直接来源）。**需加调试日志定位**。

### 布局⑥（标题被切两块）— 已定位根因，待修复
- 第12页 2.6 标题被横向切成两个独立段落：[63] `2.6. Research...T` + [64] `echnology`（y 相同）。
- 第26页 3.3.1 序号被切：[86] `3`（fallback_line）+ [87] `.3.1. Importance of CMP...`（title）。
- 根因：布局模型把标题横向/纵向切分，`merge_title_caption_and_table_fragments` 未合并这些 case。

### MRCD02 目录长标题合并 — 已定位根因，待修复
- page13 [2] Figure 73 的 title 末尾含 `...1N Figure 74: CA Pas...`（Figure 74 被并入）；
  [63] Figure 57 的 title 末尾含 `...Frequency Change Figure 58:...`（Figure 58 被并入）。
- 根因：长标题占满整行 + 下一行直接是下一个条目标题（无点线页码分隔），toc_processor 未把
  两个条目的标题分开。

### MRCD02 正文 page37/page80 — 数据不足
- 拆页版（-p 13-38 目录页）不含 page37/page80 正文。需用户重跑这两页获取日志。
- page37 表7注释合并+上标1\2；page80 3.21.1 CTLE 参数解释角标恢复正文大小（Adc的dc、P1/P2的1/2）。

### 公式异常（electronics）
- 第9页[3] Replacing Al2O3：`−1 K −1` 公式占位符被 LLM 改写成 `42`（`10.18 W m42`），公式错乱。
- 第9页[4] Whereas these three：译文被截断/没翻译完（末尾 `{v19}` 后内容缺失）。
- 第10页图9：化学式 Cu-SiO2 仍被 Qwen 翻译成"铜–氧化硅"（提示词约束无效，图题走普通翻译路径）。
- 第11页 cross_page/8/paragraph/1：µm 拆成 `{vN}`(µ)+m，Qwen 偶发把 m 写 μm，致 µμm。
- 根因：LLM 改写/丢弃公式占位符 + 化学式翻译 + µm 拆分联想，均为模型行为。

## 十、布局⑥ + MRCD02 长标题修复（2026-09-13 已实现，自测 PASS）

### 布局⑥（标题切块）— `paragraph_finder.py` `merge_title_caption_and_table_fragments`
- 第12页 2.6 标题：`[63] 2.6. Research...T` + `[64] echnology`，h_gap=-1.19（x 重叠），原条件 `-0.5<=h_gap` 不满足。
- 第26页 3.3.1：`[86] 3` + `[87] .3.1. Importance...`，[87] 首字符 `.` 不满足 `first_ch.isalpha()`。
- 修复：is_title_pair 的 `first_ch.isalpha()` 放宽为 `isalpha() or == "."`；标题对（title/table_caption/figure_caption/fallback_line）
  的 h_gap 下限放宽到 -2.0，普通续接保持 -0.5。
- 自测：两处均合并成功。

### MRCD02 长标题合并 — `toc_processor.py` `_iter_entry_spans_dotted`
- Figure 57/58、73/74 长标题占满整行、无点线页码分隔，`lm=None` 直接 break，两个条目标题合并。
- 修复：`if not lm: break` 处，若 pos 之后仍混入 >=2 个 Figure/Table 条目边界（_ENTRY_TOKEN_RE），按边界切分。
- 自测：Figure 57/58、73/74 均切分成两个独立条目。

## 十一、MRCD02 page37/page80 分析（2026-09-13，拆页版有数据）
> 拆页版 `-p 13-38,51,...` 的 page_number 重新从 0 编号，物理页映射：
> -p 51 → index 26（=物理 page37），-p 94 → index 32（=物理 page80）。

### MRCD02 page37（-p 51, index 26）表7注释合并
- [83] table_footnote 段含 4 行：`1.CA13...`/`shown in Table 6...`（注释1）/`2.MRCD broadcasts...`/`value of CA13...`（注释2）。
- 两条注释（1./2.）被合并成一个段落，翻译时合并成一段。需按有序列表项（1./2.）拆分。
- 另：表格中 1\2 上标被当正文显示（待确认）。

### MRCD02 page80（-p 94, index 32）CTLE 参数解释角标
- [62] `Adc is...`：`A` 字号10.0 y=395.15，`dc` 字号8.0 y=392.65（y 差2.5 > _CORNER_Y_RAISE_PT 2.0，应为下标）。
- [64] `P1 is...`、[65] `P2 is...`：1/2 应为 P 下标。
- **根因**：styles_and_formulas 阶段 `dc` 未被识别为角标公式，作为 same_style 普通文本（字号8）处理，
  翻译后按正文大小渲染。需查 styles_and_formulas 的 is_corner_mark 检测为何未命中（可能需加调试）。

## 十二、新增修复（2026-09-13）

### MRCD02 page37 表7注释拆分 — `paragraph_finder.py` `split_ordered_list_paragraphs`
- 表7下方注释 "1.CA13..." + "2.MRCD..." 被合并成一个 table_footnote 段落，翻译时合并成一段。
- 修复：新增 `split_ordered_list_paragraphs`，对 table_footnote 用宽松正则 `_FOOTNOTE_ITEM_RE`（`^\d+\.`，
  允许 "1.CA13" 无空格）识别列表项起点，按列表项拆分。自测：2 条注释正确拆分。

### 布局③ box debug 文件 bug — `typesetting.py`
- 之前写 `str(wd) / "..."`（字符串 / 报 TypeError），导致 box debug 文件没生成。
- 修复：改用 `Path(wd) / "typesetting_box_debug.txt"`。

### page80 角标（Adc的dc/P1的1）— ✅ 根因定位并修复（2026-09-14）
- **现象**：page80（idx32）CTLE 参数解释 `Adc`/`P1`/`P2` 的 `dc`/`1`/`2` 应渲染为下标，但
  实际按正文大小渲染（styles 阶段 is_corner_mark=None）。
- **数据**：styles 阶段 4CS1v：`A` y=395.15 fs=10.0，`dc` y=392.65 fs=7.9999（y 偏移 2.5>2.0，
  字号比 0.8 略超 0.79 阈值）。dc 本应判为角标，但 is_corner_mark=None。
- **根因**：`_classify_characters_in_composition` 按 composition（=line）逐个处理，A 与 dc 被
  layout 拆成**不同 composition**，dc 是该 line 首字符，previous_char=None，无法与 A 比较 y 偏移/字号。
- **修复**（styles_and_formulas.py）：
  - `_classify_characters_in_composition` 加 `prev_char` 参数（上一 composition 的最后一个字符）
  - 调用处维护 `prev_line_last_char` 跨 composition 传递
  - line 首字符用 prev_char 作参考；加 `_cross_guard`（cross 时要求 y 差 < 字符高度，防跨行误判）

### ⚠️ 中夹英回归（跨 composition 改动引入）— 已修复（2026-09-14）
- **现象**：electronics 大量中夹英（`be occupy`、`ing`、`without`、`loading`、`bonding` 等），
  用户确认是**上次改动引入的回归**（改动前正常）。
- **debug 线索**：这些英文残片用 LaTeX 字体（URWPalladioL-Roma），被**橙色框（公式框）+ 字符级小框**
  框住 → 是**公式误判**（被当作公式占位符渲染），不是翻译不完整。
- **根因**：styles_and_formulas 跨 composition 的 `_cross_guard` 阈值过宽（字符高度*1.5）。
  B32i3 段 `high-performance`（comp[1]）物理在第2行（y=531），prev_char=comp[0]末字符（第1行 y=545），
  y 差 13.9 < 14.9（1.5*字符高度10）误判为同行 → y 偏移 13.9 > 2.0 → 误判为角标 → 整词成公式。
  该误判影响几乎所有段落（每行首字符都可能跨行误判），导致大量英文单词变成公式（中夹英）。
- **修复**：`_cross_guard` 阈值从 `字符高度*1.5` 改为 `字符高度`。
  - dc（真角标，同行）：y 差 2.5 < 高度 8 → 通过 ✓
  - B32i3（跨行）：y 差 13.9 > 高度 10 → 拦截 ✓（不再误判）
- **需重跑 electronics 验证中夹英消失 + MRCD02 验证 page80 角标**。

## 十三、需要用户重跑（2026-09-13）
1. **electronics 第7/28页**：box debug 文件 bug 已修复，需重跑生成 `typesetting_box_debug.txt` 定位布局③。
2. **MRCD02 -p 13-38,51,...**：验证长标题合并 + page37 注释拆分 + page80 角标（用户正在跑）。

## 十四、MRCD02 长标题合并修复未生效（2026-09-13）
- **现象**：用户重跑后 Figure 57/58、73/74 仍合并（paragraph_finder [63] Figure 57 仍含 Figure 58）。
- **矛盾**：`_iter_entry_spans` 单测能正确切分 Figure 57/58（test_toc_real.py），但 paragraph_finder.json 里仍合并。
  说明 `_iter_entry_spans` 切分正确，但 **toc_processor.process 主流程（860行）没正确使用切分结果，或切分后又被合并回去**。
- **页码 114/136 丢失**：Figure 57 标题下一行的页码 114 在 paragraph_finder 阶段就没了（layout 阶段可能丢失）。
  Figure 57 [63] 完整 unicode 仍含 "Figure 58"（paragraph_finder 输出）。
- **下一步**：检查 process 主流程（860-934）为何没按 span 切分，或切分后被合并。
- **备份**：当前 MRCD02 debug 已拷到 `%TEMP%\opencode\mrc_backup_0913\`（9 个 JSON，2026-09-13 19:58）。
  用户将用免费引擎重跑目录页，跑完后对比该备份。
- **时间线澄清**：用户上次 MRCD02 跑（19:58）用的是 `if not lm` 修复（不完整）；我后来加的
  `inner_tokens` 修复（处理 title 内部多 token）在用户跑完之后。用户当前免费引擎重跑会用最新代码。

### 布局③ box debug 覆盖不全（2026-09-13）
- box debug 文件（typesetting_box_debug.txt）只记录了 preprocess_document 阶段（close_box_debug 在
  preprocess 末尾），而 Kikuchi 段的 scale=0.3 发生在 render_page 阶段（retypeset_with_precomputed_scale
  调用 _find_optimal_scale_and_layout）。
- 所以 box debug 只显示 fallback_line 幽灵段（debug_id=None），无 Kikuchi 正文段。
- **修复**：把 close_box_debug 从 preprocess_document 末尾移到 typesetting_document 末尾（render 全部完成后）。
  需重跑 electronics 拿完整 box debug。

### 第9页公式异常（Qwen 版仍存在，2026-09-13）
- [3] Replacing Al2O3：`10.18 W m−1 K−1` 的 `−1 K −1` 被 Qwen 改写成 `42`（`10.18 W m42`）。
- [4] Whereas these three：译文被截断/没翻译完（末尾 `{v19}` 后缺失）。
- **根因**：Qwen 偶发改写公式占位符 + 译文截断（模型行为）。引擎难以根治（`42` 非占位符，
  引擎无法识别是 `−1 K −1` 的错误改写）。登记为已知限制。
- 第10页图9化学式（Cu-SiO2→铜-氧化硅）、第11页µm 多µ 同样为模型行为，提示词约束对 Qwen 部分无效。

## 十五、最新验证（2026-09-13 深夜，免费版 MRCD02 重跑结果）

### 长标题合并：✅ 已修复（免费版实测确认）
- 免费引擎重跑目录页后，paragraph_finder.json 显示：
  - page13 [2] `Figure 73...1N`、[3] `Figure 74...2N` —— 已拆成两个独立 title 段
  - page13 [64] `Figure 57...Change`、[65] `Figure 58...2N Mode` —— 已拆成两个独立 title 段
- 说明 `inner_tokens` 切分修复在真实跑中生效（之前用户跑的 `if not lm` 版本不完整）。

### 页码 114/136 丢失：✅ 已定位根因并修复（2026-09-14）
- **TOCDEBUG 日志定位**（用户重跑目录页生成的 toc_debug.txt）：
  - Figure 73: `span0 title='Figure 73: ... 1N136'` —— **136 被并进了 title**
  - Figure 57: `span0 title='Figure 57: ... Change114'` —— **114 被并进了 title**
- **根因**：inner_tokens 切分（`_iter_entry_spans_dotted`）把 Figure 73/57 与 Figure 74/58
  之间的**独立页码（无点线）136/114** 当成了标题的一部分（`_ENTRY_TOKEN_RE` 只认
  Figure/Table 边界，不认独立数字页码）。
- **修复**：`_split_entry` 新增"尾部独立页码剥离"——当 page_s/page_e 为 None（无点线页码）时，
  检测 title 尾部 2+ 位数字，若其物理 y 与标题首字符**不同行**（独立行页码），剥离到 layout 段。
- 自测：Figure 73 → title `...1N` + layout `136`；Figure 57 → title `...Change` + layout `114`。均 PASS。
- **需用户重跑目录页验证**。

## 十八、布局③根因找到并修复（2026-09-14，重要！）

### 根因：Form/Image XObject 的 box 计算 bug
- **现象**：electronics 第6页 Kikuchi 段（debug_id=hfPtH，原文 h=96 的多行正文）被压缩成
  单行小字（输出 h=4.3），即布局③"译文字号过小/挤一行"。
- **box debug 追踪**（typesetting_box_debug.txt）：
  - `[optimal_scale入口] hfPtH y=562.25 y2=658.60 h=96.35`（原始 box）
  - `[避让下移 new_y=569.108] hfPtH`（段落间避让，box.y 微调）
  - `[optimal_scale入口] hfPtH y=658.10 y2=658.60 h=0.50`（**box 被压成 0.5 高**）
  - `[向下扩展后] hfPtH y=648.96 y2=658.60 h=9.64`（最终，scale 缩到极小）
- **直接原因**：图片避让逻辑（typesetting.py:1381-1419）检测到 Kikuchi 段与 form[0]（Im12）
  重叠，`img_cy(370) <= para_cy(610)` 走 else 分支，`paragraph.box.y = form[0].box.y2 = 658.10`，
  把 Kikuchi 段 box 压成 0.5 高细条 → scale 只能缩到极小。
- **深层根因**：form[0]（xref 172，图7 g007 的 Form XObject）**原始 BBox 是
  `[163.27, 498.993, 430.948, 675.927]`（y=499-676，正常大小）**，但解析后 box 变成
  `y=83.56-658.10`（几乎整页）！
- **bug 位置**：`il_creater_active.py:1484`（及 legacy `il_creater.py:1293`）的 `on_xobj_form`：
  ```python
  (x, y, w, h) = guarded_bbox(bbox)  # 错误：BBox 是 [x0,y0,x1,y1]，不能当 (x,y,w,h)
  bounds = ((x, y), (x + w, y), (x, y + h), (x + w, y + h))  # x1 被当 w，y1 被当 h
  ```
  BBox 的 x1=430.948 被当宽度 w，y1=675.927 被当高度 h，导致：
  - x+w = 163.27+430.948 = 594.22 → 应用 ctm(0.85) 得 box.x2=532.7（错）
  - y+h = 498.993+675.927 = 1174.92 → 应用 ctm 得 box.y2=658.10（错）
  - 而 box.x/box.y 用了 x0/y0 正确（163.27*0.85+27.6=166.4, 498.99*0.85-340.6=83.56）
- **佐证**：pdfinterp.py:320 用 `(x, y, x2, y2) = guarded_bbox(bbox)` 正确解包同一 BBox，
  说明引擎统一按 [x0,y0,x1,y1] 处理，il_creater 是唯一解包错误的。

### 修复（已落地，两处）
- `il_creater_active.py:1484`（实际使用）：`(x,y,w,h)` → `(x,y,x2,y2)`，bounds 用 BBox 四角
- `il_creater.py:1293`（legacy，保险同步）
- 自测验证：修复前 box=(166.39,83.56,532.7,658.1)（覆盖整页），修复后=(166.39,83.56,393.92,233.96)（正常）
- 修复后 form[0] box（y=83-234）与 Kikuchi 段（y=562-658）不重叠，图片避让不再压扁它。

### 影响面评估
- 只影响 Form/Image XObject 的 box 计算（更准确），图片避让/渲染会更合理。
- inline image（bbox=(0,0,1,1)）两种解包结果一致，不破坏。
- 同类 bug 还在 `pdfminer/layout.py:952`（LTFigure.__init__），但图片避让用 PdfForm 不用 LTFigure，
  暂不影响布局③；可作为后续可选修复（需评估影响面）。
- **需用户重跑 electronics 验证布局③**（第6页 Kikuchi 段应恢复正常多行排版）。

## 十九、第8页 FciYa 段公式错位 + 译文截断分析（2026-09-14）
> 用户报"第9页第5段 Whereas these three studies"，实际是 page8 para[72]（debug_id=FciYa）。

### 原文（完整，548 字符）
`Whereas these three studies rely on composition and inter-particle packing to create isotropic 3D
networks, Hong et al. [43] ... the through plane conductivity soared to 5.77 W m−1 K−1, which is
162% higher ... the in-plane value remained modest (2.25 W m−1 K−1).`

### 译文（il_translated）
`...并施加磁场使片晶垂直于{v13}K{v16}面排列，制得了一种{v19}K{v22}，其`

### 两个问题
1. **公式错位**：`{v13}{v16}`（原文 `5.77 W m−1 K−1` 的 `−1` 上标）被 LLM 放到"垂直于...面"
   （原文 `perpendicular to the chip plane`，此处无公式）→ 译文出现 `垂直于 −1 K −1 面`。
   根因：`{v13}/{v16}/{v19}/{v22}` 4 个占位符内容**完全相同（都是 `−1`）**，Qwen3.8 无法区分
   它们的语义位置，导致错位。input 里 `{vN}` 有 6 个（v7=3, v10=4,, v13=−1, v16=−1, v19=−1, v22=−1）。
2. **译文截断**：译文在 `...其`（原文 `for which`）提前停止，`produced an anisotropic underfill for
   which the through plane conductivity soared to...` 后半句完全缺失。
   - input 仅 766 字符（~150 tokens），**远低于 max_tokens=2048** → **不是 max_tokens 截断**
   - llm_translate_trackers：has_error=false（无 API 错误）、placeholder_full_match=true
   - 是 **Qwen3.8 主动提前停止输出**（偶发，全文 469 段约 11 段疑似截断，~2%）

### 结论：两个都是模型行为，引擎难根治
- 占位符错位：引擎无法判断译文里 `{vN}` 的正确位置（语义判断超出引擎能力）
- 译文截断：引擎无法判断"译文应该多长"（无法检测不完整）
- translator.py:335 不检查 finish_reason（若未来 max_tokens 截断也检测不到）

### 已落地防御：译文截断检测 + fallback 重译（il_translator_llm_only.py）
- 新增 `_is_truncated_translation(text)`：检测译文是否被 LLM 提前截断（后半句缺失）
  - 强信号1：以公式占位符 `{vN}` 结尾（占位符后无文字）
  - 强信号2：以"逗号/分号 + 未完成连接词/代词"结尾（`，其`/`，而`/`，以`/`，该`/`，从而`/`，使` 等）
- 在 `post_translate_paragraph` 前调用，检测到截断则 `continue` 走现有 fallback 重译
- 自测 11 用例全过（FciYa 的 `，其` 命中，正常句不误伤）
- **注意**：仅缓解截断；公式占位符**错位**（`−1 K −1` 放到无公式处）无法用此防御修复（语义判断超出引擎）。

### 登记为已知限制（Qwen 模型行为，根治不了）
- Qwen3.8 对**含多个内容相同公式占位符**（如 4 个 `−1`）的长段落：占位符错位 + 提前截断。
- 截断已加引擎防御缓解；占位符错位只能靠换更强模型或接受。

## 二十、当前工作状态 + 下一步（2026-09-14 收口更新）

### 本批改动全部收口（验证通过或登记接受）
1. **MRCD02 长标题页码 114/136 丢失**：✅ **已修复**（TOCDEBUG 定位 + 尾部独立页码剥离）并
   **用户重跑确认**（Figure 57/58、73/74 拆分正常、页码保留）。
2. **布局③（Kikuchi 段被压扁）**：✅ **已修复**（il_creater_active.py Form XObject box 计算 bug），
   **用户重跑已确认恢复正常**（详见 §十八）。
3. **page80 角标（Adc的dc/P1/P2）**：✅ **已修复**（跨 composition 传 prev_char + _cross_guard）并
   **用户重跑确认**（详见 §十二）。
4. **FciYa 段译文截断**：✅ **截断防御已生效**（用户最近一次翻译确认正常）——`_is_truncated_translation`
   → fallback 重译；公式占位符错位（−1 K−1）为已知限制（详见 §十九）。
5. **图表标题分隔符保护（补丁31）**：✅ **用户最近一次翻译确认正常**——同一文档内图表标题分隔符
   统一跟随原文 `—`/`–`，不再被 LLM 随意改写。
6. **提交前清理**：**已完成**——paragraph_finder.py / toc_processor.py / typesetting.py 的 debug 插桩
   已删除；`LOCAL_PATCHES.md` / `MEMORY_TOC_ISSUE.md` 已更新；经用户确认后已本地提交。

### 备份位置
- `%TEMP%\opencode\mrc_backup_0913\`：MRCD02 Qwen 版（9 JSON，旧代码）。
- `%TEMP%\opencode\mrc_backup_free_0913\`：MRCD02 免费版（9 JSON，长标题已修复，页码仍丢）。

### 关键文件
- `BabelDOC/babeldoc/format/pdf/document_il/midend/paragraph_finder.py`：布局①⑥、page37 注释拆分、If D 合并。
- `BabelDOC/babeldoc/format/pdf/document_il/midend/toc_processor.py`：长标题 inner_tokens 切分 + 尾部页码剥离。
- `BabelDOC/babeldoc/format/pdf/document_il/midend/typesetting.py`：布局③相关。
- `BabelDOC/babeldoc/format/pdf/document_il/midend/styles_and_formulas.py`：`_CORNER_Y_RAISE_PT=2.0` 角标检测。
- `BabelDOC/babeldoc/format/pdf/document_il/frontend/il_creater_active.py`（1484）与 `il_creater.py`（1293）：Form XObject box 修复。

## 十七、恢复工作时的关键命令
- 自测：`& "...\.venv\Scripts\python.exe" "%TEMP%\opencode\test_plain_formula.py"`（workdir=BabelDOC）
- 编译检查：`& "...\.venv\Scripts\python.exe" -m py_compile <file>`
- 用户重跑：`run.cmd --v2 "<pdf>" -o "<out>" --debug [--add-formula-placehold-hint]`

## 二十一、化学式程序化保护方案 —— 已完全回退（2026-09-14）

### 背景
- 用户要求"程序化识别并保护化学式"，避免弱模型把 Cu→铜、SiO→氧化硅。
- 尝试方案：`il_translator.py` 的 `_protect_chemical_formulas`（把化学式包成 `{vN}` 公式占位符）+ 元素白名单检测 + parse 富文本内嵌占位符回填。
- **最终失败并完全回退**，因引入大量严重回归。

### 回退原因（多次回归）
1. **大量段落 composition 为空导致译文空白**：终端报错
   `ERROR:Unable to export paragraphs that have not yet been formatted: PdfParagraph(... pdf_paragraph_composition=[])`。
   根因：parse 富文本内嵌占位符拆分改动（`parse_translate_output`）把 composition 弄空，typesetting 无法排版 → 渲染空白。
2. **白名单仍误判**：`Co-Wafer`→`CoW`、`BeO`、`DDR5MRCD02`、`QSK`、`RW60` 等被当化学式，保护成占位符后回填丢失。
3. **富文本里的化学式占位符无法可靠回填**：图9标题 `Cu–SiO₂` 的 `SiO` 在 `<style id='5'>` 富文本内，parse 后 `{vN}` 残留 UNI 里（`Cu– 2`）。

### 已回退内容（il_translator.py）
- 删除：`_CHEM_*` 常量 + `_is_chemical_formula` + 元素白名单。
- 删除：`_protect_chemical_formulas` 方法。
- 删除：`get_translate_input` 里的调用。
- 回退：`parse_translate_output` 富文本内嵌公式占位符拆分。

### 结论
- **化学式保护回到提示词方案**（common_rules.md 已有化学式保留规则）。图9 `Cu–SiO₂` 强模型正常，Qwen 弱模型偶发翻译成"铜–氧化硅"（已知限制，PROGRESS 已登记）。

### 保留的改动（非化学式）
- **page80 角标修复**（styles_and_formulas.py）：跨 composition 传 prev_char + `_cross_guard` + 移除 y 偏移角标判定的 `not first_is_bullet`。**用户确认 page80 下标正常**。
- 注意：移除 `first_is_bullet` 抑制是全局的，曾怀疑导致空白，但回退化学式后空白消失，说明空白是化学式保护/parse 引入，非角标修复。

## 二十二、If D 段拆分问题 —— 已修复（2026-09-14）

### 现象（MRCD02 page47 最后一段）
- 原文 `If DCA Scrambling is enabled prior to entering HIT-SR mode, see Step 3 in Section 4.6 for detail.`
- 译文被拆成两半：`If D`（英文残留，被单独框在蓝框里）+ `在进入 HIT-SR 模式之前...`。

### 根因
- layout 模型把一句话横向切成三段（同一行 y=213.4）：
  - `If D`（fallback_line，x=72-88）
  - `CA Scrambling...`（plain text，x=88-461）
  - `.`（fallback_line，x=461-463）
- `merge_title_caption_and_table_fragments`（水平合并）条件都不满足：
  - `CA` 首字母大写 → 非 `is_lower_cont`
  - `CA Scrambling` 是 plain text（非 title_like）→ 非 `is_title_pair`
  - 行高比不满足 → 非 `is_subscript_cont`
- 所以 `If D` 未合并，作为独立 fallback_line 段未翻译（太短被跳过），渲染成孤立框。
- **这是既有问题**（用户确认在修复公式问题之前就有），非化学式回归。

### 修复（paragraph_finder.py `merge_title_caption_and_table_fragments`）
- 新增 `is_fallback_prefix` 合并条件：
  - a 是 `fallback_line` 短前缀（unicode ≤15 字符）
  - b 是 `plain text`（unicode ≤300）
  - 同 xobj、不在表格（in_table_layout 已跳过）、同一行（y 中心差 ≤4）、b 紧贴 a 右侧（h_gap -0.5~4）、a 结尾非句末
  - 满足则把 b 并入 a
- **自测 PASS**（真实 MRCD02 数据）：`If D` + `CA Scrambling...` + `.` 合并成完整句 `If DCA Scrambling is enabled prior to entering HIT-SR mode, see Step 3 in Section 4.6 for detail.`，主段保留 nMFw4。
- 保守，误合并风险低（严格条件 + 表格排除）。
- ✅ **用户重跑已确认**：MRCD02 page47 翻译正常、不再拆开、无孤立框。

## 二十三、当前代码状态汇总（2026-09-14，回退后）

| 改动 | 状态 |
|------|------|
| 化学式程序化保护（il_translator.py） | **已完全回退** |
| parse 富文本内嵌占位符拆分 | 已回退 |
| page80 角标修复（styles_and_formulas.py） | 保留，用户确认下标正常 |
| If D fallback 前缀合并（paragraph_finder.py） | **新增**（本次），自测 PASS |

### 用户重跑验证结果（2026-09-14，MRCD02）
- ✅ **MRCD02 page47**：`If DCA Scrambling...` 翻译正常、不再拆开、无孤立框（If D 合并修复生效）。
- ✅ **MRCD02 page80 角标**：`Adc` 的 `dc`、`P1`/`P2` 的 `1`/`2` 下标正常。
- ✅ **无空白段**：回退化学式程序化保护后，composition 为空导致的空白全部消失。
- ⚠️ **已知限制**：化学式翻译问题（Cu→铜、SiO→氧化硅）仍存在，靠提示词方案，强模型正常，Qwen 弱模型偶发翻译——已登记为已知限制，接受。

### 待办（已全部完成）
- ~~更新 LOCAL_PATCHES.md / MEMORY_TOC_ISSUE.md~~：已完成。
- ~~清理 debug 插桩~~：paragraph_finder.py / toc_processor.py / typesetting.py 已清理。
- **已本地提交**（用户确认后）。

### 调试脚本（%TEMP%\opencode\）
- `test_merge_ifd.py`：If D 合并自测（读 MRCD02 paragraph_finder.json，workdir=working\_split_NB25036-MRCD02_Spec）。
- `dump_blank_comps.py` / `dump_ifd_il.py` / `dump_pf_page.py` 等：空白/If D 分析。

## 二十四、最终收口：已知问题接受清单（2026-09-14，用户确认）

### 已登记为"已知问题（接受）"
1. **公式占位符错位**（FciYa 段 `−1 K−1` 放到无公式处）：Qwen 对含多个相同内容公式占位符的长段，
   占位符语义错位，引擎无法判断 `{vN}` 正确位置。**接受**。
2. **目录页缩写展开**（`DFE`→`判决反馈均衡 (Decision Feedback Equalization, DFE)`）：强模型偶发
   不遵守提示词，比例低不影响阅读，根治需引擎级后处理。**接受**。
3. **化学式翻译**（Cu→铜、SiO→氧化硅）：Qwen 弱模型偶发，靠提示词方案，强模型正常。**接受**。
4. **弱模型自身问题**（`39]` 重复、`−2→−1`、`◦C` 重复、µ/µm 偶发）：引擎级控不住，换强模型缓解。**接受**。

### 已确认无异常并接受
5. **已知局限：前移结构块首页码在左侧（覆盖 title，Table257 类）**：用户最近翻译未复现异常，**记录接受**。
   （布局模型把个别条目的页码几何放到标题左侧，TOCProcessor 无法修正原始几何；若后续复现再处理。）

### 已确认解决（用户最近一次翻译验证）
- **FciYa 段译文截断防御**（`_is_truncated_translation` → fallback 重译）：正常。
- **图表标题分隔符保护**（补丁31 `{vSEP}`）：同一文档内标题分隔符统一跟随原文，正常。

### 已提交
- 本批改动（公式修复、布局①②③⑥、page80 角标、If D 合并、目录长标题/页码、Form XObject box、截断防御、
  标题分隔符保护）已清理 debug 插桩后**本地提交**（未 push）。
