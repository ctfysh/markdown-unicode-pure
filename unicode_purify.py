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


def convert_math_mode(text: str) -> tuple[str, int]:
    """Convert a $...$ math span: Unicode ops → \\cmd, super/sub → ^/_."""
    out = []
    prev_was_cmd = False
    for ch in text:
        if ch in SUPERSCRIPT_MAP:
            out.append("^" + SUPERSCRIPT_MAP[ch])
            prev_was_cmd = False
        elif ch in SUBSCRIPT_MAP:
            out.append("_" + SUBSCRIPT_MAP[ch])
            prev_was_cmd = False
        elif ch in MATH_OPS:
            cmd = MATH_OPS[ch]
            # \sumx would be an unknown command; separate cmd from following letter
            if cmd.startswith("\\") and prev_was_cmd:
                out.append(" ")
            out.append(cmd)
            prev_was_cmd = True
        elif ch in GREEK_LOWER:
            if prev_was_cmd:
                out.append(" ")
            out.append("\\" + GREEK_LOWER[ch])
            prev_was_cmd = True
        elif ch in GREEK_UPPER:
            if prev_was_cmd:
                out.append(" ")
            out.append("\\" + GREEK_UPPER[ch])
            prev_was_cmd = True
        elif ch.isascii() and ch.isalnum():
            if prev_was_cmd:
                out.append(" ")
            out.append(ch)
            prev_was_cmd = False
        else:
            out.append(ch)
            prev_was_cmd = False
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
            out.append("^{" + sup + "}")
            i = j
        elif ch in SUBSCRIPT_MAP:
            j = i + 1
            while j < n and expr[j] in SUBSCRIPT_MAP:
                j += 1
            sub = "".join(SUBSCRIPT_MAP[c] for c in expr[i:j])
            out.append("_{" + sub + "}")
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


def convert_plain(text: str, ambiguous: list[dict], base_offset: int) -> str:
    """Convert non-math text. Returns converted string; appends ambiguous hits
    with offsets relative to the final assembled output (base_offset + len(out))."""
    out: list[str] = []
    out_len = 0
    i = 0
    n = len(text)

    def mark(ch: str, reason: str, suggestion: str | None) -> None:
        start = max(0, i - 25)
        ctx = text[start:i + 26].replace("\n", " ")
        ambiguous.append({
            "offset": base_offset + out_len,
            "char": ch,
            "code_point": f"U+{ord(ch):04X}",
            "reason": reason,
            "suggestion": suggestion,
            "context": ctx,
        })

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
                out.append(block)
                out_len += len(block)
                i += len(run)
                continue

        # --- punctuation (deterministic) ---
        if ch in PUNCT:
            out.append(PUNCT[ch])
            out_len += 1
            i += 1
            continue

        # --- superscript: always Markdown ^N^ (any context) ---
        if ch in SUPERSCRIPT_MAP:
            j = i + 1
            while j < n and text[j] in SUPERSCRIPT_MAP:
                j += 1
            digits = "".join(SUPERSCRIPT_MAP[c] for c in text[i:j])
            out.append("^" + digits + "^")
            out_len += len(digits) + 2
            i = j
            continue

        # --- subscript: always Markdown ~N~ (any context) ---
        if ch in SUBSCRIPT_MAP:
            j = i + 1
            while j < n and text[j] in SUBSCRIPT_MAP:
                j += 1
            digits = "".join(SUBSCRIPT_MAP[c] for c in text[i:j])
            out.append("~" + digits + "~")
            out_len += len(digits) + 2
            i = j
            continue

        # --- Greek letters: plain text → English name ---
        # math vs text 归属是语义判断（效率 η → $\eta$；beta 测试 → beta），
        # 脚本一律标记进 amb.json，由 LLM 按决策树二次判断，不静默决定。
        if ch in GREEK_LOWER or ch in GREEK_UPPER:
            name = GREEK_LOWER[ch] if ch in GREEK_LOWER else GREEK_UPPER[ch]
            latex = GREEK_LATEX.get(ch)
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
                out.append(block)
                out_len += len(block)
                i = j
                continue
            mark("√", "square root: argument expected (√5 → $\\sqrt{5}$)", r"$\sqrt{<arg>}$")
            out.append(r"$\sqrt{}$")
            out_len += len(r"$\sqrt{}$")
            i += 1
            continue

        # --- math operators outside math mode ---
        if ch in INLINE_OPS:
            # ∑/∫/∏ absorb the following expression into one math block
            # (∑x² → $\sum x^2$, ∫0¹ x² dx → $\int 0^1 x^2 dx$) to avoid
            # mixing LaTeX operators with Markdown superscripts
            if ch in "∑∫∏":
                j = i + 1
                while j < n:
                    c = text[j]
                    if c in SUPERSCRIPT_MAP or c in SUBSCRIPT_MAP or \
                       (c.isascii() and c in MATH_EXPR_CHARS):
                        j += 1
                    elif c == " ":
                        k = j
                        while k < n and text[k] == " ":
                            k += 1
                        if k < n and (text[k] in SUPERSCRIPT_MAP or text[k] in SUBSCRIPT_MAP or
                                      (text[k].isascii() and text[k] in MATH_EXPR_CHARS)):
                            j = k
                            continue
                        break
                    else:
                        break
                expr = text[i:j]
                block = "$" + convert_math_mode(expr) + "$"
                out.append(block)
                out_len += len(block)
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
                out.append(r"$\cdot$")
                out_len += len(r"$\cdot$")
                i += 1
                continue
            # ±/∓ followed by a number: absorb value into one math block ($\pm 2\%$)
            if ch in ("±", "∓") and VALUE_AFTER_SIGN.match(text, i):
                m = VALUE_AFTER_SIGN.match(text, i)
                num = m.group(1).replace("%", r"\%")
                block = "$" + MATH_OPS[ch] + " " + num + "$"
                out.append(block)
                out_len += len(block)
                i = m.end()
                continue
            out.append(INLINE_OPS[ch])
            out_len += len(INLINE_OPS[ch])
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
            "output": converted,
        }
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    elif args.output:
        Path(args.output).write_text(converted, encoding="utf-8")
        print(f"wrote {args.output} ({len(converted)} chars, "
              f"{len(ambiguous)} ambiguous)", file=sys.stderr)
    else:
        print(converted, end="")

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