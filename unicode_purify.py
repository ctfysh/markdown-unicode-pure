#!/usr/bin/env python3
"""
unicode_purify.py — deterministic Unicode → ASCII/Markdown/LaTeX conversion
for the `markdown-unicode-pure` skill.

Two-pass design:
  Pass A (deterministic): converts characters whose mapping is context-free
      (math operators, Greek letters, punctuation, superscripts after units,
      subscripts inside chemical formulas).
  Pass B (ambiguity report): anything the script cannot decide reliably
      (context-dependent superscripts/subscripts, stray symbols) is left in
      place and reported as [AMBIGUOUS ...] entries, intended for a second
      LLM pass guided by the skill's decision tree.

Usage:
  python3 unicode_purify.py <input> [-o output.md] [--amb-out amb.json]
      [--in-place] [--json] [--no-ambiguous-marker]

Examples:
  python3 unicode_purify.py draft.md -o clean.md --amb-out amb.json
  python3 unicode_purify.py draft.md --in-place
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# Character maps
# --------------------------------------------------------------------------

SUPERSCRIPT_MAP = {
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5",
    "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9", "ⁿ": "n",
    "⁺": "+", "⁻": "-", "⁽": "(", "⁾": ")", "⁼": "=",
}
SUBSCRIPT_MAP = {
    "₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4", "₅": "5",
    "₆": "6", "₇": "7", "₈": "8", "₉": "9", "ₙ": "n",
    "₊": "+", "₋": "-", "₌": "=", "₍": "(", "₎": ")",
    "ₐ": "a", "ₑ": "e", "ₒ": "o", "ₓ": "x", "ₖ": "k", "ₗ": "l",
    "ₘ": "m", "ₚ": "p", "ₛ": "s", "ₜ": "t", "ᵢ": "i", "ⱼ": "j", "ᵣ": "r",
    "ᵤ": "u", "ᵥ": "v", "ᵦ": "beta", "ᵧ": "gamma", "ᵨ": "rho",
    "ᵩ": "phi", "ᵪ": "chi",
}

GREEK_LOWER = {
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon",
    "ζ": "zeta", "η": "eta", "θ": "theta", "ι": "iota", "κ": "kappa",
    "λ": "lambda", "μ": "mu", "ν": "nu", "ξ": "xi", "ο": "omicron",
    "π": "pi", "ρ": "rho", "σ": "sigma", "τ": "tau", "υ": "upsilon",
    "φ": "phi", "χ": "chi", "ψ": "psi", "ω": "omega",
}
GREEK_UPPER = {
    "Α": "Alpha", "Β": "Beta", "Γ": "Gamma", "Δ": "Delta", "Ε": "Epsilon",
    "Ζ": "Zeta", "Η": "Eta", "Θ": "Theta", "Ι": "Iota", "Κ": "Kappa",
    "Λ": "Lambda", "Μ": "Mu", "Ν": "Nu", "Ξ": "Xi", "Ο": "Omicron",
    "Π": "Pi", "Ρ": "Rho", "Σ": "Sigma", "Τ": "Tau", "Υ": "Upsilon",
    "Φ": "Phi", "Χ": "Chi", "Ψ": "Psi", "Ω": "Omega",
}

# Unicode → LaTeX command inside math mode ($...$) for Greek letters
GREEK_LATEX = {
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta", "ε": r"\epsilon",
    "ζ": r"\zeta", "η": r"\eta", "θ": r"\theta", "ι": r"\iota", "κ": r"\kappa",
    "λ": r"\lambda", "μ": r"\mu", "ν": r"\nu", "ξ": r"\xi", "ο": r"\omicron",
    "π": r"\pi", "ρ": r"\rho", "σ": r"\sigma", "τ": r"\tau", "υ": r"\upsilon",
    "φ": r"\phi", "χ": r"\chi", "ψ": r"\psi", "ω": r"\omega",
    "Γ": r"\Gamma", "Δ": r"\Delta", "Θ": r"\Theta", "Λ": r"\Lambda",
    "Ξ": r"\Xi", "Π": r"\Pi", "Σ": r"\Sigma", "Υ": r"\Upsilon",
    "Φ": r"\Phi", "Ψ": r"\Psi", "Ω": r"\Omega",
}

# Unicode → LaTeX command inside math mode ($...$)
MATH_OPS = {
    "×": r"\times", "÷": r"\div", "±": r"\pm", "∓": r"\mp", "·": r"\cdot",
    "−": "-", "≤": r"\le", "≥": r"\ge", "≠": r"\neq", "≈": r"\approx",
    "≡": r"\equiv", "∞": r"\infty", "∑": r"\sum", "∏": r"\prod",
    "∫": r"\int", "∂": r"\partial", "∇": r"\nabla", "∈": r"\in",
    "∉": r"\notin", "⊂": r"\subset", "⊃": r"\supset", "∪": r"\cup",
    "∩": r"\cap", "∅": r"\emptyset", "∀": r"\forall", "∃": r"\exists",
}

# Unicode → inline math outside math mode
INLINE_OPS = {ch: f"${cmd}$" for ch, cmd in MATH_OPS.items()}
INLINE_OPS["−"] = "-"  # minus sign in plain text → ASCII hyphen

# Chars allowed inside an absorbed product expression (unit/values joined by ·)
PRODUCT_CHARS = set("0123456789./%°") | set(SUPERSCRIPT_MAP) | set(SUBSCRIPT_MAP)

# ASCII chars that continue a math expression after ∑/∫/∏ (absorb into one block)
MATH_EXPR_CHARS = set("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ+-=()/,.")

# Full continuation set: ASCII math chars + Unicode operators (∑x² - ∑y² → one block)
MATH_CONTINUE = set(MATH_EXPR_CHARS) | set(MATH_OPS)

# Unicode punctuation → ASCII
PUNCT = {
    "–": "-", "—": "--", "…": "...",
    "‘": "'", "’": "'", "“": '"', "”": '"',
}

# --------------------------------------------------------------------------
# Patterns
# --------------------------------------------------------------------------

MATH_BLOCK = re.compile(r"\$[^$\n]+\$")   # $...$ spans (single-line)

SUPER_RE = re.compile("[" + "".join(SUPERSCRIPT_MAP) + "]")
SUB_RE = re.compile("[" + "".join(SUBSCRIPT_MAP) + "]")
# ±/∓ immediately followed by a value (digits, optional %, optional unit)
VALUE_AFTER_SIGN = re.compile(r"[±∓]\s*(\d[\d.,]*%?)")
# √ + immediate argument: ASCII digits/letters/dot, or a parenthesized
# expression; ASCII-only so superscripts (√x²) stay out of the radical
SQRT_ARG_RE = re.compile(r"√\s*([0-9A-Za-z_.]+|\([^)]*\))")

# Anything non-ASCII that we did not resolve (excluding CJK and CJK punct)
CJK = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff00-\uffef\u2014\u2018\u2019\u201c\u201d]")
LEFTOVER = re.compile(r"[^\x00-\x7f]")

# --------------------------------------------------------------------------
# Pass A: deterministic conversion
# --------------------------------------------------------------------------


def convert_math_mode(text: str) -> str:
    """Convert a $...$ math span: Unicode ops → \\cmd, super/sub → ^/_."""
    out = []
    prev_was_cmd = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in SUPERSCRIPT_MAP or ch in SUBSCRIPT_MAP:
            # merge consecutive sup/sub chars: ᵢ₌₁ⁿ → _{i=1}^n;
            # single char keeps no braces: x² → x^2, aⱼ → a_j
            j = i
            if ch in SUBSCRIPT_MAP:
                while j < n and text[j] in SUBSCRIPT_MAP:
                    j += 1
                chain = "".join(SUBSCRIPT_MAP[c] for c in text[i:j])
                out.append(("_" + chain) if len(chain) == 1 else "_{" + chain + "}")
            else:
                while j < n and text[j] in SUPERSCRIPT_MAP:
                    j += 1
                chain = "".join(SUPERSCRIPT_MAP[c] for c in text[i:j])
                out.append(("^" + chain) if len(chain) == 1 else "^{" + chain + "}")
            prev_was_cmd = False
            i = j
        elif ch in MATH_OPS:
            cmd = MATH_OPS[ch]
            # \sumx would be an unknown command; separate cmd from following letter
            if cmd.startswith("\\") and prev_was_cmd:
                out.append(" ")
            out.append(cmd)
            prev_was_cmd = True
            i += 1
        elif ch in GREEK_LOWER:
            if prev_was_cmd:
                out.append(" ")
            out.append("\\" + GREEK_LOWER[ch])
            prev_was_cmd = True
            i += 1
        elif ch in GREEK_UPPER:
            if prev_was_cmd:
                out.append(" ")
            out.append("\\" + GREEK_UPPER[ch])
            prev_was_cmd = True
            i += 1
        elif ch.isascii() and ch.isalnum():
            if prev_was_cmd:
                out.append(" ")
            out.append(ch)
            prev_was_cmd = False
            i += 1
        else:
            out.append(ch)
            prev_was_cmd = False
            i += 1
    return "".join(out)


def convert_product_expr(expr: str) -> str:
    """Convert a ·/×-joined unit/value expression into LaTeX math.

    Unit letter runs (kg, m, s) are wrapped in \\mathrm{} (SI upright);
    numbers and operators are kept. E.g. "kg·m/s" → \\mathrm{kg}\\cdot\\mathrm{m}/\\mathrm{s};
    "5 · 10³" → 5 \\cdot 10^{3}.
    """
    out: list[str] = []
    i, n = 0, len(expr)
    while i < n:
        ch = expr[i]
        if ch in SUPERSCRIPT_MAP:
            j = i + 1
            while j < n and expr[j] in SUPERSCRIPT_MAP:
                j += 1
            sup = "".join(SUPERSCRIPT_MAP[c] for c in expr[i:j])
            out.append("^" + sup if len(sup) == 1 else "^{" + sup + "}")
            i = j
        elif ch in SUBSCRIPT_MAP:
            j = i + 1
            while j < n and expr[j] in SUBSCRIPT_MAP:
                j += 1
            sub = "".join(SUBSCRIPT_MAP[c] for c in expr[i:j])
            out.append("_" + sub if len(sub) == 1 else "_{" + sub + "}")
            i = j
        elif ch.isascii() and ch.isalpha():
            j = i + 1
            while j < n and expr[j].isascii() and expr[j].isalpha():
                j += 1
            out.append("\\mathrm{" + expr[i:j] + "}")
            i = j
        elif ch == "·":
            out.append(r"\cdot")
            i += 1
        elif ch == "×":
            out.append(r"\times")
            i += 1
        elif ch == "%":
            # % is a comment char in LaTeX; always escape inside math
            out.append(r"\%")
            i += 1
        elif ch == " ":
            out.append(" ")
            i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out).strip()


def _ident_to_math(ident: str) -> str:
    """ΔLOO-IC → \mathrm{LOO\text{-}IC}; δT → T; δ¹³C → ^{13}\mathrm{C}"""
    parts = []
    i = 0
    n = len(ident)
    after_sup = False
    while i < n:
        c = ident[i]
        if c in SUPERSCRIPT_MAP:
            chain = []
            while i < n and ident[i] in SUPERSCRIPT_MAP:
                chain.append(SUPERSCRIPT_MAP[ident[i]])
                i += 1
            parts.append("^{" + "".join(chain) + "}")
            after_sup = True
        elif c in SUBSCRIPT_MAP:
            chain = []
            while i < n and ident[i] in SUBSCRIPT_MAP:
                chain.append(SUBSCRIPT_MAP[ident[i]])
                i += 1
            parts.append("_{" + "".join(chain) + "}")
            after_sup = True
        else:
            run = []
            while i < n and (ident[i].isascii() and (ident[i].isalnum() or ident[i] == "-")):
                run.append(ident[i])
                i += 1
            r = "".join(run)
            if len(r) == 1 and r.isalpha() and not after_sup:
                parts.append(r)  # single-letter variable stays italic
            else:
                # element symbol after isotope superscript (δ¹³C) or acronym
                # run (LOO-IC) → upright \mathrm{}; hyphen is a text hyphen
                parts.append(r"{\mathrm{" + r.replace("-", r"\text{-}") + "}}")
            after_sup = False
    return "".join(parts)


def _ident_to_md(ident: str) -> str:
    """Convert Unicode superscripts/subscripts inside an absorbed identifier
    to Markdown (δ¹³C absorbed → delta^13^C), keeping ASCII as-is."""
    parts = []
    i = 0
    n = len(ident)
    while i < n:
        c = ident[i]
        if c in SUPERSCRIPT_MAP:
            chain = []
            while i < n and ident[i] in SUPERSCRIPT_MAP:
                chain.append(SUPERSCRIPT_MAP[ident[i]])
                i += 1
            parts.append("^" + "".join(chain) + "^")
        elif c in SUBSCRIPT_MAP:
            chain = []
            while i < n and ident[i] in SUBSCRIPT_MAP:
                chain.append(SUBSCRIPT_MAP[ident[i]])
                i += 1
            parts.append("~" + "".join(chain) + "~")
        else:
            parts.append(c)
            i += 1
    return "".join(parts)


def convert_plain(text: str, ambiguous: list[dict], base_offset: int) -> str:
    """Convert non-math text. Returns converted string; appends ambiguous hits
    with offsets relative to the final assembled output (base_offset + len(out))."""
    out: list[str] = []
    out_len = 0
    i = 0
    n = len(text)

    def mark(ch: str, reason: str, suggestion: str | None,
             scope: str | None = None) -> None:
        # scope: whole unit the character belongs to (e.g. ΔLOO-IC) — the
        # suggestion then covers the whole scope, so a reviewer never
        # replaces just the character and splits the unit (mixing).
        start = max(0, i - 25)
        ctx = text[start:i + 26].replace("\n", " ")
        ambiguous.append({
            "offset": base_offset + out_len,
            "char": scope or ch,
            "code_point": f"U+{ord((scope or ch)[0]):04X}",
            "reason": reason,
            "suggestion": suggestion,
            "context": ctx,
        })

    def emit_math(block: str) -> None:
        nonlocal out_len
        if out and out[-1].endswith("$"):
            out.append(" ")
            out_len += 1
        out.append(block)
        out_len += len(block)

    while i < n:
        ch = text[i]

        # --- ·-joined product expression (units/values) ---
        # Detect at run start to avoid double-emitting the left operand:
        # kg·m/s → $\mathrm{kg}\cdot\mathrm{m}/\mathrm{s}$; 5 · 10³ → $5 \cdot 10^{3}$
        if ch.isascii() and (ch.isalnum() or ch in PRODUCT_CHARS):
            j = i
            while j < n:
                c = text[j]
                if c in "·×" or c in SUPERSCRIPT_MAP or c in SUBSCRIPT_MAP or \
                   (c.isascii() and (c.isalnum() or c in PRODUCT_CHARS)):
                    j += 1
                elif c == " ":
                    k = j
                    while k < n and text[k] == " ":
                        k += 1
                    # continue absorbing after space only if next token is
                    # a value (digit/super/sub) or ·/×, not a bare unit
                    if k < n and (text[k] in "·×" or text[k] in SUPERSCRIPT_MAP or
                                  text[k] in SUBSCRIPT_MAP or
                                  (text[k].isascii() and text[k].isdigit())):
                        j = k
                        continue
                    break
                else:
                    break
            run = text[i:j]
            # trim dangling ·/× (no operand after it, e.g. "A · B" → "A")
            while run and run[-1] in "·× ":
                run = run[:-1]
            run = run.strip()
            if "·" in run:
                block = "$" + convert_product_expr(run) + "$"
                mark("·", "unit product: letters assumed SI units (upright); verify variables are italic",
                     "units → \\mathrm{}, variables → italic")
                emit_math(block)
                i += len(run)
                continue

        # --- punctuation (deterministic) ---
        if ch in PUNCT:
            out.append(PUNCT[ch])
            out_len += 1
            i += 1
            continue

        # --- superscript/subscript: formula (letter/= subscript) → LaTeX, else Markdown ---
        if ch in SUPERSCRIPT_MAP or ch in SUBSCRIPT_MAP:
            sub_chain, sup_chain = [], []
            j = i
            while j < n and text[j] in SUBSCRIPT_MAP:
                sub_chain.append(SUBSCRIPT_MAP[text[j]])
                j += 1
            while j < n and text[j] in SUPERSCRIPT_MAP:
                sup_chain.append(SUPERSCRIPT_MAP[text[j]])
                j += 1
            letters = [v for v in sub_chain + sup_chain if v.isalpha()]
            has_eq = "=" in sub_chain or "=" in sup_chain
            if letters or has_eq:
                # confirmed formula (aⱼ → $a_j$, xᵢ₌₁ⁿ → $x_{i=1}^n$);
                # look back for the ASCII base variable already emitted
                k = i
                while k > 0 and text[k - 1].isascii() and text[k - 1].isalnum():
                    k -= 1
                base = text[k:i]
                if base and out and "".join(out[-len(base):]) == base:
                    del out[-len(base):]
                    out_len -= len(base)
                block = "$" + base
                if sub_chain:
                    s = "".join(sub_chain)
                    block += ("_" + s) if len(s) == 1 else "_{" + s + "}"
                if sup_chain:
                    s = "".join(sup_chain)
                    block += ("^" + s) if len(s) == 1 else "^{" + s + "}"
                block += "$"
                emit_math(block)
                i = j
                continue
            # uncertain/plain (H₂O, m², s⁻¹) → Markdown
            if sub_chain:
                out.append("~" + "".join(sub_chain) + "~")
                out_len += len(sub_chain) + 2
            if sup_chain:
                out.append("^" + "".join(sup_chain) + "^")
                out_len += len(sup_chain) + 2
            i = j
            continue

        # --- Greek letters: plain text → English name ---
        # math vs text 归属是语义判断（效率 η → $\eta$；beta 测试 → beta），
        # 脚本一律标记进 amb.json，由 LLM 按决策树二次判断，不静默决定。
        if ch in GREEK_LOWER or ch in GREEK_UPPER:
            name = GREEK_LOWER[ch] if ch in GREEK_LOWER else GREEK_UPPER[ch]
            latex = GREEK_LATEX.get(ch)
            # Scope lookahead: Δ/δ prefixing an ASCII identifier (ΔLOO-IC,
            # ΔT, δ¹³C) — the Greek letter's scope covers the whole
            # identifier, not the letter alone. Suggest the whole form so a
            # reviewer never produces a lone $\Delta$ glued to bare text.
            if ch in "Δδ" and i + 1 < n:
                j = i + 1
                while j < n:
                    c = text[j]
                    if c in SUPERSCRIPT_MAP or c in SUBSCRIPT_MAP:
                        j += 1
                    elif c.isascii() and (c.isalnum() or c == "-"):
                        j += 1
                    else:
                        break
                if j > i + 1:
                    ident = text[i + 1:j]
                    scope = ch + ident
                    math_suffix = _ident_to_math(ident)
                    md_suffix = _ident_to_md(ident)
                    text_suffix = name + " " + md_suffix
                    # \Delta + bare letter needs a space (\Delta T), otherwise
                    # \DeltaT parses as an undefined command in LaTeX
                    sep = " " if math_suffix[:1].isalpha() else ""
                    suggestion = (f"${latex}{sep}{math_suffix}$ (math) or "
                                  f"{text_suffix} (plain text)")
                    mark(ch, "Greek prefix + identifier: one scope "
                             "(ΔLOO-IC → $\\Delta\\mathrm{LOO\\text{-}IC}$)",
                         suggestion, scope=scope)
                    out.append(name + md_suffix)
                    out_len += len(name) + len(md_suffix)
                    i = j
                    continue
            suggestion = f"${latex}$ (math) or {name} (plain text)" if latex else f"{name} (plain text)"
            mark(ch, "Greek letter: math vs plain text", suggestion)
            out.append(name)
            out_len += len(name)
            i += 1
            continue

        # --- √ with argument (√5, √x, √(x+1)) → $\sqrt{...}$ ---
        if ch == "√":
            m = SQRT_ARG_RE.match(text, i)
            if m:
                arg = m.group(1)
                j = m.end()
                # absorb trailing superscript into the radical (√x² → $\sqrt{x^2}$)
                sup = ""
                while j < n and text[j] in SUPERSCRIPT_MAP:
                    sup += SUPERSCRIPT_MAP[text[j]]
                    j += 1
                if sup:
                    arg += "^{" + sup + "}"
                block = r"$\sqrt{" + arg + "}$"
                emit_math(block)
                i = j
                continue
            mark("√", "square root: argument expected (√5 → $\\sqrt{5}$)", r"$\sqrt{<arg>}$")
            emit_math(r"$\sqrt{}$")
            i += 1
            continue

        # --- math operators outside math mode ---
        if ch in INLINE_OPS:
            # ∑/∫/∏ absorb the following expression into one math block
            # (∑x² → $\sum x^2$, ∫0¹ x² dx → $\int 0^1 x^2 dx$) to avoid
            # mixing LaTeX operators with Markdown superscripts
            if ch in "∑∫∏":
                cmd = MATH_OPS[ch]
                parts: list[str] = []
                cur: list[str] = []
                first = True
                j = i + 1

                def flush_block() -> None:
                    nonlocal first
                    expr = "".join(cur).strip()
                    cur.clear()
                    if not expr:
                        return
                    # trailing binary op absorbed but not followed by an operand
                    # (∫0¹ x² dx + ∑yᵢ) belongs outside the math block
                    tail = ""
                    while expr and expr[-1] in "+-=":
                        tail = expr[-1] + tail
                        expr = expr[:-1].strip()
                    if expr:
                        math = convert_math_mode(expr)
                        sep = " " if first and not math.startswith(("_", "^")) else ""
                        parts.append("$" + (cmd if first else "") + sep + math + "$")
                        first = False
                    if tail:
                        parts.append(tail)

                while j < n:
                    c = text[j]
                    if c in SUPERSCRIPT_MAP or c in SUBSCRIPT_MAP or c in MATH_CONTINUE:
                        cur.append(c)
                        j += 1
                    elif c == " ":
                        k = j
                        while k < n and text[k] == " ":
                            k += 1
                        if k < n and (text[k] in SUPERSCRIPT_MAP or text[k] in SUBSCRIPT_MAP or
                                      text[k] in MATH_CONTINUE or
                                      text[k] in "和与及或到至"):
                            cur.append(" ")
                            j = k
                            continue
                        break
                    elif c in "和与及或" or c in "到至":
                        if c in "到至":
                            # summation bounds (∑ i=1 到 n → $\sum_{i=1}^{n}$)
                            lower = "".join(cur).strip()
                            k = j + 1
                            while k < n and text[k] == " ":
                                k += 1
                            upper = ""
                            while k < n and (text[k] in SUPERSCRIPT_MAP or
                                             text[k] in SUBSCRIPT_MAP or
                                             (text[k].isascii() and text[k] in MATH_EXPR_CHARS)):
                                upper += text[k]
                                k += 1
                            upper = upper.strip()
                            if lower and upper:
                                parts.append("$" + cmd + "_{" +
                                             convert_math_mode(lower) + "}^{" +
                                             convert_math_mode(upper) + "}$")
                                first = False
                                cur = []
                                j = k
                                continue
                            break
                        # conjunction (和/与/及/或): close current block, emit
                        # conjunction as text, absorb next expr into its own block
                        flush_block()
                        parts.append(c)
                        j += 1
                        continue
                    else:
                        break
                flush_block()
                block = "".join(parts)
                emit_math(block)
                i = j
                continue
            # × in plain text → ASCII x (dimension descriptions, etc.)
            if ch == "×":
                out.append("x")
                out_len += 1
                i += 1
                continue
            # · between CJK chars (北京·上海) → keep as-is (CJK interpunct
            # is legitimate Unicode punctuation, same family as ，。)
            if ch == "·":
                prev_ch = text[i - 1] if i > 0 else ""
                next_ch = text[i + 1] if i + 1 < n else ""
                is_cjk_sep = (prev_ch.isalpha() and not prev_ch.isascii()) or \
                             (next_ch.isalpha() and not next_ch.isascii())
                if is_cjk_sep:
                    out.append("·")
                    out_len += 1
                    i += 1
                    continue
                # non-CJK stray ·: default to math, flag for LLM verification
                mark("·", "interpunct: CJK separator vs math multiplication",
                     "CJK context → keep ·; math → $\\cdot$")
                emit_math(r"$\cdot$")
                i += 1
                continue
            # ±/∓ followed by a number: absorb value into one math block ($\pm 2\%$)
            if ch in ("±", "∓") and VALUE_AFTER_SIGN.match(text, i):
                m = VALUE_AFTER_SIGN.match(text, i)
                num = m.group(1).replace("%", r"\%")
                block = "$" + MATH_OPS[ch] + " " + num + "$"
                emit_math(block)
                i = m.end()
                continue
            emit_math(INLINE_OPS[ch])
            i += 1
            continue

        out.append(ch)
        out_len += 1
        i += 1

    return "".join(out)


def process(text: str) -> tuple[str, list[dict]]:
    """Main pipeline: math spans + plain text, offsets relative to final output."""
    ambiguous: list[dict] = []

    # Pass 1: convert $...$ math spans (position-aware via re.sub callback)
    math_offsets: list[tuple[int, int]] = []

    def math_cb(m: re.Match) -> str:
        math_offsets.append((m.start(), m.end()))
        return convert_math_mode(m.group(0))

    math_done = MATH_BLOCK.sub(math_cb, text)

    # Pass 2: convert the rest (everything not inside a math span).
    # base_offset tracks the cumulative OUTPUT length so ambiguous offsets
    # land correctly in the final assembled string.
    out_parts = []
    out_len = 0
    cursor = 0
    for start, end in sorted(math_offsets):
        seg = convert_plain(math_done[cursor:start], ambiguous, out_len)
        out_parts.append(seg)
        out_len += len(seg)
        out_parts.append(math_done[start:end])
        out_len += len(math_done[start:end])
        cursor = end
    seg = convert_plain(math_done[cursor:], ambiguous, out_len)
    out_parts.append(seg)
    converted = "".join(out_parts)

    # Pass 3: report any remaining non-ASCII that is not CJK (dedupe by offset)
    reported = {e["offset"] for e in ambiguous}
    for m in LEFTOVER.finditer(converted):
        if m.start() in reported:
            continue
        ch = m.group(0)
        # CJK, ° (degree), and · (U+00B7) are legitimate: CJK interpuncts
        # (列夫·托尔斯泰) are kept by convert_plain; any non-CJK · would
        # already have been converted to $\cdot$ there
        if CJK.match(ch) or ch == "\u00b0" or ch == "\u00b7":
            continue
        ambiguous.append({
            "offset": m.start(),
            "char": ch,
            "code_point": f"U+{ord(ch):04X}",
            "reason": "unresolved non-ASCII character",
            "suggestion": None,
            "context": converted[max(0, m.start() - 25):m.start() + 26].replace("\n", " "),
        })

    return converted, ambiguous


# --------------------------------------------------------------------------
# Mixing (混杂) audit
# --------------------------------------------------------------------------

# LaTeX + Markdown markers mixed inside one unit — the anti-patterns the
# decision tree forbids. Blocks are located with MATH_BLOCK (same $...$
# semantics as the converter) so boundaries never cross spaces or CJK.
SPLIT_FORMULA_RE = re.compile(r"\$[^$\n]+\$\s*[-+]\s*\$[^$\n]+\$")


def check_mixing(text: str) -> list[dict]:
    """Scan text for LaTeX/Markdown mixing patterns (混杂). Returns
    {offset, pattern, match, context} hits sorted by offset."""
    hits = []
    for m in MATH_BLOCK.finditer(text):
        offset = m.start()
        block = m.group(0)
        content = block[1:-1]
        after = text[m.end():m.end() + 1]
        ctx = text[max(0, offset - 25):m.end() + 25].replace("\n", " ")
        if CJK.search(content):
            hits.append({"offset": offset, "pattern": "CJK inside math block",
                         "match": block, "context": ctx})
        if after == "$":
            hits.append({"offset": offset, "pattern": "adjacent math blocks without space",
                         "match": block, "context": ctx})
        elif after.isascii() and after.isalnum():
            hits.append({"offset": offset, "pattern": "math block glued to bare text",
                         "match": block, "context": ctx})
        elif after and after in "^~":
            hits.append({"offset": offset, "pattern": "math block followed by Markdown super/subscript",
                         "match": block, "context": ctx})
        if SPLIT_FORMULA_RE.match(text, offset):
            hits.append({"offset": offset, "pattern": "operator between math blocks (split formula)",
                         "match": SPLIT_FORMULA_RE.match(text, offset).group(0),
                         "context": ctx})
    hits.sort(key=lambda h: h["offset"])
    return hits


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="input file (use - for stdin)")
    ap.add_argument("-o", "--output", help="output file (default: stdout)")
    ap.add_argument("--amb-out", help="write ambiguity report JSON to this file")
    ap.add_argument("--in-place", action="store_true",
                    help="overwrite the input file (also writes <input>.amb.json)")
    ap.add_argument("--json", action="store_true",
                    help="print machine-readable JSON summary instead of text")
    ap.add_argument("--no-ambiguous-marker", action="store_true",
                    help="do not embed [AMBIGUOUS ...] markers in the text output")
    ap.add_argument("--check-mixing", action="store_true",
                    help="scan output for LaTeX/Markdown mixing patterns; "
                         "exit 2 if any found")
    args = ap.parse_args()

    if args.input == "-":
        text = sys.stdin.read()
        src_name = "<stdin>"
    else:
        src = Path(args.input)
        if not src.exists():
            print(f"error: no such file: {args.input}", file=sys.stderr)
            return 1
        text = src.read_text(encoding="utf-8")
        src_name = str(src)

    converted, ambiguous = process(text)

    # Embed markers unless suppressed
    if ambiguous and not args.no_ambiguous_marker and not args.json:
        markers = {e["offset"]: f"[AMBIGUOUS: {e['code_point']} {e['char']}]"
                   for e in ambiguous}
        pieces = []
        for idx, ch in enumerate(converted):
            pieces.append(ch)
            if idx in markers:
                pieces.append(markers[idx])
        converted = "".join(pieces)

    if args.json:
        stats = {
            "source": src_name,
            "ambiguous_count": len(ambiguous),
            "ambiguous": ambiguous,
            "mixing_count": 0,
            "mixing": [],
            "output": converted,
        }
        if args.check_mixing:
            mixing = check_mixing(converted)
            stats["mixing_count"] = len(mixing)
            stats["mixing"] = mixing
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    elif args.output:
        Path(args.output).write_text(converted, encoding="utf-8")
        print(f"wrote {args.output} ({len(converted)} chars, "
              f"{len(ambiguous)} ambiguous)", file=sys.stderr)
    else:
        print(converted, end="")

    if args.check_mixing and not args.json:
        mixing = check_mixing(converted)
        if mixing:
            print(f"{len(mixing)} mixing violation(s):", file=sys.stderr)
            for h in mixing:
                print(f"  @{h['offset']}: {h['pattern']}  "
                      f"match={h['match']!r}  ...{h['context']}...",
                      file=sys.stderr)
            return 2

    if args.amb_out:
        Path(args.amb_out).write_text(
            json.dumps(ambiguous, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.in_place and args.input != "-":
        Path(args.input).write_text(converted, encoding="utf-8")
        Path(str(args.input) + ".amb.json").write_text(
            json.dumps(ambiguous, ensure_ascii=False, indent=2), encoding="utf-8")

    return 0


if __name__ == "__main__":
    sys.exit(main())