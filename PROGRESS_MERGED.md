# 进度记录总档：BabelDOC 公式 / TOC / 布局问题全量排查

> 更新日期：2026-09-15。合并自 `PROGRESS_FORMULA.md`（公式问题）+ `MEMORY_TOC_ISSUE.md`（MRCD02 目录页 TOC 专项）。
> 分支 `fix/toc-formula-digits` 已 fast-forward 合并到 main 并 push 远端 `Niuxiaoer1996/BabelDOC`（提交 `665b8a0`）。
> 本档记录**问题现象、根因、解决方案与当前状态**；代码级修复细节见 `LOCAL_PATCHES.md` 补丁 38/39/40/41/42。

---

## 〇、汇总表（问题状态总览）

### 1. 未解决 / 挂起

| # | 问题 | 说明 | 状态 |
|---|------|------|------|
| U1 | **问题7：正文初始化步骤叠印** | JESD238B.01 第23页，布局把第2小点切碎片，译文叠印。单页/拆页正常，全篇才出现（布局上下文相关）。根因复杂 | 暂缓（非本批范围）|
| U2 | **布局提速**（非 bug） | Layout 阶段占耗时 2/3（CPU 推理慢，无 NVIDIA GPU）| 另开任务待办 |

### 2. 待验证（改动已落地，未用户重跑确认）

> ⚠️ 截至 2026-09-14 收口，**无待验证项**（本批全部经用户重跑确认）。

### 3. 已解决（用户重跑确认）

| # | 问题 | 修复 | 确认 |
|---|------|------|------|
| S1 | 布局①参数解释逐行被合并 | `split_parameter_explanation_paragraphs` + `is_parameter_explanation_line` 标记 | ✅ |
| S2 | 布局③译文字号过小/挤一行（Kikuchi 段）| `il_creater_active.py:1484`/`il_creater.py:1293` Form XObject box 解包 `(x,y,w,h)`→`(x,y,x2,y2)` | ✅ |
| S3 | 布局⑥标题被横向切两块（2.6节/3.3.1）| `merge_title_caption_and_table_fragments` 放宽（`isalpha() or "."`、h_gap 下限 -2.0）| ✅ |
| S4 | MRCD02 目录长标题合并（图57/58、73/74）| `toc_processor.py` inner_tokens 切分 + `_split_entry` 尾部独立页码剥离 | ✅ 页码 114/136 保留 |
| S5 | MRCD02 page37 表7 注释合并（1./2.）| `split_ordered_list_paragraphs`（`_FOOTNOTE_ITEM_RE`）| ✅ |
| S6 | MRCD02 page80 角标（Adc的dc/P1/P2）| `styles_and_formulas.py` 跨 composition 传 prev_char + `_cross_guard`（阈值=字符高度）| ✅ |
| S7 | 中夹英回归（跨 composition 误判公式）| `_cross_guard` 阈值从 `字符高度*1.5`→`字符高度` | ✅ |
| S8 | If D 段拆分（page47 孤立框）| `merge_title_caption_and_table_fragments` 新增 `is_fallback_prefix` | ✅ 完整成句 |
| S9 | FciYa 段译文截断 | `_is_truncated_translation` → fallback 重译 | ✅ |
| S10 | 图表标题分隔符被 LLM 改写 | `il_translator.py` `{vSEP}` 占位符保护 | ✅ |
| S11 | 目录页 TOC 全量（前移结构/点线驱动/页码恢复/标题切分/尾页单条点线/下划线判公式）| toc_processor 补丁38/39/40 + styles 下划线排除 | ✅ |
| S12 | Ctrl+C 取消后进程退不出（线程池堆积）| `il_translator_llm_only.py` 批循环内部补 `raise_if_cancelled()`（含 fallback 提交循环）| ✅ 中途 Ctrl+C 干净退出 |

### 4. 已知问题（登记接受）

| # | 问题 | 说明 | 状态 |
|---|------|------|------|
| K1 | 公式占位符错位 | Qwen 对含多个相同 `{vN}`（如 4 个 `−1`）长段，占位符语义错位，引擎无法判断正确位置 | 接受 |
| K2 | 目录页缩写展开 | `DFE`→`判决反馈均衡 (Decision Feedback Equalization, DFE)`，强模型偶发不遵守，比例低 | 接受 |
| K3 | 化学式翻译 | `Cu`→铜、`SiO`→氧化硅，Qwen 弱模型偶发，靠提示词方案，强模型正常 | 接受 |
| K4 | 弱模型自身问题 | `39]` 重复、`−2→−1`、`◦C` 重复、µ/µm 偶发，引擎级控不住 | 接受 |
| K5 | 前移结构块首页码在左侧（Table257 类）| 布局模型把页码几何放标题左侧，TOCProcessor 无法修正原始几何 | 接受（未复现）|

---

## 一、公式问题的根因

弱模型（SiliconFlowFree）不可靠地处理 `{vN}` 公式占位符：丢弃、改写为 `<sub>/<sup>`、或自造正文内容。
原代码"追加到末尾"兜底把丢弃的公式塞到段尾 → 符号/上下标跑到段尾；且 `µ→×`、`×→·` 是目标字体映射错误。

**结论**：公式问题本质是**弱模型不可控**（丢弃/改写占位符）+ **目标字体映射错误**。强模型（Qwen3.8）明显更好。

## 二、已落地修复（BabelDOC，提交 `665b8a0`）

### 1) 公式分类（`styles_and_formulas.py`）
- `_PLAIN_INLINE_CHAR_RE = re.compile(r"[0-9+\]\[=]")`：数字、`+`、`[`、`]`、`=` 判为普通文本。
  - ⚠️ **`× · − < > ~ ± µ °` 已从该集合移除（回退）**——转文本会在目标 CJK 字体映射成错误字形（µ→×、×→·），必须保持公式保原字形。
- 角标识别：`is_corner_mark` 的 **y 偏移检测**（`_CORNER_Y_RAISE_PT=2.0`），识别基线抬高/降低的上下标。
- 安全字符压制：普通字符命中时置 `is_formula_start/middle` False 并压制公式字体触发；强信号（公式字体/vertical/box 错位）不受影响。

### 2) 回填（`il_translator.py`）
- `_recover_missing_formula_placeholders`：LLM 丢弃的公式占位符**按原文顺序插回"下一个幸存占位符"之前**（富文本 `<style>` + 幸存占位符作锚点），替代 append-to-end。
- `_convert_llm_subsup_to_placeholders`：LLM 自造的 `<sub>X</sub>`/`<sup>X</sup>`（内容 X == 某丢弃公式）**替换回 `{vN}`**，使原位回填成真上下标（修复 SiO₂ 下标）。

### 3) hint 开关生效（`il_translator_llm_only.py`）
- `PROMPT_TEMPLATE` "Do NOT Modify" 段：若输入带 `formula_placeholders_hint`，说明 `{vN}` 对应真实内容，必须原样保留占位符。
- **实测**：hint 对 SiliconFlowFree 无效（弱模型无视），对 Qwen3.8 影响很小，价值有限。

## 三、布局问题（现象 / 根因 / 解决方案）

### 布局① 参数解释逐行被合并 — ✅ 已修复
- **现象**：公式下方 where 后参数解释（每行一个参数）被合并成一个多行段落，LLM 翻译后合并成一行。
- **根因**：版面模型把逐行参数并成一段。
- **方案**：`split_parameter_explanation_paragraphs` 识别"短符号 : 描述"条目行（`_PARAM_ENTRY_RE`）按行拆段，设 `is_parameter_explanation_line=True` 标记，让 merge 跳过（否则字母参数行会被重新合并）。

### 布局③ 译文字号过小 / 挤一行 — ✅ 已修复
- **现象**：第6页 Kikuchi 段（h=96 多行正文）被压成单行小字。
- **根因**：`il_creater_active.py:1484`/`il_creater.py:1293` `on_xobj_form` 把 BBox `[x0,y0,x1,y1]` 误当 `(x,y,w,h)` 解包 → form box 变成几乎整页 → 图片避让压扁正文段。
- **方案**：`(x,y,w,h)` → `(x,y,x2,y2)`。
- **影响面**：只影响 Form/Image XObject box 计算（更准确）；inline image 不受影响。

### 布局⑥ 标题被横向切两块 — ✅ 已修复
- **现象**：第12页 2.6 标题 `2.6. Research...T` + `echnology`；第26页 3.3.1 `3` + `.3.1...`。
- **根因**：布局模型横向切分标题。
- **方案**：`is_title_pair` 的 `first_ch.isalpha()` 放宽为 `isalpha() or == "."`；标题对 h_gap 下限放宽到 -2.0。

### If D 段拆分（page47 孤立框）— ✅ 已修复
- **现象**：`If DCA Scrambling...` 被切成 `If D`（fallback_line）+ `CA Scrambling...`（plain）+ `.`，`If D` 成孤立框。
- **根因**：layout 把一句话横向切成三段，既有水平合并条件都不满足。
- **方案**：`merge_title_caption_and_table_fragments` 新增 `is_fallback_prefix` 合并条件（a 是 fallback_line 短前缀 ≤15 字符、b 是 plain text ≤300、同 xobj/同行/紧贴右侧/非句末）。
- **性质**：既有问题（修复公式问题之前就有），非化学式回归。

## 四、MRCD02 目录页 TOC 专项

### 现象与根因
- 全篇翻译（不带 `--split`）目录页异常：图/表目录条目不拆分、重叠、合并错乱；拆分翻译时基本正常。
- **根因（布局上下文效应）**：布局模型对目录页切分碎片化。物理页25（图目录）是 **4 个合并成一大段的目录块**，结构为 `[页码][Figure N: 标题][点线][页码][Figure N+1]...`，页码被剥离并前移到标题前。

### TOCProcessor 关键逻辑（toc_processor.py）
- `_count_dotted_lines`：按点线 run 计数，修复目录尾页误判。
- `is_toc_page`：点线行数 + marker + `prev_in_toc` 判定。
- `process`：维护跨页状态 `_toc_state["in_toc"]`。
- `_is_continuation_candidate`：同列起始（x 差 ≤2）才识别续接。

### 解决方案（补丁38/39/40 + 本批）
1. **重写 `_iter_entry_spans`/`_split_entry`/`process`**：前移结构 + 点线驱动切分。
2. **`_count_dotted_lines` 改 run 计数**：修复目录尾页误判退出。
3. **`_filter_title_outliers`**：剔除 title 段离群残留字符。
4. **点线驱动兜底**：长标题/末尾条目标题整行丢失修复。
5. **标题横切碎片合并**（`_merge_left_title_fragment`/`_pair_orphan_layouts`/`_merge_adjacent_titles`）：修复第8章 Absolute Maximum Ratings 等。
6. **`normalize_boxes` 安全 box.x 修复**：折行标题 box.x 被续接字符拉低问题。
7. **补丁39**（`cbd2b50`）：段首页码残片前移误判修复 + 点线缺失条目页码恢复（图118=345、图171=407、表319=389、表257=314、表238=299）。
8. **补丁40**：目录尾页单条点线误退出 + 下划线 `_` 判公式误判排除。
9. **本批**：长标题 inner_tokens 切分 + `_split_entry` 尾部独立页码剥离（图57/58、73/74 页码 114/136 保留）。

### 已解决项总表
页码完全不见、章节目录长标题消失、标题横向分割、标题过长/与章节号重合、回归（11.1.8）、译文偏移（首行缩进）、章节合并（7.29.2/7.29.3、5.3.1/5.4）——全部已修复并经用户重跑确认。形态B（段末尾条目页码）确认为假问题。

### 已知局限（已接受）
- **前移结构块首页码在左侧**（Table257 类）：布局模型把个别条目的页码几何放到标题左侧，TOCProcessor 无法修正原始几何。未复现异常，记录接受。

## 五、page37 / page80 分析

- **page37**：表7 注释（1./2.）被合并成一个 table_footnote 段 → `split_ordered_list_paragraphs` 按 `_FOOTNOTE_ITEM_RE`（`^\d+\.`）拆分。✅
- **page80**：CTLE 参数解释 `Adc` 的 `dc`、`P1`/`P2` 的 `1`/`2` 应渲染为下标 → 根因是 A 与 dc 被 layout 拆成不同 composition，dc 是 line 首字符 previous_char=None 无法比较 → 跨 composition 传 prev_char + `_cross_guard`。✅ 用户确认下标正常。

## 六、化学式程序化保护方案 —— 已完全回退

- 曾尝试 `_protect_chemical_formulas`（把化学式包成 `{vN}`）+ 元素白名单 + parse 富文本内嵌占位符回填。
- **回退原因**：① 大量段落 composition 为空导致译文空白；② 白名单误判（`Co-Wafer`/`BeO`/`DDR5MRCD02` 等）；③ 富文本内化学式占位符无法可靠回填。
- **结论**：回到提示词方案（common_rules.md）。图9 `Cu–SiO₂` 强模型正常，Qwen 弱模型偶发翻译成"铜–氧化硅"（已知限制 K3）。
- **保留的改动（非化学式）**：page80 角标修复（跨 composition prev_char + `_cross_guard`），用户确认下标正常。

## 七、FciYa 段公式错位 + 译文截断

- **公式错位**：`{v13}{v16}`（`−1`）被 LLM 放到无公式处。根因：4 个 `−1` 占位符内容完全相同，Qwen 无法区分语义位置 → 已知限制 K1。
- **译文截断**：译文提前停止（非 max_tokens 截断，是 Qwen 主动提前停止，全文 ~2%）→ 已加 `_is_truncated_translation` 检测 → fallback 重译。✅ 用户确认正常。

## 八、恢复工作时的关键命令

- 编译检查：`& "...\.venv\Scripts\python.exe" -m py_compile <file>`
- 用户重跑：`run.cmd --v2 "<pdf>" -o "<out>" --debug [--add-formula-placehold-hint]`

## 九、当前 git 状态

- 分支：`main`（`fix/toc-formula-digits` 已 fast-forward 合并）。
- BabelDOC 本批改动（公式/布局①②③⑥/page80角标/IfD/目录长标题页码/Form XObject box/截断防御/标题分隔符保护）**已清理 debug 插桩、本地提交 `665b8a0`、push 远端 `Niuxiaoer1996/BabelDOC` 的 main**。
- BabelDOC 补丁42（Ctrl+C 取消退不出 + fallback 时间戳）本地提交 `e7dc1ae`，待 push。
- pdf2zh-domain 已提交（本地，未 push）：`7cc7882`（命名+hint 标签 + 提示词化学式/µm 约束）、`b7e2c13`（术语表新增）。

## 十、已知问题接受清单（2026-09-14，用户确认）

K1~K5 全部登记接受（详见开头汇总表「4. 已知问题」）。核心：公式占位符错位、目录页缩写展开、化学式翻译、弱模型自身问题、TOC 前移结构首页码在左侧。
