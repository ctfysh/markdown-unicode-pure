#!/usr/bin/env python3
"""
unicode_purify.py — pure execution engine for the `markdown-unicode-pure` skill.

All judgment belongs to the AI; this script only executes. The AI reads the
input text, finds every problem (Unicode special chars, LaTeX/Markdown mixing,
misused forms such as units/chemical formulas in math), classifies each with a
`kind`, and writes an annotation file. This script validates those annotations
mechanically, renders each replacement from lookup tables, and applies it.
It never scans, detects, or classifies.

Loop (until all problems are resolved):
  1. AI: read <input>; classify every problem; write annotations.json
     [{"offset": 10, "scope": "m²", "kind": "markdown_super"}, ...]
  2. Python:
       python3 unicode_purify.py <input> --annotations ann.json -o out.md
     validate (offset+scope must match the input text, kind must be known,
     scopes must not overlap) -> render -> apply. Invalid annotations abort
     with an error; nothing is guessed.
  3. AI: read <output>; if problems remain, write new/changed annotations and
     return to step 2. Python can list remaining non-ASCII characters as
     factual evidence (--leftover) to help the AI check.

Kinds (AI classifies; Python renders mechanically):
  unicode kinds:   markdown_super markdown_sub math_super_sub
                   greek_math greek_text greek_prefix_math greek_prefix_md
                   math_op op_value math_expr sum_limits
                   dimension_x math_x product interpunct sqrt punct keep
  structural:      merge_math space_blocks chem_to_md unit_to_md
                   ordinal_plain letter_sub_math

Usage:
  python3 unicode_purify.py <input> --annotations ann.json -o out.md [--json]
  python3 unicode_purify.py <input> --leftover    # factual non-ASCII listing
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# Character maps (lookup tables; the mechanical "how to change" knowledge)
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

# Unicode punctuation → ASCII
PUNCT = {
    "–": "-", "—": "--", "…": "...",
    "‘": "'", "’": "'", "“": '"', "”": '"',
}

# Characters that are legitimately non-ASCII and stay untouched
VALID_UNICODE = set("\u00b0\u00b7")  # degree, interpunct

# --------------------------------------------------------------------------
# Mechanical renderers: kind + scope → replacement string
# The AI supplies (offset, scope, kind); these functions never decide what a
# scope means, only how to write it in the target syntax.
# --------------------------------------------------------------------------


def _render_markdown_super(scope: str) -> str:
    """m² → m^2^, ¹⁴C → ^14^C, s⁻¹ → s^-1^ (digit superscripts → ^N^)."""
    out, i, n = [], 0, len(scope)
    while i < n:
        c = scope[i]
        if c in SUPERSCRIPT_MAP:
            j = i + 1
            while j < n and scope[j] in SUPERSCRIPT_MAP:
                j += 1
            out.append("^" + "".join(SUPERSCRIPT_MAP[x] for x in scope[i:j]) + "^")
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _render_markdown_sub(scope: str) -> str:
    """H₂O → H~2~O (digit subscripts → ~N~)."""
    out, i, n = [], 0, len(scope)
    while i < n:
        c = scope[i]
        if c in SUBSCRIPT_MAP:
            j = i + 1
            while j < n and scope[j] in SUBSCRIPT_MAP:
                j += 1
            out.append("~" + "".join(SUBSCRIPT_MAP[x] for x in scope[i:j]) + "~")
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _render_math_super_sub(scope: str) -> str:
    """aⱼ → $a_j$, xᵢ₌₁ⁿ → $x_{i=1}^{n}$ (letter/= super/sub → LaTeX)."""
    return "$" + convert_math_mode(scope) + "$"


def _render_greek_math(scope: str) -> str:
    """η → $\\eta$ (math context)."""
    return "$" + GREEK_LATEX[scope[0]] + "$"


def _render_greek_text(scope: str) -> str:
    """η → eta (plain text context)."""
    ch = scope[0]
    return GREEK_LOWER.get(ch) or GREEK_UPPER.get(ch)


def _render_greek_prefix_math(scope: str) -> str:
    """ΔLOO-IC → $\\Delta\\mathrm{LOO\\text{-}IC}$; δ¹³C → $\\delta^{13}\\mathrm{C}$."""
    ch, ident = scope[0], scope[1:]
    suffix = _ident_to_math(ident)
    sep = " " if suffix[:1].isalpha() else ""  # \Delta T, never \DeltaT
    return f"${GREEK_LATEX[ch]}{sep}{suffix}$"


def _render_greek_prefix_md(scope: str) -> str:
    """ΔLOO-IC → DeltaLOO-IC; δ¹³C → delta^13^C (plain text form)."""
    ch, ident = scope[0], scope[1:]
    name = GREEK_LOWER.get(ch) or GREEK_UPPER.get(ch)
    return name + _ident_to_md(ident)


def _render_math_op(scope: str) -> str:
    """± → $\\pm$ (standalone operator)."""
    return INLINE_OPS[scope[0]]


def _render_op_value(scope: str) -> str:
    """±2% → $\\pm 2\\%$ (operator + value absorbed)."""
    ch, rest = scope[0], scope[1:]
    num = rest.replace("%", r"\%")
    return f"${MATH_OPS[ch]} {num}$"


def _render_math_expr(scope: str) -> str:
    """∑x² - ∑y² → $\\sum x^2 - \\sum y^2$ (whole expression, one block)."""
    return "$" + convert_math_mode(scope) + "$"


def _render_sum_limits(scope: str) -> str:
    """∑ i=1 到 n → $\\sum_{i=1}^{n}$ (到/至 introduce bounds)."""
    m = re.match(r"([∑∏∫])\s*(.*?)\s*(?:到|至)\s*(.*)", scope)
    if not m:
        return scope
    cmd = MATH_OPS[m.group(1)]
    lower = convert_math_mode(m.group(2))
    upper = convert_math_mode(m.group(3))
    return f"${cmd}_{{{lower}}}^{{{upper}}}$"


def _render_dimension_x(scope: str) -> str:
    """× in dimensions → x (2.0 m × 1.4 m → 2.0 m x 1.4 m)."""
    return "x"


def _render_math_x(scope: str) -> str:
    """× in math → $\\times$."""
    return r"$\times$"


def _render_product(scope: str) -> str:
    """kg·m/s → $\\mathrm{kg}\\cdot\\mathrm{m}/\\mathrm{s}$ (whole product)."""
    return "$" + convert_product_expr(scope) + "$"


def _render_interpunct(scope: str) -> str:
    """· between CJK (北京·上海) → keep as-is."""
    return scope


def _render_sqrt(scope: str) -> str:
    """√5 → $\\sqrt{5}$ (whole argument absorbed)."""
    return r"$\sqrt{" + scope[1:] + "}$"


def _render_punct(scope: str) -> str:
    """– → -, … → ... (Unicode punctuation → ASCII)."""
    return PUNCT[scope[0]]


def _render_keep(scope: str) -> str:
    """1st, 2024, Figure 1 → unchanged (already correct)."""
    return scope


# Commands the merge renderer can safely split apart when glued to a letter or
# digit (\sumx → \sum x, \pm2 → \pm 2). Unknown commands are left untouched.
KNOWN_COMMANDS = (
    sorted({v for v in MATH_OPS.values() if v.startswith("\\")},
           key=len, reverse=True)
    + [r"\\" + n for n in GREEK_LOWER.values()]
    + [r"\\" + n for n in GREEK_UPPER.values()]
)


def _render_merge_math(scope: str) -> str:
    """Merge a split/mixed unit into one math block:
    $\\sum$x^2^ → $\\sum x^2$; $\\pm$2% → $\\pm 2\\%$."""
    inner = scope.replace("$", "")
    # Markdown super/sub markers → LaTeX (single char keeps no braces)
    inner = re.sub(r"\^([^^~]+)\^",
                   lambda m: "^" + m.group(1) if len(m.group(1)) == 1
                   else "^{" + m.group(1) + "}", inner)
    inner = re.sub(r"~([^^~]+)~",
                   lambda m: "_" + m.group(1) if len(m.group(1)) == 1
                   else "_{" + m.group(1) + "}", inner)
    inner = convert_math_mode(inner)
    inner = inner.replace("%", r"\%")  # % is a comment char in LaTeX
    for cmd in KNOWN_COMMANDS:
        # split a known command glued to a letter/digit; never split \pm into
        # \p m (a plain `\\[a-zA-Z]+(?=[a-zA-Z])` regex backtracks into this)
        inner = re.sub(re.escape(cmd) + r"(?=[0-9a-zA-Z])",
                       lambda _: cmd + " ", inner)
    return "$" + inner + "$"


def _render_space_blocks(scope: str) -> str:
    """$a_i$$b_j$ → $a_i$ $b_j$ (adjacent math blocks get a space)."""
    return re.sub(r"(\$[^$\n]+\$)(\$)", r"\1 \2", scope)


def _render_chem_to_md(scope: str) -> str:
    """$H_2O$ → H~2~O (chemical formula wrongly in LaTeX → Markdown)."""
    inner = scope.strip("$")
    inner = re.sub(r"_(\d+)", r"~\1~", inner)
    inner = re.sub(r"\^(\d+)", r"^\1^", inner)
    return inner


def _render_unit_to_md(scope: str) -> str:
    """$10 m^2$ → 10 m^2^ (unit wrongly in LaTeX → Markdown)."""
    inner = scope.strip("$")
    inner = re.sub(r"\^(\d+)", r"^\1^", inner)
    inner = re.sub(r"_(\d+)", r"~\1~", inner)
    return inner


def _render_ordinal_plain(scope: str) -> str:
    """1^st^ → 1st (ordinal superscript → plain text)."""
    return re.sub(r"\^([^^~]+)\^", r"\1", scope)


def _render_letter_sub_math(scope: str) -> str:
    """x~i~ → $x_i$ (letter subscript wrongly as Markdown → LaTeX)."""
    inner = re.sub(r"~([^~]+)~",
                   lambda m: "_" + m.group(1) if len(m.group(1)) == 1
                   else "_{" + m.group(1) + "}", scope)
    return "$" + convert_math_mode(inner) + "$"


RENDERERS = {
    "markdown_super": _render_markdown_super,
    "markdown_sub": _render_markdown_sub,
    "math_super_sub": _render_math_super_sub,
    "greek_math": _render_greek_math,
    "greek_text": _render_greek_text,
    "greek_prefix_math": _render_greek_prefix_math,
    "greek_prefix_md": _render_greek_prefix_md,
    "math_op": _render_math_op,
    "op_value": _render_op_value,
    "math_expr": _render_math_expr,
    "sum_limits": _render_sum_limits,
    "dimension_x": _render_dimension_x,
    "math_x": _render_math_x,
    "product": _render_product,
    "interpunct": _render_interpunct,
    "sqrt": _render_sqrt,
    "punct": _render_punct,
    "keep": _render_keep,
    "merge_math": _render_merge_math,
    "space_blocks": _render_space_blocks,
    "chem_to_md": _render_chem_to_md,
    "unit_to_md": _render_unit_to_md,
    "ordinal_plain": _render_ordinal_plain,
    "letter_sub_math": _render_letter_sub_math,
}


# --------------------------------------------------------------------------
# Execution: validate → render → apply
# --------------------------------------------------------------------------


def validate_annotations(text: str, annotations: list[dict]) -> list[str]:
    """Check every annotation against the input text. Returns error strings
    (empty = valid). A mis-scoped annotation aborts; the script never guesses."""
    errors: list[str] = []
    seen: list[tuple[int, int]] = []
    for idx, ann in enumerate(annotations):
        if "offset" not in ann or "scope" not in ann or "kind" not in ann:
            errors.append(f"#{idx}: missing offset/scope/kind")
            continue
        off, scope, kind = ann["offset"], ann["scope"], ann["kind"]
        if not isinstance(off, int) or off < 0 or off >= len(text):
            errors.append(f"#{idx}: offset {off} out of range (len={len(text)})")
            continue
        if text[off:off + len(scope)] != scope:
            errors.append(f"#{idx}: scope {scope!r} does not match text at "
                          f"offset {off} ({text[off:off + 12]!r}...)")
            continue
        if kind not in RENDERERS:
            errors.append(f"#{idx}: unknown kind {kind!r}")
            continue
        for so, se in seen:
            if not (off + len(scope) <= so or se <= off):
                errors.append(f"#{idx}: scope overlaps annotation at {so}")
        seen.append((off, off + len(scope)))
    return errors


def apply_annotations(text: str, annotations: list[dict]) -> str:
    """Render and apply validated annotations. Offsets refer to the ORIGINAL
    text, so replacements are applied right-to-left to keep them stable."""
    plan = []
    for ann in annotations:
        scope, kind = ann["scope"], ann["kind"]
        plan.append((ann["offset"], ann["offset"] + len(scope),
                     RENDERERS[kind](scope)))
    plan.sort(key=lambda p: p[0], reverse=True)  # apply from the end
    out = text
    for start, end, repl in plan:
        out = out[:start] + repl + out[end:]
    return out


def _is_cjk(ch: str) -> bool:
    """True for CJK ideographs/punctuation/fullwidth forms — never conversion
    targets of this script (no RENDERERS kind handles them)."""
    o = ord(ch)
    return (0x3000 <= o <= 0x303F or 0x3400 <= o <= 0x4DBF or
            0x4E00 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF or
            0xFF00 <= o <= 0xFFEF)


def leftover_nonascii(text: str) -> list[dict]:
    """Factual listing of non-ASCII characters that could need conversion
    (excluding CJK and valid symbols) — evidence for the AI's final check,
    not judgment."""
    hits = []
    for m in re.finditer(r"[^\x00-\x7f]", text):
        ch = m.group(0)
        if ch in VALID_UNICODE or _is_cjk(ch):
            continue
        hits.append({
            "offset": m.start(),
            "char": ch,
            "code_point": f"U+{ord(ch):04X}",
            "context": text[max(0, m.start() - 25):m.start() + 25].replace("\n", " "),
        })
    return hits


# --------------------------------------------------------------------------
# Math-mode renderers (shared by several kinds)
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
            # single char keeps no braces: x² → x^2, aⱼ → a_j, xₙ² → x_n^2
            is_sub = ch in SUBSCRIPT_MAP
            table = SUBSCRIPT_MAP if is_sub else SUPERSCRIPT_MAP
            j = i
            while j < n and text[j] in table:
                j += 1
            chain = "".join(table[c] for c in text[i:j])
            mark = "_" if is_sub else "^"
            if len(chain) == 1:
                out.append(mark + chain)
            else:
                out.append(mark + "{" + chain + "}")
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
    """ΔLOO-IC → \\mathrm{LOO\\text{-}IC}; δT → T; δ¹³C → ^{13}\\mathrm{C}"""
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
                # element symbol after isotope superscript (δ¹³C) is grouped
                # {\mathrm{C}}; a standalone acronym run (LOO-IC) is not
                inner = r"\mathrm{" + r.replace("-", r"\text{-}") + "}"
                parts.append("{" + inner + "}" if after_sup else inner)
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


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="input file (use - for stdin)")
    ap.add_argument("--annotations", metavar="JSON",
                    help="apply AI-classified annotations to the input")
    ap.add_argument("-o", "--output", help="output file (default: stdout)")
    ap.add_argument("--json", action="store_true",
                    help="print machine-readable JSON report instead of text")
    ap.add_argument("--leftover", action="store_true",
                    help="list remaining non-ASCII chars as factual evidence")
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

    if args.annotations is None:
        # Pure evidence mode: list non-ASCII characters for the AI's check.
        report = {"source": src_name, "leftover": leftover_nonascii(text)}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    # Execution mode: validate, render, apply.
    ann_path = Path(args.annotations)
    if not ann_path.exists():
        print(f"error: no such annotations file: {args.annotations}", file=sys.stderr)
        return 1
    annotations = json.loads(ann_path.read_text(encoding="utf-8"))
    if isinstance(annotations, dict) and "annotations" in annotations:
        annotations = annotations["annotations"]
    if not isinstance(annotations, list):
        print("error: annotations must be a JSON array of "
              "{offset, scope, kind} objects", file=sys.stderr)
        return 1

    errors = validate_annotations(text, annotations)
    if errors:
        print(f"error: {len(errors)} invalid annotation(s):", file=sys.stderr)
        for e in errors[:20]:
            print(f"  {e}", file=sys.stderr)
        return 1

    converted = apply_annotations(text, annotations)

    if args.json:
        print(json.dumps({
            "source": src_name,
            "applied": len(annotations),
            "output": converted,
            "leftover": leftover_nonascii(converted),
        }, ensure_ascii=False, indent=2))
    elif args.output:
        Path(args.output).write_text(converted, encoding="utf-8")
        print(f"wrote {args.output} ({len(converted)} chars, "
              f"{len(annotations)} applied)", file=sys.stderr)
    else:
        print(converted, end="")

    return 0


if __name__ == "__main__":
    sys.exit(main())