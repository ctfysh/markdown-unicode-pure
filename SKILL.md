---
name: markdown-unicode-pure
description: 严禁所有 Unicode 特殊字符（上下标、数学符号、希腊字母等），统一使用 ASCII + Markdown/LaTeX 语法。AI 判定 + Python 纯执行闭环。
---

# Unicode → ASCII 转换规范（AI 判定 + Python 执行）

## 架构原则

**AI 判定，Python 执行，AI 终检。** 职责严格分离：

- **AI（本 skill 的宿主模型）**：读原文 → 找出所有问题（Unicode、混用、错用）→ 分类 → 写标注 JSON → 检查 Python 输出 → 循环直到全部解决
- **Python（`unicode_purify.py`）**：只执行，不判断。验证标注合法性 → 按 kind 渲染 → 应用 → 输出。任何歧义直接报错拒绝，绝不猜测

```
┌─ AI 读原文，逐字符判定分类，写 annotations.json
│
├─ python3 unicode_purify.py <input> --annotations ann.json -o out.md
│     验证：offset/scope 匹配原文、kind 合法、scope 不重叠（报错即停）
│     渲染：按 kind 查表 → 应用（右到左）
│
├─ AI 读 out.md，对照原文核查：是否全部解决？是否引入混用/错用？
│     python3 unicode_purify.py out.md   # 全量证据报告（无 flag）
│       └ leftover    剩余非 ASCII（CJK/合法符号已过滤）
│       └ latex_units 单位/数量级被写进 LaTeX（yr$^{-1}$ 等，ASCII 不可见违规）
│       └ mixing      LaTeX 与纯文本混用（= 号外置、运算符拆分、箭头+LaTeX 等）
│
└─ 未清 → 补标注重新 apply → 循环，直到所有问题解决为止
```

**循环终止条件**：证据报告三个区块（`leftover` / `latex_units` / `mixing`）全空，且 AI 目检输出无混用/错用。三个区块是**机械正则证据**：Python 只报告"文本中存在什么"，不决定该怎么做——处置（分类、改法）始终由 AI 判定。

## 标注格式

`annotations.json` 是 JSON 数组，每条一个待转换片段：

```json
[{"offset": 4, "scope": "m²", "kind": "markdown_super"}]
```

| 字段 | 说明 |
|------|------|
| `offset` | scope 在原文中的起始位置（0 起，UTF-8 码点计数） |
| `scope` | 原文中待转换的完整片段（**一个特殊字符 + 其语义操作数 = 一个整体**） |
| `kind` | 转换类别（见词汇表，必须精确匹配） |

**scope 界定原则**：符号 + 操作数必须整体标注。`±2%` 是一个 scope（`$\pm 2\%$`），`$\pm$2%` 是拆散错误；`ΔLOO-IC` 整体（`$\Delta\mathrm{LOO\text{-}IC}$`）；`E=mc²` 整体（`E=mc^2^`）。

Python 逐条验证，任一失败即整体拒绝（exit 1），不猜不修：
- 根必须是数组（否则报 `must be a JSON array`）
- `offset`/`scope`/`kind` 三者缺一不可
- `offset` 越界或 `text[offset:offset+len(scope)] != scope` → 标注与原文不符
- `kind` 不在词汇表中 → 未知类别
- 两个 scope 重叠 → 冲突

## kind 词汇表（24 种，Python 按此渲染）

| kind | 判定依据（AI 侧） | 渲染（Python 侧） | 示例 |
|------|------------------|-------------------|------|
| `markdown_super` | 数字/符号上标，单位或不确定语境 | `^...^` | `m²`→`m^2^`，`¹⁴C`→`^14^C`，`s⁻¹`→`s^-1^` |
| `markdown_sub` | 数字下标，化学式 | `~...~` | `H₂O`→`H~2~O` |
| `math_super_sub` | 上下标含**字母**或 `=` → 数学变量 | `$x^2$` / `$a_j$` / `$x_{i=1}^n$` | `x²`→`$x^2$`，`aⱼ`→`$a_j$`，`xᵢ₌₁ⁿ`→`$x_{i=1}^n$` |
| `greek_math` | 希腊字母，数学变量语境 | `$\eta$` 等 | `η`→`$\eta$` |
| `greek_text` | 希腊字母，文本/描述语境 | 英文名 | `η`→`eta` |
| `greek_prefix_math` | Δ/δ 后紧跟 ASCII 标识符/上下标，数学语境 | `$\Delta T$` 等，前缀后保留空格 | `ΔLOO-IC`→`$\Delta\mathrm{LOO\text{-}IC}$`，`ΔT`→`$\Delta T$`，`δ¹³C`→`$\delta^{13}{\mathrm{C}}$` |
| `greek_prefix_md` | Δ/δ 后紧跟标识符，文本/Markdown 语境 | 英文名，无空格 | `ΔLOO-IC`→`DeltaLOO-IC`，`δ¹³C`→`delta^13^C` |
| `math_op` | 数学运算符（单独或起头） | `$\pm$` 等 | `±`→`$\pm$`，`∑`→`$\sum$` |
| `op_value` | 运算符 + 紧跟数值（整体） | 整体 LaTeX，`%` 转义 `\%` | `±2%`→`$\pm 2\%$` |
| `math_expr` | 完整数学表达式（一个环境） | 一个 `$...$`，运算符后空格 | `∑x² - ∑y²`→`$\sum x^2 - \sum y^2$` |
| `sum_limits` | `∑`/`∫`/`∏` + 上下限结构 | `_{lower}^{upper}` | `∑ i=1 到 n`→`$\sum_{i=1}^{n}$` |
| `dimension_x` | `×` 在尺寸描述中 | 纯 ASCII `x` | `2.0 m × 1.4 m`→`2.0 m x 1.4 m` |
| `math_x` | `×` 在数学公式中 | `$\times$` | `$A = l \times w$` |
| `product` | `·` 两侧为量值/单位（整体） | LaTeX，单位正体 `\mathrm{}` | `kg·m/s`→`$\mathrm{kg}\cdot\mathrm{m}/\mathrm{s}$` |
| `interpunct` | `·` 两侧为中文（中文间隔号） | **保留原样** | `北京·上海`→`北京·上海` |
| `sqrt` | `√` + 参数（整体吸收） | `$\sqrt{...}$`，参数内上标转 LaTeX（单字符无花括号） | `√5`→`$\sqrt{5}$`，`√x²`→`$\sqrt{x^2}$` |
| `punct` | Unicode 标点 | ASCII | `–`→`-`，`—`→`--`，`…`→`...` |
| `keep` | 合法 Unicode，原样保留 | 原样 | `°`、`·`、`2024`、`Figure 1` |
| `merge_math` | 已写好的 `$...$` 内混入 Markdown 标记/游离上标 | 合并进同一 LaTeX 环境 | `$\sum$x^2^`→`$\sum x^2$`，`$\pm$2%`→`$\pm 2\%$` |
| `space_blocks` | 相邻两个 LaTeX 块粘连 | 中间加空格 | `$a_i$$b_j$`→`$a_i$ $b_j$` |
| `chem_to_md` | 化学式被错误写进 LaTeX | Markdown 下标 | `$H_2O$`→`H~2~O` |
| `unit_to_md` | 单位被错误写进 LaTeX | Markdown 上标 | `$10 m^2$`→`10 m^2^` |
| `ordinal_plain` | 序数被写成上标 | 纯文本 | `1^st^`→`1st` |
| `letter_sub_math` | 字母下标被写成 Markdown | LaTeX 下标 | `x~i~`→`$x_i$` |

## 执行入口（AI 判定决策树）

遇到特殊字符，按优先级判断归属（**不混杂**是硬约束）：

1. **确认是文字/单位/化学式？** → Markdown（上下标 `^ ^` / `~ ~`：`m^2^`、`H~2~O`、`E=mc^2^`；尺寸 `×` → 纯 ASCII `x`）
2. **确认是数学公式？** → 完整 LaTeX `$...$`（`$\pm 2\%$`、`$\sum x^2 - \sum y^2$`；LaTeX 内部用 `^`/`_`）
3. **不确定归属？** → Markdown 兜底（`m²` → `m^2^`，希腊字母 → 英文名）
4. **序数/编号/年份？** → 保持纯文本（`1st`、`Figure 1`、`2024`）

**如何判断"确认是公式"**：上下标含**字母**（`aⱼ`、`xᵢ`）或含 `=`（`xᵢ₌₁ⁿ`）→ 数学变量下标，公式 → LaTeX（`$a_j$`、`$x_{i=1}^n$`）；只有数字（`H₂O`、`m²`、`s⁻¹`）→ 单位/化学式 → Markdown。`∑`/`∫`/`∏` 出现 → 公式。

**文档级一致性（上下文判定）**：同一 token 全文出现多次时，优先复用它此前被确认的形态。如 `式中 $\eta$ 为效率，η 值取 95%` → 第二个 `η` 也标 `greek_math`；`delta^13^C 含量，以及 δ¹³C 值` → `δ¹³C` 也标 `greek_prefix_md`。冲突（同一 token 两种形态都合理）→ 按就近上下文定，仍存疑则向用户确认。

**优先级总原则**：确认文字 → Markdown；确认公式 → LaTeX；不确定 → Markdown 兜底；绝不混杂（同一表达式内禁止混用 `$...$` 与 `^ ^`/`~ ~`；中文绝不放进 LaTeX）。

## 混杂自检清单（转换完成后逐项核对）

**混杂（mixing）指同一表达式内同时出现 LaTeX 与 Markdown 标记。** AI 检查输出时逐项核对：

- [ ] 同一公式是否既用 `$...$` 又用 `^ ^`/`~ ~`？→ 公式内统一 `^`/`_`（LaTeX），Markdown 上下标只出现在 `$...$` 外
- [ ] 中文是否误入 LaTeX？→ `$...$` 内不应出现中文；`∑x²和y²` 应为 `$\sum x^2$和$y^2$`（`和` 是中文连词，留在文本）
- [ ] 符号+数值是否被拆散？→ `±2%` 应为 `$\pm 2\%$`，不是 `$\pm$2%`；`kg·m/s` 应整体进 `$\mathrm{kg}\cdot\mathrm{m}/\mathrm{s}$`，不是 `kg$\cdot$m/s`
- [ ] 运算符后是否残留 Markdown 上标？→ `∑x²` 应为 `$\sum x^2$`，不是 `$\sum$x^2^`；`√x²` 应为 `$\sqrt{x^2}$`
- [ ] 一个完整数学表达式是否被拆成多个 LaTeX 环境？→ `∑x² - ∑y²` 应为 `$\sum x^2 - \sum y^2$`（一个环境），不是 `$\sum x^2$ - $\sum y^2$`
- [ ] 相邻两个公式块之间是否有空格分隔？→ `$a_i$ $b_j$`，不是 `$a_i$$b_j$`
- [ ] 数学连词结构是否正确？→ `∑ i=1 到 n` 应为 `$\sum_{i=1}^{n}$`（`到` 引入上下限）；`∑x²和y²` 的 `和` 是文本连接词（两侧各自成块）
- [ ] 上标格式是否统一？→ 单字符 `^2`/`_j`，多字符 `^{10}`/`_{i=1}`（如 `m^2^`、`x^{10}`、`$x_{i=1}^n$`）——**如无必要不用花括号**（`x_{i=1}^n` 中 `ⁿ` 是单字符 → `^n`，不写 `^{n}`）

> AI 生成的文本比脚本转换更容易引入混杂——LLM 输出也可能把 LaTeX 运算符与 Markdown 上下标拼在一起。无论来源，转换后必须过此清单。

## 转换规则表（AI 判定依据）

### 数学运算符（→ LaTeX）

| 字符 | 替换 | 字符 | 替换 | 字符 | 替换 |
|------|------|------|------|------|------|
| `×` | `$\times$` | `≤` | `$\le$` | `∑` | `$\sum$` |
| `÷` | `$\div$` | `≥` | `$\ge$` | `∏` | `$\prod$` |
| `±` | `$\pm$` | `≠` | `$\neq$` | `∫` | `$\int$` |
| `∓` | `$\mp$` | `≈` | `$\approx$` | `∂` | `$\partial$` |
| `·` | `$\cdot$` | `≡` | `$\equiv$` | `∇` | `$\nabla$` |
| `−` | `-` | `∞` | `$\infty$` | `√` | `$\sqrt{5}$`（带参数） |
| `∈` | `$\in$` | `∉` | `$\notin$` | `∀` | `$\forall$` |
| `⊂` | `$\subset$` | `⊃` | `$\supset$` | `∃` | `$\exists$` |
| `∪` | `$\cup$` | `∩` | `$\cap$` | `∅` | `$\emptyset$` |

> 数学变量中的希腊字母：`$\alpha$` `$\beta$` `$\gamma$` `$\delta$` `$\epsilon$` `$\eta$` `$\theta$` `$\lambda$` `$\mu$` `$\nu$` `$\pi$` `$\sigma$` `$\phi$` `$\omega$`
> 非数学语境中的希腊字母：写英文名（`alpha`、`beta`、`eta`）

### Unicode 上下标（数字 → Markdown，字母/= → LaTeX 公式）

| 场景 | 规则 | 示例 |
|------|------|------|
| 数字上标（单位/不确定） | `^数字^` | `²` → `m^2^`，`⁻¹` → `s^-1^`，`¹⁴C` → `^14^C` |
| 数字下标（化学式） | `~数字~` | `₂` → `H~2~O` |
| 字母下标 / 含 `=` 的下标（确认公式） | LaTeX `$...$` | `aⱼ` → `$a_j$`，`xᵢ` → `$x_i$`，`xᵢ₌₁ⁿ` → `$x_{i=1}^n$` |
| 公式中 `∑`/`∫`/`∏` 后表达式 | 整体吸收进一个 LaTeX 块 | `∑x²` → `$\sum x^2$`，`∫0¹ x² dx` → `$\int 0^1 x^2 dx$` |
| `∑`/`∫`/`∏` 的上下限 | 用 `_{lower}^{upper}` | `∑ i=1 到 n` → `$\sum_{i=1}^{n}$` |
| 完整公式含运算符 | 一个表达式 = 一个 LaTeX 环境 | `∑x² - ∑y²` → `$\sum x^2 - \sum y^2$`（不是两个 `$...$`） |
| 上标格式 | 单字符 `^2`/`_j`，多字符 `^{10}`/`_{i=1}` | `x^2^`、`$x_{i=1}^n$`、`$\sqrt{x^2}$` |

### Unicode 标点（→ ASCII）

| 字符 | 替换 | 字符 | 替换 |
|------|------|------|------|
| `–` | `-` | `'` `'` | `'` |
| `—` | `--` | `"` `"` | `"` |
| `…` | `...` | | |

### 边界情况

| 场景 | 规则 |
|------|------|
| 全 ASCII 文本（`m/s`、`kg`、`10 cm`） | **原样保留，不转换**（无 Unicode 就无需任何处理） |
| `×` 在尺寸描述中（如 `2.0 m × 1.4 m`） | 转纯 ASCII `x`（`2.0 m x 1.4 m`），不进 LaTeX |
| `×` 在数学公式中 | 用 `$\times$`（如 `$A = l \times w$`） |
| `·` 两侧是单位/量值（`kg·m/s`、`5 · 10³`） | 整体进 LaTeX，单位正体 `\mathrm{}`（`$\mathrm{kg}\cdot\mathrm{m}/\mathrm{s}$`、`$5 \cdot 10^{3}$`） |
| `·` 两侧是中文（`北京·上海`、`列夫·托尔斯泰`） | **中文间隔号，保留原样**（与 `，`、`。` 同属中文标点，U+00B7 合法 Unicode） |
| `√` 后接参数（`√5`、`√x`、`√(x+1)`） | 整体进 LaTeX `$\sqrt{5}$`、`$\sqrt{x}$`、`$\sqrt{(x+1)}$`；参数内上标转 LaTeX（`√x²`→`$\sqrt{x^2}$`、`√(x+1)²`→`$\sqrt{(x+1)^2}$`，单字符无花括号） |
| `±2%` 等符号+数值 | 整体进 LaTeX `$\pm 2\%$`（`%` 须转义 `\%`，勿拆散） |
| `∑`/`∫`/`∏` 后接表达式 | **整体吸收**进一个 LaTeX 块（`∑x²`→`$\sum x^2$`、`∫0¹ x² dx`→`$\int 0^1 x^2 dx$`）；**完整公式 = 一个环境**（`∑x² - ∑y²`→`$\sum x^2 - \sum y^2$`）；遇中文连词 `和/与/及/或` 拆块（`∑x²和y²`→`$\sum x^2$和$y^2$`）、`到/至` 转上下限（`∑ i=1 到 n`→`$\sum_{i=1}^{n}$`）；其他中文（`，`、`的`）立即中断，中文绝不放进 LaTeX |
| `aⱼ`、`xᵢ₌₁ⁿ` 等字母下标 | 确认公式 → LaTeX `$a_j$`、`$x_{i=1}^n$`（字母/`=` 下标是数学变量标志；数字下标是单位/化学式标志） |
| `10^6^ kg` 等数量级+单位 | 用 `^ ^`，不进 LaTeX |
| `ΔLOO-IC`、`ΔT`、`δ¹³C`（Δ/δ 后紧跟标识符/上下标） | **作用范围前瞻吸收**：整个标识符（含 `-`）整体转换，绝不拆散（`ΔLOO-IC`→`DeltaLOO-IC` 或 `$\Delta\mathrm{LOO\text{-}IC}$`；`ΔT`→`DeltaT` 或 `$\Delta T$`；`δ¹³C`→`delta^13^C` 或 `$\delta^{13}{\mathrm{C}}$`）。**禁止 `$\Delta$LOO-IC`**（符号+客体拆散 = 混杂） |
| `Δ G`（Δ/δ 后跟空格/中文/句号） | 孤立希腊字母：文本语境 → `Delta`/`delta` 英文名，数学语境 → `$\Delta$`/`$\delta$`（无操作数可吸收） |

## 失败模式

| 触发条件 | 一线修复 | 兜底 |
|----------|---------|------|
| 未列出的 Unicode 字符 | 按决策树判断归属 | 标 `keep` 并询问用户 |
| 字符歧义（如 `·`） | 检查上下文 | 中文语境保留 `·`；其他默认 `$\cdot$` |
| LaTeX 语法不确定 | 按表格查表转换 | 标注 `[CHECK: ???]` 询问用户 |
| 输入无 Unicode 问题 | 原文输出 | — |
| 证据报告仍有命中（`leftover` / `latex_units` / `mixing` 任一非空） | 对命中逐条补标注 → 重新 apply | 无法判断则保留并询问用户 |

## 常见错误

| 原句（❌） | 修正（✅） |
|-----------|-----------|
| 面积 10 m²，化学式 H₂O | 面积 10 m^2^，化学式 H~2~O |
| E=mc²，第 2nd 章讨论 ¹⁴C | E=mc^2^，第 2nd 章讨论 ^14^C |
| 加速度 m/s² | 加速度 m/s^2^ |
| 效率 η 95%，误差 ±2% | 效率 $\eta$ 95%，误差 $\pm 2\%$ |
| 浓度 0.1−0.5 mol/L，温度 20–25°C | 浓度 0.1-0.5 mol/L，温度 20-25°C |
| 面积 2.0 m × 1.4 m，体积 2.8 m³ | 面积 2.0 m x 1.4 m，体积 2.8 m^3^ |
| 下标 aⱼ、上标 xᵢ₌₁ⁿ | $a_j$、$x_{i=1}^n$（字母下标 = 公式 → LaTeX） |
| ∑x² - ∑y² | $\sum x^2 - \sum y^2$（一个完整公式 = 一个 LaTeX 环境） |
| ∑x²和y² | $\sum x^2$和$y^2$（`和` 是中文连词，两侧各自成块） |
| ∑ i=1 到 n | $\sum_{i=1}^{n}$（`到` 引入上下限） |
| ΔLOO-IC、ΔT、δ¹³C | DeltaLOO-IC / `$\Delta\mathrm{LOO\text{-}IC}$`、DeltaT / `$\Delta T$`、delta^13^C / `$\delta^{13}{\mathrm{C}}$` |
| $\Delta$LOO-IC、$\pm$2% | $\Delta\mathrm{LOO\text{-}IC}$、$\pm 2\%$（符号+操作数拆散 = 混杂） |

## 反例清单（不要做什么）

| ❌ 不要 | ✅ 要 |
|--------|------|
| 不要用 Unicode 上下标 `m²` `H₂O` | 用 Markdown `m^2^` `H~2~O` |
| 不要用 Unicode 希腊字母 `η` `α` `β` | 数学中用 `$\eta$`；文本用 `eta` `alpha` |
| 不要用 Unicode 数学符号 `±` `≤` `≥` | 用 LaTeX `$\pm$` `$\le$` `$\ge$` |
| 不要用 Unicode 标点 `–` `—` `…` | 用 ASCII `-` `--` `...` |
| 不要把单位写进 LaTeX `$10 m^2$` | 用 Markdown `10 m^2^` |
| 不要把化学式写进 LaTeX `$H_2O$` | 用 Markdown `H~2~O` |
| 不要把序数写成上标 `1^st^` | 保持纯文本 `1st` |
| 不要把字母下标转成 Markdown `a~j~` | 确认公式 → LaTeX `$a_j$` |
| 不要混用 `$\sum$x^2^` | 整体进 LaTeX `$\sum x^2$` |
| 不要拆散符号+操作数 `$\Delta$LOO-IC`、`$\pm$2%` | 整体 `$\Delta\mathrm{LOO\text{-}IC}$`、`$\pm 2\%$`（作用范围：特殊字符+语义操作数=一个整体） |
| 不要拆散完整公式 `$\sum x^2$ - $\sum y^2$` | 一个环境 `$\sum x^2 - \sum y^2$` |
| 不要把中文放进 LaTeX `$\sum x^2和y^2$` | 中文连词留在文本 `$\sum x^2$和$y^2$` |
| 不要给单字符上标加花括号 `$x_{i=1}^{n}$` | `$x_{i=1}^n$`（如无必要不用花括号；多字符才 `^{10}`/`_{i=1}`） |

## CLI 速查

```bash
# 应用标注（AI 判定后执行）：验证 → 渲染 → 应用
python3 unicode_purify.py <input> --annotations ann.json -o out.md

# 证据报告（AI 终检用）——无 flag = 三个区块全输出：
#   leftover    剩余非 ASCII（CJK/合法符号已过滤）
#   latex_units 单位/数量级被写进 LaTeX（ASCII 不可见违规）
#   mixing      LaTeX 与纯文本混用
python3 unicode_purify.py out.md

# 只看某一类证据（互斥区块选择器）
python3 unicode_purify.py out.md --leftover    # 仅剩余非 ASCII
python3 unicode_purify.py out.md --mixing      # 仅 latex_units + mixing

# 机器可读报告（含 output + leftover + latex_units + mixing）
python3 unicode_purify.py <input> --annotations ann.json --json

# 输入 "-" 从 stdin 读取
```