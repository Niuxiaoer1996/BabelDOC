# 分式（Fraction）问题调查记录（临时）

> 本文件为 **临时调查记录**，供排查续接用。Page 407/408 完全解决后，
> 统一按规范登记到 `LOCAL_PATCHES.md` / `docs/HISTORY.md` / README 问题总表。
> 涉及 JESD209-5B，页眉页码 407/408 = `_split_JESD209-5B-HJ` 子集的 page[0]/page[1]（仅跑 2 页时）。

## 一、Page 407（已解决=总表 S20；整行大公式方案待办=U11）

### 现象
分式塌陷：分式分子/分母（数学字体）被 `=`/`(`/`)`/`.` 等普通字体符号拆成多个独立公式，
relocate 时水平排列导致塌陷。JESD209 Page 407 有 4 个形态相同的分式（2 个 Accuracy + 2 个 Granularity）。

### 根因
布局模型(doc_layout_model)：
- Accuracy 分式标 `isolate_formula`（整行 passthrough，正常）。
- Granularity 分式标 `plain text`（分子/分母数学字体被 `=`/`(`/`)`/`.` 拆成多个独立公式，
  生成 `{vN}` 后水平塌陷，异常）。

### 当前修复（已落地，用户确认"非常完美，和原文一样对齐"）
`styles_and_formulas.py::_merge_vertical_fractions`（在 process_page_styles 之后执行）：
1. **合并分式**：把垂直相邻（`_is_vertical_fraction_pair`：x 重叠 + y 中心差 0.5*avg_h~2.5*avg_h）
   的分子+分母公式合并成一个 `PdfFormula`，并收集分数线曲线（`ncurve_final=1`）。
2. **等号前后分别合并成小公式**：分式左右两侧同行（`has_y_intersection`）的"公式+文本符号
   （`=`/`.`/`%`）"**分别**合并成 2 个小公式，各自 `y_offset=-(height/2)`，box 中心对齐 current_y。
3. **顺序修复**：`remove_non_formula_lines` 移到 `_merge_vertical_fractions` 之后，避免误删分数线。

### 关键代码位置
- `_merge_vertical_fractions`（styles_and_formulas.py ~line 1360）
- `_is_vertical_fraction_pair`（~line 1337）
- `_is_fraction_symbol_text`（~line 1314）
- `process_page` 顺序：`process_page_styles` → `_remove_orphan_spaces` → `_merge_vertical_fractions`
  → `remove_non_formula_lines`

### 当前方案形态
**分离小公式方案**：分式本身 + 等号前小公式 + 等号后小公式 = 3 个公式，各自 y_offset=-半高。
Page 407 已验证完美（分子中心 202.6 / 分母中心 217.0，与原文一致）。

### 待办（未尝试）
**整行合并成大公式方案**：把分式 + 左右两侧同行公式+符号 **全部合并成一个大 PdfFormula**，
统一 y_offset=-(height/2)。尚未实现。若大公式可行则用之；不行则保留当前分离方案。

### 另确认的 Page 407 问题（本会话新增，用户复查）— 均已解决
1. **`2^16` 渲染成 `2^^16`（双 `^`）**：从最开始就有（与公式位置修正无关）。**已解决**（补丁50，
   `9bb7192`，il_translator.py `post_translate` 清理循环新增规则移除占位符前 LLM 补的文本 `^`）。
2. **最后一行第一个 `2^16` 的 `16` 位置错乱**：`16` 本是 `2` 的上标，但现在 `16` 跑到 `2` 的
   **下方且更远**。**是公式位置修正之后才出现的回归**，根因：孤立空格把上标公式 box 顶部撑高到
   20.3pt，`process_page_offsets` 算 `y_offset` 得 -13.45，上标 `16` 往下掉 13pt。**已解决**（补丁49，
   `111ff85`，styles_and_formulas.py 把 `_remove_orphan_spaces` 提前到 `process_page_offsets` 之前）。

## 二、Page 408（U9）— 未解决，排查中

### 现象（用户肉眼观察）
图235、图236 下方各有两个无序列表项：
- 列表项1：`OSC Match_temp : OSC Match_temp = [tWCK2DQ (T) – tWCKosc (T) – OSC offset_temp ]`
  - **下标问题**：`temp` 被翻译成中文"温度"，`Match_temp` 未作为完整下标保留。
- 列表项2：`tWCKosc(T) : tWCKosc（T）= Run Time/2*Count`
  - **分式问题**：分子 `Run Time` + 分数线被错误前置到最前，`tWCKosc（T）= ` 和分母 `2*Count`
    留在后面，分式被拆开错位。整行还有"位置偏上"问题（可后调）。
- 图236 与图235 同构，只是温度变量 → 电压变量（V）。

**注意**：用户反馈图235"分子分母都在最开始"，图236"分子在前、分母在后"——两者最终 composition
顺序其实相同（分子最前、分母最后），差异原因待查（可能与分式合并/等号合并后的渲染有关）。

### 已确认的机制（debug 日志 + json 分析）

**数据位置**：`babeldoc_cache/working/_split_JESD209-5B-HJ/`
（仅跑 407/408 两页时：page[0]=407, page[1]=408）
- `styles_and_formulas.json`：最终 composition。
- `paragraph_finder.json`：paragraph_finder 阶段 composition。
- `fraction_merge_debug.txt`：SCAN/cand/BREAK/THREAD 日志。

**para88（图235）最终 composition 顺序**（styles_and_formulas.json page[1]）：
```
comp0  TEXT '_'  x=213 y=388.6
comp1  FORMULA 分子'RRRRmRmmD' x=250 y=387.8 ncurve=1   ← 分式分子+分数线
comp2  TEXT 'tWCKosc(T)'  x=112
comp3  TEXT ' '  x=164
comp4  TEXT ':'  x=166
comp5  TEXT '  ' x=169
comp6  FORMULA 'MWCW OMM'  x=180 ncurve=0
comp7  TEXT '(' x=223
comp8  FORMULA 'T' x=226 ncurve=0
comp9  TEXT ') = ' x=233
comp10 FORMULA 分母'W*WCRmC' x=252 y=376.9 ncurve=0   ← 分式分母（独立）
```
→ **分子在 composition 最前（comp1）、分母在最后（comp10），中间隔着 tWCKosc 文本**。

**para103（图236）最终 composition 顺序**：与 para88 同构（comp0=分子、comp1=bullet、comp2=tWCKosc、
...comp10=分母）。

**threading 阶段（paragraph_finder）**：para88（WGpvT）gap_ys=[370.6]，只分出：
```
L0 y=(370.7,392.6) 'tWCKosc(T) : MWCW OMM(T)= RRRmRmmD W*WCRmC'  ← 分子分母在同一行！
L1 y=(354.1,365.2) ' '（孤立空格）
```
para103（uE6u5）同理，分子分母都在 L1 同一行。
→ **threading 阶段分子分母本来在同一行**，分开发生在 collect（`_classify_characters_in_composition`
按字符流顺序：分子 `RRmRmmD` 在分母 `W*WCRmC` 前）及后续。

### 已做的尝试与结论
1. **去掉孤立空格**（已落地）：分子下方 y≈370.7 的孤立空格污染公式 box（高 21.9 → 4.8），
   新增 `_remove_orphan_spaces`（styles_and_formulas.py）在 `_merge_vertical_fractions` 前调用，
   移除与主字符行 y 中心差 >6pt 的空格。**成功**：橙色框变小只包分子。**但分式仍不合并**。
2. **合并仍失败**：`_merge_vertical_fractions` 从分子（comp1）向后扫描遇到 `tWCKosc(T)`（comp2
   普通文本）就 `BREAK`，**找不到分母（comp10）**。日志：`SCAN comp1 ... BREAK comp2 is ordinary TEXT`，
   `merged fractions: 0`。→ 光去空格不够，需解决"跨文本/跨 composition 找分母"。

### 核心根因（当前结论）
**composition 顺序错乱**：分式分子被排到最前、分母被排到最后，中间被 `tWCKosc(T) : ... = `
文本隔开。导致：
- `_merge_vertical_fractions` 无法跨文本合并分子分母。
- 译文里"分子被前置、分母留后"。

**待验证**：为什么图235和图236最终 composition 顺序相同（都是分子最前分母最后），用户却观察到
图235"分子分母都在最前"、图236"分子在前分母在后"。可能与分式/等号左右合并的渲染差异有关。

### 修复方向（两方案）
- **方案A（已开分支实验）**：改 `paragraph_finder.py::_split_paragraph_into_lines`（threading 分行），
  让分式分子分母不被拆成不同行、保持相邻。**风险大**（影响全局分行）。
- **方案B（更安全）**：改 `styles_and_formulas.py::_merge_vertical_fractions`，让它能**跨 composition**
  （跳过中间的普通文本）找到分子分母，合并成一个分式，并处理位置。风险集中在分式处理。

当前在 git 分支 `fix/p408-fraction-line-split`（BabelDOC 仓库）上实验方案A；已加 THREAD/THREAD-LINES
debug 日志确认 threading 行为（发现分子分母本就在同一行，见上）。

### 待办
- [x] **Page 407 回归**（已解决，见上文）：`2^16` → `2^^16`（双 `^`）=补丁50 `9bb7192`；
      最后一行第一个 `2^16` 的 `16` 上标位置错乱=补丁49 `111ff85`。
- [ ] 确认图235 vs 图236 渲染差异根因。
- [ ] 方案A 实验：改 threading 分行，验证分式能否合并且不影响全局；不行则回退方案B。
- [ ] 方案B 兜底：`_merge_vertical_fractions` 跨 composition 合并分子分母。
- [ ] 列表项1 下标问题：`temp` 翻译成"温度"、`Match_temp` 未作整体下标。
- [ ] 整行"位置偏上"问题（可后调）。
- [ ] 408 完全解决后统一更新 LOCAL_PATCHES.md / HISTORY.md / README 问题总表。

### 附：Page 407 vs Page 408 差异总结
- **Page 407**：分式（分子+分母+分数线）原始是**一个公式**，`_merge_vertical_fractions` 只把等号
  等符号并进去，成功。
- **Page 408**：分式原始被拆成**两个公式**（分子+分数线 / 分母），且分子被排到 composition 最前、
  分母最后、中间隔文本，导致无法合并。

---

## 三、续接所需的关键信息（新窗口/新会话先读本节）

### 关键几何数据（Page 408，styles_and_formulas.json page[1] para88）
- **分子 comp1**（去掉孤立空格后）：box=(249.8, 387.8, 285.1, 392.6)，cy≈390.2，h≈4.8，ncurve=1（含分数线）。
- **分母 comp10**：box=(252.4, 376.9, 279.8, 381.6)，cy≈379.2，h≈4.7，ncurve=0。
- **`_is_vertical_fraction_pair(分子, 分母)`**：y_diff=11，avg_h≈4.75，0.5*avg_h=2.4，2.5*avg_h=11.9
  → `2.4 <= 11 <= 11.9` = **True**。即去掉空格后，分子分母**能**被判定为垂直分式。
- **但** `_merge_vertical_fractions` 从分子（comp1）向后扫描，遇到 comp2（`tWCKosc(T)` 普通文本）就
  `BREAK`，**永远不会检查到 comp10（分母）** → `merged fractions: 0`。这是当前唯一的合并障碍。

### 如何运行验证（用户执行）
- 用户会跑 **page407、408 两页**（不是全量 429-439），工作目录 `_split_JESD209-5B-HJ` 会更新为 2 页。
- 关键 debug 日志文件：`babeldoc_cache/working/_split_JESD209-5B-HJ/fraction_merge_debug.txt`
  - `SCAN comp{i}` / `cand comp{j} pair=...` / `BREAK comp{j}`：`_merge_vertical_fractions` 的扫描路径。
  - `THREAD para=...` / `THREAD-LINES`：`_split_paragraph_into_lines` 的 threading 分行结果。
- 分析用 python 脚本读 `styles_and_formulas.json`（page[1]=408）看最终 composition。

### 当前代码改动（BabelDOC 分支 fix/p408-fraction-line-split，未提交）
- `styles_and_formulas.py`：
  - **核心**：`_merge_vertical_fractions`（分式+等号左右合并，Page 407 方案）+ `_is_vertical_fraction_pair`
    + `_is_fraction_symbol_text` + `process_page` 顺序（`_remove_orphan_spaces` → `_merge_vertical_fractions`
    → `remove_non_formula_lines`）。
  - **核心**：`_remove_orphan_spaces`（去掉分子下方孤立空格，box 21.9→4.8）。
  - **方案B（本轮新增）**：`_merge_vertical_fractions` 扫描时跳过"与分子 box 无 x 重叠的侧面普通文本"
    （如 `tWCKosc(T)` 位于分式左侧 x=112，分子 x=250），继续向后找分母，不再直接 BREAK；
     合并时只并入分子+分母公式字符（侧面文本保留原样），删除时只删分母 comp、保留侧面文本；
     等号左右合并增加 x 重叠/紧邻防护，避免把 `tWCKosc(T)` 字母文本误并入分式。
   - **方案B-2（本轮）**：合并收集只并入"与分子同 x 列"的中间成分（否则误吞 `MWCW OMM`/`T`/`:`，
     使合并 box 向左延伸、把 `tWCKosc(T) :` 吞进公式）；删除只删分母+真正并入的中间成分。
   - **方案B-3（本轮）**：合并分式后按 x 坐标重排到正确位置（分式插到"最后一个 x2<=分式.x
     的 comp"之后），修复 408"分式跑到行首"（composition 顺序非阅读顺序，分子 x=250 却排最前）。
   - **方案B-4（本轮）**：等号左右合并前检查分式行是否含"可翻译字母文本"（如 `tWCKosc`）；
     有则跳过等号左右合并（避免把 `MWCW OMM(T)` 可翻译内容吞进公式）。407 分式行无字母文本，照常合并。
   - **临时 debug**：`fraction_merge_debug.txt` 相关打印（SCAN/cand/BREAK/SKIP/COLLECT/MERGE 等）——**未清理**。
- `paragraph_finder.py`：
  - **临时 debug**：`_split_paragraph_into_lines` 里的 THREAD/THREAD-LINES 打印——**未清理**。
- `FRACTION_INVESTIGATION.md`：本文件（新增，临时记录）。

### git 分支状态
- BabelDOC 当前分支 `fix/p408-fraction-line-split`（从 main 切出）。main 未动。
- 改动文件：`styles_and_formulas.py`、`paragraph_finder.py`（M），`FRACTION_INVESTIGATION.md`（??新增）。
- **方案A（改 threading 分行）在此分支实验**；若失败需回退，可回 main 或 revert 本分支改动。
- 提交需用户确认（AGENTS.md 铁律）。

### 下一步（新窗口续接从这里开始）
1. **（本轮已实现）方案B**：`_merge_vertical_fractions` 跳过"与分子无 x 重叠的侧面普通文本"找分母，
   只合并分子+分母字符、保留侧面文本、删除时只删分母；等号左右合并加 x 重叠/紧邻防护。
   **待用户跑 page407/408 验证**：确认 para88（图235）/para103（图236）分式不再塌陷错位，且 Page 407
   无回归（等号左右合并仍正常）。
   - 若验证失败，重点检查：`_is_vertical_fraction_pair` 是否判 True（分子/分母 box 需在去孤立空格后
     满足 0.5~2.5×avg_h）；`tWCKosc(T)` 等侧面文本的 x 是否与分子 box 无重叠（否则不会 SKIP 而 BREAK）；
     等号左右合并的 x 防护是否误挡了 407 的 `= value` 合并。
   - 方案A（改 threading 分行）**已放弃**：threading 阶段分子分母本就在同一行，不是真正问题。
2. 确认图235 vs 图236 渲染差异根因（两者最终 composition 顺序相同，但用户观察不同）——若方案B 合并后
   顺序一致，此差异应随之消失；否则继续查。
3. 处理列表项1 下标问题（`temp`→"温度"）。
4. 整行"位置偏上"问题（可后调）。
5. 408 完全解决后统一更新 LOCAL_PATCHES.md / HISTORY.md / README 问题总表，并清理 debug 日志。
6. **Page 407 回归（已解决）**：`2^16`→`2^^16`（双 `^`）=补丁50 `9bb7192`（il_translator.py
   移除占位符前 LLM 补的文本 `^`）+ 最后一行第一个 `2^16` 的 `16` 上标位置错乱=补丁49 `111ff85`
   （styles_and_formulas.py 提前 `_remove_orphan_spaces` 到 `process_page_offsets` 之前）。均用户确认。
7. **（本轮新确认）408 偏右根因 = first_line_indent 首行缩进**：
   - `typesetting.py::_find_optimal_scale_and_layout` 当 `first_line_indent=True` 时 `current_x += space_width*4`
     （≈19pt）。列表项（以 bullet 开头）本不应缩进，却整体右移 19pt → 图236 整体偏右、图235 文字偏右。
   - 图235（para88, fallback_line）文字右移后排到 x=262 > 分式 box.x=249.8，分式放不下而换行退到下一行开头。
   - **已修**：`typesetting.py` 列表项跳过 first_line_indent——①首 unit 是 bullet 字符（`•`/`?`+空格）；
     ②列表项续行（当前段落非 bullet 开头，但同一 y 带左侧有一个以 bullet 开头的段落，如图235 的
     para88 文本/分式段 + 左侧 para87 bullet 段）。两者都跳过缩进。
   - **（新确认）图235 第二点整行消失的根因**：para88（fallback_line）若仍缩进，文本排到 x=262 >
     分式 box.x=249.8，分式换行占两行、current_y 超出 box 底部 → all_units_fit=False → 排版失败，
     `pdf_paragraph_composition=[]`、`optimal_scale=0.1`，pdf_creater 报
     "Unable to export paragraphs that have not yet been formatted"，整行内容消失（只剩 para87 bullet）。
   - **（修正）列表项续行判断**：para87（bullet 段）unicode 是**纯 `?`**（无后随空格），故续行判断
     不要求 `?` 后跟空格，纯 bullet 字符段落即视为列表项 → para88 不缩进、分式不换行、排版成功。
   - **（关键修正）NameError 根因**：列表项续行判断在 `_layout_typesetting_units` 里用了 `page.pdf_paragraph`，
     但该函数**没有 `page` 参数** → 抛 NameError，被 `_find_optimal_scale_and_layout` 的 try/except 静默
     捕获 → para88 排版异常失败（composition 空）。已给 `_layout_typesetting_units` 加 `page` 参数并在
     调用处（line 1106）传 `page`。
8. **图236 分式偏下已修**：`_row_text_box` 选择改为"选中心最接近分式中心、且含真实字母的文本"
   （排除 `_`/bullet `?  ` 等边缘字符），yoff = 文本半高 - 分式半高，分式中心与 `tWCKosc(V)` 中心对齐。
