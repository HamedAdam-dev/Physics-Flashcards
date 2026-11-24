# Physics flashcards

An Anki-style recall game. It shows a prompt (the left-hand side of an equation, or the name of a result). You type the answer against a clock, reveal it, then grade yourself. Spaced repetition decides when each card comes back.

## Setup (once)

```bash
pip install matplotlib pillow
python extract_cards.py y3_handbook.tex      # builds cards.json from your handbook
python flashcards.py --prerender             # optional: renders every card once (about 2 min)
python flashcards.py                         # play
```

- **tkinter** ships with Python on Windows and macOS. On Linux, run `sudo apt install python3-tk`.
- **Real LaTeX rendering** is used automatically if `latex` and `dvipng` are on your PATH, plus the `cm-super` fonts (all included in a full TeX Live / MacTeX install; MiKTeX installs them on demand). Otherwise the app falls back to matplotlib's built-in maths engine, which handles everything except that large matrices look cramped. The status bar shows which renderer is in use.

## Playing

| Key | Action |
|---|---|
| Enter | reveal the answer (Shift+Enter = new line) |
| 1 / 2 / 3 / 4 | Again / Hard / Good / Easy |
| F1 | hint: the result's name, if it has one |
| S | suspend a bad card for good (after revealing) |
| Esc | end the session |

- **Review mode:** due cards first, then up to *new* unseen cards. Use this every day.
- **Cram mode:** random cards from the chosen section, with no effect on the schedule. Use this the night before an exam.
- **Timer:** if you run out of time, *Good* and *Easy* count as *Hard*. The goal is instant recall, not slow re-derivation.
- **Typing:** type LaTeX (`\sqrt{\pi/a}`) or plain shorthand (`sqrt(pi/a)`). Your typed answer is rendered under the real one so you can compare. You grade yourself because equivalent answers can be written in many forms.
- `python flashcards.py --stats` lists your most-forgotten and slowest cards.

## How the data is stored

| File | Contents | Edit by hand? |
|---|---|---|
| `cards.json` | the deck, generated from the handbook | no: regenerate it |
| `custom_cards.json` | your own extra cards (example included) | yes |
| `progress.json` | your review history and schedule | no |
| `.render_cache/` | rendered images | no (safe to delete) |

The deck and your progress are kept **separate**. Card ids are a hash of section + equation, so after you edit the handbook and re-run `extract_cards.py`, every unchanged card keeps its history. Only the cards you edited start fresh.

A card looks like this:

```json
{
  "id": "3f9c2a71b0de",
  "type": "equation",
  "section": "Single-particle quantum mechanics: textbook equations",
  "subsection": "Integrals to know by heart",
  "group": "Gaussians",
  "name": "",
  "prompt": "\\int_{-\\infty}^{\\infty}e^{-ax^2}\\dd x = \\;?",
  "prompt_is_text": false,
  "expected": "\\sqrt{\\frac\\pi a}",
  "answer": "\\int_{-\\infty}^{\\infty}e^{-ax^2}\\dd x = \\sqrt{\\frac\\pi a}"
}
```

- `equation` cards show the left-hand side. `named` cards show a name (from your `\lbl{...}{name}` labels and the Key Equations table) and ask for the whole equation.
- Your handbook macros (`\ket`, `\comm`, `\dd`, ...) work in every card.

## Adding your own cards

Add entries to `custom_cards.json`. Only `id`, `prompt` and `answer` are required:

```json
{"id": "custom-ehrenfest", "type": "named", "section": "Custom",
 "prompt": "Ehrenfest theorem", "answer": "\\frac{d}{dt}\\langle A\\rangle = \\frac{i}{\\hbar}\\langle[\\hat H,\\hat A]\\rangle"}
```

Use a new, unique `id` for each card.

## How the extractor picks cards

- Display equations are split at line breaks, `\qquad` and arrows (`\Rightarrow`, `\xrightarrow`) into single facts.
- Facts with `=`, `\approx`, `\le`, `\ge`, `\propto` or `\sim` become `equation` cards.
- Long chains (`A = B = C = D`) are treated as derivation steps and skipped.
- Annotations like `$=1$` or "a number" are not used as names.

It is a heuristic, so a few cards will be odd (e.g. intermediate steps of the oscillator derivation). Press **S** on those once and they never come back.
