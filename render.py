"""Turn LaTeX strings into PIL images for the flashcard GUI.

Three engines, tried in order:
  1. "usetex": real LaTeX through matplotlib (needs latex + dvipng on PATH, which
     any TeX Live / MiKTeX / MacTeX install provides). Uses your handbook's own
     \\newcommand macros, so everything renders exactly as in the PDF.
  2. "mathtext": matplotlib's built-in maths engine (no TeX install needed).
     Your macros are expanded first; a few constructs (matrices) are not supported.
  3. plain text: the raw LaTeX source, so a card is never blank.
Rendered images are cached on disk, keyed by a hash of the text.
"""

import hashlib
import io
import re
import shutil
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from PIL import Image  # noqa: E402

BASE_PREAMBLE = r"\usepackage{amsmath}\usepackage{amssymb}"

# macro -> (number of arguments, replacement) used for the mathtext fallback
MACROS = {
    "ket": (1, r"|#1\rangle"), "bra": (1, r"\langle #1|"), "braket": (2, r"\langle #1|#2\rangle"),
    "mel": (3, r"\langle #1|#2|#3\rangle"), "expval": (1, r"\langle #1\rangle"),
    "comm": (2, r"[#1,#2]"), "acomm": (2, r"\{#1,#2\}"),
    "ah": (0, r"\hat{a}"), "ad": (0, r"\hat{a}^\dagger"), "ch": (0, r"\hat{c}"), "cd": (0, r"\hat{c}^\dagger"),
    "Hh": (0, r"\hat{H}"), "Nh": (0, r"\hat{N}"), "Ph": (0, r"\hat{P}"), "xh": (0, r"\hat{x}"),
    "ph": (0, r"\hat{p}"), "Tr": (0, r"\mathrm{Tr}\,"), "dd": (0, r"\mathrm{d}"), "lbl": (2, r"#1"),
}
SIMPLE = [
    (r"\tfrac", r"\frac"), (r"\dfrac", r"\frac"), (r"\lvert", "|"), (r"\rvert", "|"),
    (r"\iint", r"\int\!\!\int"), (r"\Bigl", ""), (r"\Bigr", ""), (r"\bigl", ""), (r"\bigr", ""),
    (r"\Big", ""), (r"\big", ""), (r"\boldsymbol", r"\mathbf"), (r"\dots", r"\ldots"),
    (r"\coloneqq", ":="), (r"\varepsilon", r"\epsilon"),
]


def _arg(s, i):
    """Read one macro argument at s[i]: {group}, \\command or a single char."""
    while i < len(s) and s[i] == " ":
        i += 1
    if i >= len(s):
        return "", i
    if s[i] == "{":
        depth, j = 0, i
        while j < len(s):
            if s[j] == "\\":
                j += 2
                continue
            depth += {"{": 1, "}": -1}.get(s[j], 0)
            if depth == 0:
                return s[i + 1:j], j + 1
            j += 1
        return s[i + 1:], len(s)
    if s[i] == "\\":
        m = re.match(r"\\([A-Za-z]+|.)", s[i:])
        return m.group(0), i + len(m.group(0))
    return s[i], i + 1


def expand_macros(s, passes=4):
    for _ in range(passes):
        changed = False
        for name, (n, tpl) in MACROS.items():
            pat = re.compile(r"\\" + name + r"(?![A-Za-z])")
            out, j = [], 0
            for m in pat.finditer(s):
                if m.start() < j:
                    continue
                out.append(s[j:m.start()])
                k, args = m.end(), []
                for _ in range(n):
                    a, k = _arg(s, k)
                    args.append(a)
                rep = tpl
                for idx, a in enumerate(args, 1):
                    rep = rep.replace(f"#{idx}", a)
                out.append("{" + rep + "}" if n else rep + " ")
                j = k
                changed = True
            out.append(s[j:])
            s = "".join(out)
        if not changed:
            break
    return s


BRACE_ARGS = {"frac": 2, "tfrac": 2, "dfrac": 2, "sqrt": 1, "binom": 2, "hat": 1, "vec": 1,
              "mathbf": 1, "mathrm": 1, "bar": 1, "tilde": 1, "dot": 1, "ddot": 1}


def brace_args(s):
    """mathtext needs braces: \\frac12 -> \\frac{1}{2}, \\sqrt2 -> \\sqrt{2}."""
    pat = re.compile(r"\\(" + "|".join(BRACE_ARGS) + r")(?![A-Za-z])")
    out, j = [], 0
    for m in pat.finditer(s):
        if m.start() < j:
            continue
        out.append(s[j:m.start()])
        k = m.end()
        opt = ""
        if m.group(1) == "sqrt" and k < len(s) and s[k] == "[":  # \sqrt[n]{x}
            e = s.index("]", k)
            opt, k = s[k:e + 1], e + 1
        args = []
        for _ in range(BRACE_ARGS[m.group(1)]):
            a, k = _arg(s, k)
            args.append("{" + brace_args(a) + "}")
        out.append("\\" + m.group(1) + opt + "".join(args))
        j = k
    out.append(s[j:])
    return "".join(out)


RELATION_NAMES = [(r"\ge", r"\geq"), (r"\le", r"\leq"), (r"\ne", r"\neq")]
ENV = re.compile(r"\\begin\{(pmatrix|bmatrix|matrix|cases)\}(.*?)\\end\{\1\}", re.S)


def stack(rows, left, right):
    """mathtext has no matrices: stack rows with zero-thickness \\genfrac."""
    if len(rows) == 1:
        inner = rows[0]
        return (r"\left" + left + inner + r"\right" + right) if left else inner
    inner = rows[-1]
    for r in reversed(rows[1:-1]):
        inner = r"\genfrac{}{}{0}{}{" + r + "}{" + inner + "}"
    return r"\genfrac{" + left + "}{" + right + "}{0}{}{" + rows[0] + "}{" + inner + "}"


def matrices_to_stacks(s):
    def conv(m):
        kind, body = m.group(1), m.group(2)
        rows = [r.strip() for r in re.split(r"\\\\", body) if r.strip()]
        rows = [r"\ \ ".join(c.strip() for c in r.split("&")) for r in rows]
        delims = {"pmatrix": ("(", ")"), "bmatrix": ("[", "]"), "matrix": ("", ""), "cases": (r"\{", "")}
        return "{" + stack(rows, *delims[kind]) + "}"
    return ENV.sub(conv, s)


def to_mathtext(s):
    s = matrices_to_stacks(s)
    s = re.sub(r"\\(textstyle|displaystyle|scriptstyle)(?![A-Za-z])", "", s)
    s = expand_macros(s)
    for a, b in RELATION_NAMES:
        s = re.sub(re.escape(a) + r"(?![A-Za-z])", lambda _m, b=b: b, s)
    s = brace_args(s)
    for a, b in SIMPLE:
        s = re.sub(re.escape(a) + r"(?![A-Za-z])", lambda _m, b=b: b, s)
    s = re.sub(r"\\operatorname\{([^{}]*)\}", r"\\mathrm{\1}", s)
    s = re.sub(r"\\text\{([^{}]*)\}", lambda m: r"\mathrm{" + m.group(1).replace(" ", r"\ ") + "}", s)
    s = re.sub(r"\\binom\{([^{}]*)\}\{([^{}]*)\}", r"\\left(\\genfrac{}{}{0}{}{\1}{\2}\\right)", s)
    return s


class Renderer:
    def __init__(self, macros=(), cache_dir=".render_cache", fontsize=22, prefer_usetex=True):
        self.cache = Path(cache_dir)
        self.cache.mkdir(exist_ok=True)
        self.fontsize = fontsize
        self.preamble = BASE_PREAMBLE + "".join(
            m for m in macros if "@" not in m and "recipe" not in m and m.startswith("\\newcommand"))
        self.usetex_ok = prefer_usetex and shutil.which("latex") and shutil.which("dvipng")
        if self.usetex_ok:  # probe once
            try:
                self._draw(r"\ket\psi = \frac12", math=True, usetex=True)
            except Exception:
                self.usetex_ok = False
        self.engine = "usetex" if self.usetex_ok else "mathtext"

    # -- drawing -------------------------------------------------------------
    def _draw(self, text, math, usetex, color="black"):
        with plt.rc_context({"text.usetex": usetex, "text.latex.preamble": self.preamble,
                             "mathtext.fontset": "cm", "font.family": "serif"}):
            fig = plt.figure(figsize=(0.01, 0.01))
            if math:
                s = (r"$\displaystyle " + text + "$") if usetex else ("$" + text + "$")
            else:
                s = text
            fig.text(0, 0, s, fontsize=self.fontsize, color=color)
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=110, bbox_inches="tight", pad_inches=0.08,
                        transparent=False, facecolor="white")
            plt.close(fig)
        buf.seek(0)
        return Image.open(buf).convert("RGB")

    def render(self, text, math=True):
        """Return (PIL image, engine used)."""
        key = hashlib.sha1(f"{self.engine}|{math}|{self.fontsize}|{text}".encode()).hexdigest()[:16]
        path = self.cache / f"{key}.png"
        if path.exists():
            return Image.open(path).convert("RGB"), "cache"
        attempts = []
        if self.usetex_ok:
            attempts.append(("usetex", lambda: self._draw(text, math, usetex=True)))
        if math:
            mt = to_mathtext(text)
        else:  # plain text that may contain $...$ snippets
            mt = re.sub(r"\$([^$]*)\$", lambda m: "$" + to_mathtext(m.group(1)) + "$", text)
        attempts.append(("mathtext", lambda: self._draw(mt, math, usetex=False)))
        attempts.append(("raw", lambda: self._draw(text.replace("$", r"\$"), False, usetex=False)))
        for name, fn in attempts:
            try:
                img = fn()
                img.save(path)
                return img, name
            except Exception:
                plt.close("all")
        raise RuntimeError("could not render: " + text)


if __name__ == "__main__":  # quick self-test: python render.py cards.json
    import json
    import sys

    deck = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "cards.json", encoding="utf-8"))
    r = Renderer(deck["meta"].get("preamble", []), cache_dir=".render_cache_test")
    print("engine:", r.engine)
    counts = {}
    for c in deck["cards"]:
        for text, math in ((c["prompt"], not c["prompt_is_text"]), (c["answer"], True)):
            _, used = r.render(text, math)
            counts[used] = counts.get(used, 0) + 1
    print(counts)
