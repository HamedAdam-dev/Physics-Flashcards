"""Build a flashcard deck (cards.json) from a LaTeX handbook.

Usage:
    python extract_cards.py y3_handbook.tex            # writes cards.json
    python extract_cards.py y3_handbook.tex -o my.json

What becomes a card
-------------------
* Every display equation (equation, gather, align, \\[ \\]) is split into single
  facts at top-level line breaks (\\\\), \\qquad and arrows (\\Rightarrow, \\xrightarrow).
* A fact with a relation (=, \\approx, \\le, \\ge, \\propto) becomes an
  "equation" card: you see the left-hand side and must recall the right-hand side.
* A fact wrapped in \\lbl{equation}{name} also becomes a "named" card: you see
  the name (e.g. "Hellmann-Feynman") and must recall the whole equation.
* Two-column table rows of the form  Name & $equation$  become "named" cards
  (this picks up the Key Equations Summary table).

Card ids are a hash of the section and the equation, so re-running the
extractor after editing the handbook keeps your review history for every card
whose equation did not change.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

MATH_ENVS = ("equation", "equation*", "gather", "gather*", "align", "align*", "multline", "multline*")
RELATIONS = [r"\approx", r"\propto", r"\ge", r"\le", r"\sim"]  # tried after '='
ARROWS = [r"\quad\Rightarrow\quad", r"\quad\Longrightarrow\quad", r"\Longrightarrow",
          r"\Rightarrow", r"\iff", r"\longleftrightarrow"]
MAX_FACT_LEN = 260  # longer pieces are usually derivation steps, not facts


# ---------------------------------------------------------------- brace helpers
def read_group(s, i):
    """s[i] == '{'. Return (content, index after the closing brace)."""
    assert s[i] == "{", s[i:i + 20]
    depth, j = 0, i
    while j < len(s):
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1:j], j + 1
        j += 1
    raise ValueError("unbalanced braces near: " + s[i:i + 60])


def split_top(s, sep):
    """Split s on the literal sep, but only at brace depth 0."""
    parts, depth, start, j = [], 0, 0, 0
    while j < len(s):
        c = s[j]
        if c == "\\" and not s.startswith(sep, j):
            j += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif depth == 0 and s.startswith(sep, j):
            # a control word separator must not be followed by a letter (\le vs \left)
            end = j + len(sep)
            if sep[-1].isalpha() and end < len(s) and s[end].isalpha():
                j += 1
                continue
            parts.append(s[start:j])
            start = j = end
            continue
        j += 1
    parts.append(s[start:])
    return parts


def replace_cmd(s, name, nargs, fn):
    """Replace \\name{a1}..{an} (brace args only) with fn(args)."""
    out, j = [], 0
    pat = re.compile(r"\\" + re.escape(name) + r"(?![A-Za-z])")
    while True:
        m = pat.search(s, j)
        if not m:
            out.append(s[j:])
            return "".join(out)
        out.append(s[j:m.start()])
        k, args = m.end(), []
        try:
            for _ in range(nargs):
                while k < len(s) and s[k] in " \n":
                    k += 1
                if k < len(s) and s[k] in "_^":  # \underbrace{x}_{label}
                    k += 1
                arg, k = read_group(s, k)
                args.append(arg)
        except (AssertionError, ValueError, IndexError):
            out.append(s[m.start():m.end()])
            j = m.end()
            continue
        out.append(fn(args))
        j = k


def strip_comments(tex):
    return re.sub(r"(?<!\\)%.*", "", tex)


def clean_text(t):
    """Turn a LaTeX text snippet (heading, label) into plain-ish text."""
    t = re.sub(r"\\(textbf|emph|textit)\{([^{}]*)\}", r"\2", t)
    parts = re.split(r"(\$[^$]*\$)", t)  # only touch text outside $...$
    for i in range(0, len(parts), 2):
        for acc, ch in (('\\"o', "ö"), ('\\"a', "ä"), ('\\"u', "ü"), ("\\'e", "é"), ("\\`e", "è")):
            parts[i] = parts[i].replace(acc, ch)
        parts[i] = (parts[i].replace("``", '"').replace("''", '"').replace("~", " ")
                    .replace("\\&", "&").replace("\\dots", "...").replace("--", "-"))
    t = "".join(parts)
    return " ".join(t.split()).strip(" .:,")


# ---------------------------------------------------------------- fact cleaning
def tidy(piece):
    p = piece.strip()
    p = re.sub(r"\\(label|ref|eqref)\{[^{}]*\}", "", p)
    p = re.sub(r"\\(nonumber|notag)\b", "", p)
    p = re.sub(r"^(\\q?quad|\\,|\\;|\\\s)+", "", p).strip()
    p = re.sub(r"\\text\{\([^{}]*\)\}\s*$", "", p).strip()  # trailing "(linear in the ket)"
    p = re.sub(r"(\\q?quad|\\,|\\;|\\\s|\s)+$", "", p).strip()
    p = re.sub(r"\s*[,.;]+\s*$", "", p).strip()
    p = re.sub(r"(\\\s*)+$", "", p).strip()  # lone trailing backslashes left by "\ \Rightarrow"
    p = re.sub(r"(\\q?quad|\\,|\\;|\\\s|\s)+$", "", p).strip()
    return p


def unwrap_braces(p):
    """Remove \\overbrace{x}^{..} / \\underbrace{x}_{..} decorations, keep x."""
    p = replace_cmd(p, "overbrace", 2, lambda a: a[0])
    p = replace_cmd(p, "underbrace", 2, lambda a: a[0])
    return p


def split_relation(eq):
    """Return (lhs, rel, rhs) at the first top-level relation, or None."""
    for rel in ["="] + RELATIONS:
        parts = split_top(eq, rel)
        if len(parts) >= 2 and parts[0].strip() and parts[1].strip():
            return parts[0].strip(), rel, rel.join(parts[1:]).strip()
    return None


PROTECT = re.compile(r"\\begin\{(pmatrix|bmatrix|vmatrix|matrix|cases|array)\}.*?\\end\{\1\}", re.S)


def facts_from_math(block):
    """Split one display-math block into individual fact strings."""
    # protect matrices / cases: their & and \\ are not fact separators
    saved = []

    def stash(m):
        saved.append(m.group(0))
        return f"\x00{len(saved) - 1}\x00"

    block = PROTECT.sub(stash, block)
    block = re.sub(r"\\\\\[[^\]]*\]", r"\\\\", block)  # \\[4pt] -> \\
    facts = []
    for row in split_top(block, "\\\\"):
        row = row.replace("&", " ")
        for piece in split_top(row, "\\qquad"):
            # split derivation arrows into separate facts
            pieces = [piece]
            for arrow in ARROWS:
                pieces = [q for p in pieces for q in split_top(p, arrow)]
            pieces = [q for p in pieces
                      for q in replace_cmd(p, "xrightarrow", 1, lambda a: "\x01").split("\x01")]
            for q in pieces:
                q = re.sub("\x00(\\d+)\x00", lambda mm: saved[int(mm.group(1))], q)
                q = tidy(q)
                if len(q) >= 5:
                    facts.append(q)
    return facts


NOT_NAMES = {"ket", "its bra", "a number", "phase", "exact", "odd", "even", "bosons", "fermions",
             "spin", "radial", "one-body", "two-body", "plane wave", "energy gap", "taylor"}


def is_real_name(label):
    """Underbrace labels are sometimes names ("Born rule") and sometimes
    annotations ("$=1$", "a number"). Keep only the names."""
    if not label:
        return False
    plain = re.sub(r"\$[^$]*\$", "", label).strip(" ,.;:()")
    if label.lower() in NOT_NAMES or len(re.findall(r"[A-Za-z]", plain)) < 4:
        return False
    return True


MATH_CELL = re.compile(r"\$[^$]+\$(\s*,\s*\$[^$]+\$)*")


# ---------------------------------------------------------------- main scan
TOKEN = re.compile(
    r"\\(?P<sec>section|subsection)\*?\{"
    r"|\\recipe\{"
    r"|\\begin\{(?P<env>equation\*?|gather\*?|align\*?|multline\*?)\}"
    r"|(?P<disp>\\\[)"
    r"|\\begin\{(?P<tab>tabularx?)\}"
    r"|\\textbf\{"
)


def extract(tex_path):
    raw = Path(tex_path).read_text(encoding="utf-8")
    preamble_macros = [m for m in re.findall(r"^\\newcommand\{\\[A-Za-z]+\}.*$", raw, flags=re.M)
                       if "@" not in m and "recipe" not in m and "subsection" not in m]
    tex = strip_comments(raw)
    body = tex[tex.find("\\begin{document}"):]

    section, subsection, group = "Front matter", "", ""
    cards, seen = [], set()

    def add(kind, prompt, answer, name="", expected="", prompt_is_text=False):
        answer = answer.strip()
        key = (kind, answer, prompt)
        if not answer or key in seen:
            return
        seen.add(key)
        topic = " > ".join(x for x in (section, subsection) if x)
        cid = hashlib.sha1(f"{kind}|{topic}|{prompt}|{answer}".encode()).hexdigest()[:12]
        cards.append({
            "id": cid,
            "type": kind,
            "section": section,
            "subsection": subsection,
            "group": group,
            "name": name,
            "prompt": prompt,
            "prompt_is_text": prompt_is_text,
            "expected": expected or answer,
            "answer": answer,
        })

    def handle_fact(fact):
        if len(fact) > MAX_FACT_LEN:
            return
        names, whole_label = [], ""

        # a fact that is exactly \lbl{eq}{name} -> named card
        m = re.fullmatch(r"\\lbl\s*(\{.*)", fact, flags=re.S)
        if m:
            try:
                eq, k = read_group(fact, fact.index("{"))
                name, k2 = read_group(fact, k)
                if fact[k2:].strip() == "":
                    whole_label = clean_text(name)
                    names.append(whole_label)
                    fact = eq
            except (AssertionError, ValueError):
                pass

        # inner \lbl{x}{y}: keep x, remember y as a hint
        def inner(a):
            names.append(clean_text(a[1]))
            return a[0]
        fact = replace_cmd(fact, "lbl", 2, inner)
        fact = unwrap_braces(fact).strip()
        fact = tidy(fact)
        if len(fact) < 5:
            return
        hint = "; ".join(n for n in names if n)

        if is_real_name(whole_label):
            add("named", whole_label, fact, name=whole_label, prompt_is_text=True)

        # chains like A = B = C = D are derivation steps, not facts to memorise
        if len(split_top(fact, "=")) > 3:
            return
        rel = split_relation(fact)
        if rel:
            lhs, op, rhs = rel
            if len(lhs) >= 2 and len(rhs) >= 1:
                add("equation", f"{lhs} {op} \\;?", fact, name=hint, expected=rhs)

    pos = 0
    while True:
        m = TOKEN.search(body, pos)
        if not m:
            break
        start = m.end()
        tok = m.group(0)
        try:
            if m.group("sec"):
                title, pos = read_group(body, start - 1)
                if m.group("sec") == "section":
                    section, subsection = clean_text(title), ""
                else:
                    subsection = clean_text(title)
                group = ""
            elif tok.startswith("\\recipe"):
                title, pos = read_group(body, start - 1)
                subsection, group = clean_text(title), ""
            elif tok.startswith("\\textbf"):
                text, pos = read_group(body, start - 1)
                if "$" not in text and len(text) < 70:
                    group = clean_text(text)
            elif m.group("env"):
                env = m.group("env")
                end = body.index(f"\\end{{{env}}}", start)
                for fact in facts_from_math(body[start:end]):
                    handle_fact(fact)
                pos = end
            elif m.group("disp"):
                end = body.index("\\]", start)
                for fact in facts_from_math(body[start:end]):
                    handle_fact(fact)
                pos = end + 2
            elif m.group("tab"):
                env = m.group("tab")
                end = body.index(f"\\end{{{env}}}", start)
                table = re.sub(r"\\(toprule|midrule|bottomrule)", "", body[start:end])
                for row in split_top(table, "\\\\"):
                    cells = [c.strip() for c in split_top(row, "&")]
                    if len(cells) != 2:
                        continue
                    name, eq = cells
                    if MATH_CELL.fullmatch(eq) and "$" not in name and len(name) > 2:
                        maths = re.sub(r"\$\s*,\s*\$", r",\\quad ", eq).strip("$")
                        add("named", clean_text(name), tidy(maths), name=clean_text(name),
                            prompt_is_text=True)
                pos = end
            else:
                pos = start
        except (ValueError, AssertionError) as exc:
            print(f"warning: skipped a construct near char {m.start()}: {exc}", file=sys.stderr)
            pos = start

    return {"meta": {"source": str(tex_path), "preamble": preamble_macros}, "cards": cards}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tex")
    ap.add_argument("-o", "--out", default="cards.json")
    args = ap.parse_args()
    deck = extract(args.tex)
    Path(args.out).write_text(json.dumps(deck, indent=1, ensure_ascii=False), encoding="utf-8")
    kinds = {}
    for c in deck["cards"]:
        kinds[c["type"]] = kinds.get(c["type"], 0) + 1
    print(f"wrote {len(deck['cards'])} cards to {args.out}  {kinds}")


if __name__ == "__main__":
    main()
