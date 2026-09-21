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

## 二、Page 408（U9）— 分式已解决(S24/S25)；下标问题修复中（待用户重跑确认）

### 现象（用户肉眼观察）
图235、图236 下方各有两个无序列表项：
- 列表项1：`OSC Match_temp : OSC Match_temp = [tWCK2DQ (T) – tWCKosc (T) – OSC offset_temp ]`
  - **下标问题**：`temp` 被翻译成中文"温度"，`Match_temp` 未作为完整下标保留。

### 下标问题根因（2026-09-19 排查，方向已修正——TT2/TT6 都是公式字体，非"字体未识别"）
> 先前的"TT2 普通字体未识别为公式"假设**错误**：TT2=`TimesNewRomanPSMT`、TT6=`MIKSPQ+CambriaMath`，
> 两者都匹配公式字体名模式（`TimesNewRoman.*`/`Cambria.*`），**都是公式字体**。已撤销对应的代码改动。

**真实现象（styles_and_formulas.json / translate_tracking.json 确认）**：
- **冒号前**的 `Match_volt`（comp2, x=131）：完整公式 `{v5}`，字符 `Match_volt ` 含下划线，**正常**。
- **冒号后**的第二个 `Match_volt`：被**拆分损坏**——`Match`→公式 `{v12}`，`volt`→**TEXT**（comp8），
  下划线 `_` 被抽到独立的 comp15 `_ `；翻译后 `volt`→"电压"。
- **`offset_volt`**：`offset`→公式（comp12），`volt`→**TEXT**（comp13）→"电压"。
- temp 版（para 83/84/86）：第二个 `Match`→公式（comp6），`temp` 被拆到 para 84 comp0（TEXT）、
  para 86 comp0（TEXT）→"温度"。
- **二次损坏**：`_merge_vertical_fractions`（补丁47）把冒号后 `Match`+两个 `(V)`+`_` 过度合并成
  comp7 `(V) (V)_  Match`（box x=193-345 横跨整行）。
- `volt`/`temp`→TEXT 的拆分发生在**翻译前**的公式分类/分组阶段（translate_tracking 的 input 已显示
  `{v12}=Match` + `volt`(text)），`_merge_vertical_fractions` 在其后（翻译后）进一步过度合并。
- **确切拆分机制待定**：`volt` 是 TT6 公式字体、非 plain-inline 字符，静态分析看 `is_in_formula_font`
  应为 True，但实际被拆成 TEXT。需加 debug 插桩到 `_classify_characters_in_composition`/
  `_group_classified_characters` 输出 para 102 逐字符公式 tag，再让用户重跑确认。

### 【根因确认 2026-09-20】下划线 `_` 的 visual_bbox 异常 → 行聚类拆行 + 公式 box 污染
经 debug（paragraph_finder.json + styles_and_formulas.json + classify_debug.txt）最终确认，图235（temp 版）
两个剩余问题的**共同根因**是 **TT6(CambriaMath) 下划线 `_` 字符的 `visual_bbox` 异常**：
其字形包围盒（glyph bbox）被报在放置框上方约 16pt（vis_y≈388.6，而 box_y≈404.5）。

**数据证据（para Y7gq4，temp 版列表项）**：
- paragraph_finder.json：该行被拆成 3 个 LINE 片段，拆分点正好都在异常 `_` 处：
  - comp0 `OSCMatch_temp :   OSCMatch`（第2个 `Match` 结束，缺 `_`）
  - comp1 `temp = [tWCK2DQ(T) –tWCKosc(T) – OSCoffset_`（以 `temp` 开头、`offset_` 结尾）
  - comp2 `temp]`
- 第2个 `Match` 的下划线 `_`（x≈212.8）形成独立 fallback_line 段落 `gw5d3`（孤立 `_`），
  因 vis_y=388.6 与主字符行（vis_y=404.5）不重叠，被 `extract_char.process_page_chars_to_lines`
  （用 visual_bbox 聚类）聚成独立"行"，后成独立段落，未并入列表项 → **第2个 `Match_temp` 丢下划线**。
- `offset_temp`（styles comp13）本身是完整一个公式，但其 box 被 `_` 的 vis_y=388.6 撑高
  （y 388.5-409，高 20pt），使 `process_page_offsets` 的 y_offset 算错 → **没落在 OSC 下方**。
- 对比：冒号前第1个 `Match_temp` 的 `_`（x=148，TT2）vis_y=402.1 正常 → 未拆行、下划线完整。

**根因修复（il_creater_active.py，frontend，实际主路径）**：
- 注意：`il_creater_active.py`（ActiveILCreater）才是实际使用的 PDF 解析路径，
  `il_creater.py`（ILCreater）是遗留兼容工具（native_parse.py/active_parse_runtime.py 走 active 路径）。
  最初误改在 `il_creater.py`，验证无效后已 `git checkout` 回退，改到 `il_creater_active.py`。
- 【2026-09-20 二轮 debug 精确定位】真正根因是 **CambriaMath 字体的 `font.descent` 异常**，
  而非字形包围盒覆盖：
  - debug（underscore_dbg.txt）显示：异常 `_` 的 `descent≈-15.97pt`（font.descent≈-2465），
    而正常 `_`/其他字符仅 `-1.8~-2.0pt`（font.descent≈-247）。
  - `project_native_char` 默认 visual_bbox = `box + descent`（line 1365-1370）。`descent=-15.97`
    使 `_` 的 visual_bbox 上移约 16pt（vis_y 388.5 vs box_y 404.5）。
  - 为什么只有 `_` 受影响：正常字符（Match/temp）字形墨迹面积大（volume>1），被
    `char_bounding_box` 字形包围盒覆盖（得到正确 visual_bbox）；而 `_` 是细线下划线、
    字形包围盒体积小（volume≤1），**不触发字形覆盖，走默认 `box+descent`**，被异常 descent 拉高。
- **修复**（line 1371-1386）：默认 visual_bbox（`box+descent`）计算后，若其与字符放置框
  `bbox` 在 y 轴**无重叠**（descent 异常、visual_bbox 与 bbox 分离），回退用 `bbox` 本身作为
  visual_bbox。正常 descent 时 `box+descent` 与 bbox 必然重叠（仅下移约 2pt），不受影响。
  这是通用原则修复（非 `_` 特判）。一处修复同时解决：聚类拆行（丢下划线）+ 公式 box 污染
  （offset_temp 定位）。
- 注：曾尝试在字形包围盒覆盖块加"y 重叠守卫"，但 debug 证明 `_` 的 in_volume=False 根本不走
  该块，故该守卫无效已回退。真正的修复在默认 descent 路径。

### 【2026-09-20 三】图235 第二点分式"分子偏上"根因 = 分子分母被拆到不同段落（非回归）
用户反馈：图235 第二点（`tWCKosc(T) : Run Time/2*Count`）"分子+正文整体偏上，分母/分数线正常"；
图236 第二点正常。**经对比备份与当前两轮 styles/typsetting 数据完全相同**——descent 修复**没有**
改变分式渲染，故"偏上"是长期存在、非本轮引入的回归（用户可能此前未细看第二点）。

**根因（create_il / paragraph_finder / styles_and_formulas 三层确认）**：
- 原始（create_il）：图235 分式是标准垂直分式——分子（Run Time）x=249.6-282 y=387.8-394.8、
  分母（2*Count）x=252-282 y=376.9-383.9，x 重叠、垂直相邻。
- paragraph_finder 阶段：**图235 分式被拆成 3 个段落**（都标 fallback_line）：
  - cfej1（主段）：`OSCMatch_temp : ... tWCKosc(T) : ... 分子`（分子 y=388）
  - Kb2b4：**分母**（y=377）——独立成段
  - ykn4h：bullet `?`
- 图236（aWy7d）分式是 **plain text 整段**：分子（y=102）分母（y=91-96）**同一段落**。
- styles_and_formulas `_merge_vertical_fractions` 只遍历**单个段落内**的 composition，故：
  - 图236 分子分母同段 → 合并成功 → 分式整体 yoff=-3.99，正常居中。
  - 图235 分子（cfej1）分母（Kb2b4）**跨段落** → 无法合并 → 分子单独渲染 yoff=+5.467（上移），
    分子离分数线约 10pt（236 仅 6pt），即"分子偏上"。

**关键差异**：图235 被布局模型标为 fallback_line 且分子分母 y 有 gap（cfej1 y≥381.2 vs Kb2b4
y≤379.2，gap≈2pt）→ threading 拆段；图236 被标 plain text 且 y 连续 → 整段。

**修复方向（已实现 方案A：跨段落合并成公式）**：
- `styles_and_formulas.py` 新增 `_merge_cross_paragraph_fractions(page)`，在 `_merge_vertical_fractions`
  开头调用：扫描所有段落，把"**恰好一个公式**的分母段落（无普通文本行）+ 与某公式垂直分式对
  （`_is_vertical_fraction_pair`）+ x 重叠"的**分母段落**并入分子段落，删除原分母段落。
  随后 `_merge_vertical_fractions` 就能在段内把分子分母合并成一个公式（整体居中，不再偏上）。
- 验证：235 分式分子 box=(249.8,387.8,282,394.8)、分母 box=(252.4,376.9,279.8,381.6)，x 重叠、
  y_diff=12.05、avg_h=5.85，`2.925≤12.05≤14.6` → `_is_vertical_fraction_pair=True`。合并条件成立。
  235 分母段（6DyrB）ncomp=1 单公式 → 通过。
- **回归修正（236 第二点拼接第一点，2026-09-21）**：初始版"纯公式段"判定用
  `any(c.pdf_line is not None)`，但图236 第二点段（4WvfK，`tWCKosc(V) : ...=运行时间/2*计数`）
  的文本在 styles 阶段已被分类为 pdf_formula（数学字体），非 pdf_line → 误判为"纯公式"，把**整个
  第二点段**吸收进第一点段 → 第二点拼接在第一点后面。**已改为"恰好 1 个 composition 且为
  pdf_formula"**：4WvfK 有文本+分式=多 composition → 排除；6DyrB 单公式 → 照常合并。
- 安全性：只合并"单个公式分母段"，且须垂直分式对；Page 407 分式分子分母本就在同段，找不到跨段
  对，不受影响。同行公式 y_diff≈0 不满足垂直相邻，不会误并。

**预期**：重跑后图235 整行应为单一 cluster → 单 fallback_line → `Match_temp`/`offset_temp` 下划线完整，
`offset_temp` 正常作为 OSC 的下标渲染。

### 【2026-09-21 四】图235 第二点"整体向上"根因 = 换行行距未考虑分式高度（typesetting）
分式跨段合并后（comp22 box=(249.8,376.9,282,394.8)，高 17.93pt），图235 第二点仍"整体向上 ~8pt"。
经 typesetting 追踪：
- **根因**：`typesetting.py::_layout_typesetting_units` 换行时（line ~1717）行距按**当前行**（第一点
  文本行，高 ~10pt）`max(font_size*scale*line_skip, mode_height*line_skip, max_height*1.05)≈15pt` 推进，
  **不前瞻下一行**的高分式（17.93pt）→ 第二点被上拉 ~8pt，与第一点几乎重叠（原文本两行间距 23pt）。
- **修复**：新增 `para_max_height = max(unit.height)*scale`（段落内最高排版单元），行距下限追加
  `para_max_height * line_skip`。正常文本段 `para_max_height≈font_size` → 行距不变；仅含高公式段落
  的行距被抬升，避免高分式行被上一行重叠。
- **次要根因（分式 box 被空格撑高 2.23pt）**：分式分子 `运行时间 `（尾随空格）的空格无字形，
  visual_bbox 取整字符框高（CambriaMath 空格 y2=394.8）→ 分式 box 由 ~15.7 涨到 17.93pt，
  y_offset 更偏负。**修复**：`formular_helper.py::update_formula_data` 高度计算新增本地
  `_formula_height_char_ignored`（忽略空格，仍计入宽度）。注意不改共享的
  `layout_helper.formular_height_ignore_char`（它在 `is_newline` 里使用，忽略空格会破坏换行检测）。
- 两修复均为条件式：只影响含高公式段/含空格公式，正常文本与普通公式不受影响。


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
- [x] 图235 第二点分式"分子偏上"=跨段落合并（方案A）已实现 + 图236 拼接回归已修（单公式守卫）。
- [x] 图235 第二点"整体向上 ~8pt"=typesetting 行距未考虑分式高度，已修（para_max_height 行距下限）
      + 分式 box 空格撑高已修（formular_helper 高度忽略空格）。
- [x] **列表项1 下标问题（temp/volt 译成"温度/电压"）已解决**：真因是 `_` 下划线 descent 异常导致
      `Match_temp`/`offset_temp` 被拆行、`temp`/`volt` 被当普通文本。il_creater_active.py descent 钳制
      + paragraph_finder fallback_line 链合并 + styles 角标续接后，`Match_temp`/`Match_volt`/
      `offset_temp`/`offset_volt` 均作为完整公式保留（translate_tracking `formula_chars: Match_temp`/
      `Match_volt`/`offset_volt`），不再翻译成"温度/电压"。用户重跑确认列表项与原文一致。
- [x] **图236 `OSC` 保留为英文（不译成"振荡器"）已解决**：`OSC` 作为公式/标识符保留（输出含
      `<style id='4'>OSC</style>`），未被翻译成"振荡器"。用户重跑确认与原文一致。
- [x] 408 完全解决后统一更新 LOCAL_PATCHES.md / HISTORY.md / README 问题总表。

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
