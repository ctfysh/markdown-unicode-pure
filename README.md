# markdown-unicode-pure

Unicode → ASCII + Markdown/LaTeX 转换规范。禁止所有 Unicode 特殊字符（上下标、数学符号、希腊字母、特殊标点），统一转换为 ASCII + Markdown 或 LaTeX 语法。

## 用途

面向 AI 写作/排版场景：当输出内容需要提交到只接受 ASCII + Markdown/LaTeX 语法的系统（如严格 Markdown 渲染器、LaTeX 编译器、无 Unicode 支持的旧系统）时，本 skill 确保输出零 Unicode 特殊字符。

## 解决的问题

AI 默认输出倾向使用 Unicode 特殊字符（`m²`、`H₂O`、`η`、`±`、`≤`、`–`），这些字符在以下场景会出问题：

- 严格 Markdown 渲染器不识别 `m²`，但识别 `m^2^`
- LaTeX 编译器不识别 `η`，但识别 `$\eta$`
- 旧系统/数据库编码不支持 Unicode 数学符号

## 转换规则速览

转换优先级：**Markdown > LaTeX > 绝不用 Unicode（任何形式）**。

| 场景 | 规则 | 示例 |
|------|------|------|
| 上下标（任何语境） | Markdown `^ ^` / `~ ~` 优先 | `m²` → `m^2^`，`x²` → `x^2^`，`H₂O` → `H~2~O`，`¹⁴C` → `^14^C` |
| 数学运算符 | LaTeX `$...$` | `±` → `$\pm$`，`≤` → `$\le$`，`∑` → `$\sum$` |
| 符号+数值 | 整体进 LaTeX，`%` 转义 `\%` | `±2%` → `$\pm 2\%$` |
| 尺寸 `×` | 纯 ASCII `x` | `2.0 m × 1.4 m` → `2.0 m x 1.4 m` |
| 数学公式中的 `×` | `$\times$` | `$A = l \times w$` |
| 希腊字母（数学） | LaTeX `$\alpha$` | `α > β` → `$\alpha > \beta$` |
| 希腊字母（文本） | 英文名 | `η 为 95%` → `eta 为 95%` |
| 序数/编号/年份 | 保持纯文本 | `1st`、`Figure 1`、`2024` |
| 特殊标点 | ASCII | `–` → `-`，`…` → `...` |

## 批量处理脚本

`unicode_purify.py` 做确定性替换（上下标、运算符、希腊字母、标点、`±` 数值），无法判断的语义歧义写入 amb.json 供二次处理：

```bash
python3 unicode_purify.py <input> -o <output> --amb-out amb.json
```

## 文件结构

| 文件 | 用途 |
|------|------|
| `SKILL.md` | 主规范（决策树 + 转换规则表 + 失败模式 + 反例清单） |
| `unicode_purify.py` | 批量确定性转换脚本（上下标/运算符/希腊字母/标点 + amb.json 歧义报告） |
| `test-prompts.json` | 3 个测试 prompt（用于实测表现评估） |
| `results.tsv` | 达尔文优化日志（9 列，含 eval_mode） |

## 安装

将本目录放入任意 skills-compatible runtime 的 skills 目录（如 `~/.claude/skills/`、`~/.config/opencode/skills/`、`.cursor/skills/` 等，取决于你的工具），或在提示词中直接引用 `SKILL.md` 内容。

## 开发

本 skill 通过 [darwin-skill](https://github.com/alchaincyf/darwin-skill) 优化。优化历史见 `results.tsv`：

| 轮次 | 改动 | 判定 |
|------|------|------|
| baseline | 初始版本（130 行） | 76.7 分 |
| Round 1 | 添加失败模式 fallback 表（dim3） | 2-0 better → keep |
| Round 2 | 重构压缩：决策树前置 + 合并表格 + 新增反例清单（130→92 行） | 2-0 better → keep |
| Round 3 | Markdown 优先原则 + 新增 unicode_purify.py 批量脚本（dim6 资源整合） | 主 session 自评（待记录） |