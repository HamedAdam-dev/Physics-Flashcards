"""Physics flashcards: see a prompt, type the equation from memory, beat the clock.

    python flashcards.py                 # uses cards.json (+ custom_cards.json if present)
    python flashcards.py --prerender     # render every card once so the game never waits
    python flashcards.py --stats         # print your weakest cards and exit

Keys
    Enter        reveal the answer (Shift+Enter = new line in your answer)
    1 2 3 4      Again / Hard / Good / Easy   (after revealing)
    F1           show a hint (the equation's name, if it has one), before revealing
    S            suspend this card for good (after revealing)
    Esc          end the session
"""

import argparse
import datetime as dt
import json
import os
import random
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from PIL import ImageTk

import srs
from render import Renderer

HERE = Path(__file__).resolve().parent
CARDS = HERE / "cards.json"
CUSTOM = HERE / "custom_cards.json"
PROGRESS = HERE / "progress.json"


# ---------------------------------------------------------------- data
def load_deck():
    deck = json.loads(CARDS.read_text(encoding="utf-8"))
    cards = deck["cards"]
    if CUSTOM.exists():
        extra = json.loads(CUSTOM.read_text(encoding="utf-8"))
        for c in extra:
            c.setdefault("type", "named")
            c.setdefault("section", "Custom")
            c.setdefault("subsection", "")
            c.setdefault("group", "")
            c.setdefault("name", "")
            c.setdefault("prompt_is_text", c["type"] == "named")
            c.setdefault("expected", c["answer"])
        cards = cards + extra
    return deck["meta"], cards


def load_progress():
    if PROGRESS.exists():
        return json.loads(PROGRESS.read_text(encoding="utf-8"))
    return {}


def save_progress(progress):
    tmp = PROGRESS.with_suffix(".tmp")
    tmp.write_text(json.dumps(progress, indent=1), encoding="utf-8")
    os.replace(tmp, PROGRESS)  # atomic: a crash never corrupts your history


def build_queue(cards, progress, section, mode, new_limit):
    pool = [c for c in cards if section == "All sections" or c["section"] == section]
    pool = [c for c in pool if not progress.get(c["id"], {}).get("suspended")]
    if mode == "Cram (random, no scheduling)":
        random.shuffle(pool)
        return pool
    now = dt.datetime.now()
    due = [c for c in pool if c["id"] in progress and srs.is_due(progress[c["id"]], now)]
    due.sort(key=lambda c: progress[c["id"]]["due"])
    new = [c for c in pool if c["id"] not in progress or progress[c["id"]]["due"] is None]
    return due + new[:new_limit]


# ---------------------------------------------------------------- GUI
class App:
    def __init__(self, root, meta, cards, progress):
        self.root, self.meta, self.cards, self.progress = root, meta, cards, progress
        self.renderer = Renderer(meta.get("preamble", []), cache_dir=HERE / ".render_cache")
        self.queue, self.card, self.revealed = [], None, False
        self.t0, self.timer_job, self.done, self.correct, self.times = 0.0, None, 0, 0, []
        self._imgs = {}
        self.build()

    def build(self):
        r = self.root
        r.title("Physics flashcards")
        r.geometry("1000x720")
        r.configure(bg="white")
        style = ttk.Style()
        style.configure("TLabel", background="white")
        style.configure("Topic.TLabel", foreground="#666", font=("Helvetica", 11))
        style.configure("Big.TLabel", font=("Helvetica", 13))

        top = ttk.Frame(r, padding=8)
        top.pack(fill="x")
        sections = ["All sections"] + sorted({c["section"] for c in self.cards})
        self.section = tk.StringVar(value="All sections")
        self.mode = tk.StringVar(value="Review (spaced repetition)")
        self.limit = tk.IntVar(value=25)
        self.newlim = tk.IntVar(value=20)
        ttk.Combobox(top, textvariable=self.section, values=sections, width=42, state="readonly").pack(side="left")
        ttk.Combobox(top, textvariable=self.mode, width=28, state="readonly",
                     values=["Review (spaced repetition)", "Cram (random, no scheduling)"]).pack(side="left", padx=6)
        ttk.Label(top, text="seconds").pack(side="left")
        ttk.Spinbox(top, from_=5, to=180, textvariable=self.limit, width=4).pack(side="left", padx=(2, 8))
        ttk.Label(top, text="new").pack(side="left")
        ttk.Spinbox(top, from_=0, to=500, textvariable=self.newlim, width=4).pack(side="left", padx=(2, 8))
        ttk.Button(top, text="Start", command=self.start).pack(side="left")
        ttk.Button(top, text="Hint (F1)", command=self.show_hint).pack(side="left", padx=6)

        body = tk.Frame(r, bg="white", padx=16)
        body.pack(fill="both", expand=True)
        self.topic = ttk.Label(body, style="Topic.TLabel", text="")
        self.topic.pack(anchor="w", pady=(6, 2))
        self.prompt = tk.Label(body, bg="white", text="Pick a section and press Start.",
                               font=("Helvetica", 16))
        self.prompt.pack(pady=8)
        self.hint = ttk.Label(body, style="Big.TLabel", text="", foreground="#a35")
        self.hint.pack()

        tf = tk.Frame(body, bg="white")
        tf.pack(fill="x", pady=4)
        self.bar = ttk.Progressbar(tf, maximum=1000, length=600)
        self.bar.pack(side="left", padx=(0, 8))
        self.clock = ttk.Label(tf, text="", style="Big.TLabel")
        self.clock.pack(side="left")

        ttk.Label(body, text="Your answer (LaTeX or plain text)   Enter = reveal, Shift+Enter = new line",
                  style="Topic.TLabel").pack(anchor="w")
        self.entry = tk.Text(body, height=3, font=("Courier", 14), wrap="word", relief="solid", bd=1)
        self.entry.pack(fill="x")
        self.entry.bind("<Return>", self.on_enter)

        self.answer = tk.Label(body, bg="white")
        self.answer.pack(pady=(14, 4))
        self.mine_caption = ttk.Label(body, style="Topic.TLabel", text="")
        self.mine_caption.pack()
        self.mine = tk.Label(body, bg="white")
        self.mine.pack()

        bf = tk.Frame(body, bg="white")
        bf.pack(pady=10)
        self.buttons = {}
        for g, colour in zip((1, 2, 3, 4), ("#d9534f", "#f0ad4e", "#5cb85c", "#5bc0de")):
            b = tk.Button(bf, text=srs.GRADES[g], width=12, bg=colour, fg="white", state="disabled",
                          command=lambda g=g: self.grade(g))
            b.pack(side="left", padx=4)
            self.buttons[g] = b
        self.status = ttk.Label(r, text="", padding=6, style="Topic.TLabel")
        self.status.pack(fill="x", side="bottom")

        r.bind("<Key>", self.on_key)
        r.bind("<Escape>", lambda e: self.finish())

    # ---------------------------------------------------------- images
    def image(self, text, math):
        img, _ = self.renderer.render(text, math)
        maxw = 940
        if img.width > maxw:
            img = img.resize((maxw, int(img.height * maxw / img.width)))
        tkimg = ImageTk.PhotoImage(img)
        self._imgs[id(tkimg)] = tkimg
        return tkimg

    def show_image(self, label, text, math):
        tkimg = self.image(text, math)
        label.configure(image=tkimg, text="")
        label.image = tkimg

    # ---------------------------------------------------------- flow
    def start(self):
        self.queue = build_queue(self.cards, self.progress, self.section.get(), self.mode.get(),
                                 self.newlim.get())
        self.done = self.correct = 0
        self.times = []
        if not self.queue:
            self.prompt.configure(image="", text="Nothing due here. Try another section, Cram mode, or more new cards.")
            return
        self.next_card()

    def next_card(self):
        self._imgs.clear()
        if not self.queue:
            return self.finish()
        self.card = self.queue.pop(0)
        self.revealed, self.timed_out = False, False
        c = self.card
        self.topic.configure(text=" › ".join(x for x in (c["section"], c["subsection"], c["group"]) if x))
        self.show_image(self.prompt, c["prompt"], not c["prompt_is_text"])
        self.hint.configure(text="")
        self.answer.configure(image="", text="")
        self.mine.configure(image="", text="")
        self.mine_caption.configure(text="")
        for b in self.buttons.values():
            b.configure(state="disabled")
        self.entry.configure(state="normal")
        self.entry.delete("1.0", "end")
        self.entry.focus_set()
        self.update_status()
        self.root.update_idletasks()
        self.t0 = time.monotonic()
        self.tick()

    def tick(self):
        limit = max(1, self.limit.get())
        elapsed = time.monotonic() - self.t0
        self.bar["value"] = min(1000, 1000 * elapsed / limit)
        self.clock.configure(text=f"{max(0.0, limit - elapsed):4.1f}s")
        if elapsed >= limit:
            self.timed_out = True
            return self.reveal()
        self.timer_job = self.root.after(100, self.tick)

    def on_enter(self, event):
        if event.state & 0x1:  # Shift held: allow a newline
            return None
        if not self.revealed and self.card:
            self.reveal()
        return "break"

    def reveal(self):
        if self.revealed or not self.card:
            return
        self.revealed = True
        if self.timer_job:
            self.root.after_cancel(self.timer_job)
        self.secs = time.monotonic() - self.t0
        c = self.card
        self.clock.configure(text=("TIME'S UP" if self.timed_out else f"{self.secs:.1f}s"))
        self.show_image(self.answer, c["answer"], True)
        typed = self.entry.get("1.0", "end").strip()
        self.entry.configure(state="disabled")
        self.mine_caption.configure(text="you wrote:")
        if typed and "\\" in typed:  # looks like LaTeX: render it next to the real answer
            try:
                self.show_image(self.mine, typed, "$" not in typed)
            except Exception:
                self.mine.configure(text=typed, image="", font=("Courier", 15))
        elif typed:  # plain shorthand such as sqrt(pi/a): show as typed
            self.mine.configure(text=typed, image="", font=("Courier", 15))
        else:
            self.mine.configure(text="(no answer typed)", image="")
        state = self.progress.get(c["id"], srs.new_state())
        for g, b in self.buttons.items():
            days = srs.next_interval(state, 2 if (self.timed_out and g >= 3) else g)
            b.configure(state="normal", text=f"{g}  {srs.GRADES[g]}  · {srs.describe(days)}")
        self.root.focus_set()
        self.root.after(30, self.prefetch)

    def prefetch(self):
        """Render the next card while you read this one (LaTeX takes ~0.2 s per image)."""
        if self.queue:
            n = self.queue[0]
            try:
                self.renderer.render(n["prompt"], not n["prompt_is_text"])
                self.renderer.render(n["answer"], True)
            except Exception:
                pass

    def grade(self, g):
        if not self.revealed:
            return
        c = self.card
        cram = self.mode.get().startswith("Cram")
        state = self.progress.setdefault(c["id"], srs.new_state())
        if cram:
            state["history"].append({"t": dt.datetime.now().isoformat(timespec="seconds"),
                                     "grade": g, "secs": round(self.secs, 1), "cram": True})
            applied = g
        else:
            applied = srs.apply_grade(state, g, self.secs, self.timed_out)
        save_progress(self.progress)
        self.done += 1
        self.times.append(self.secs)
        if applied >= 3:
            self.correct += 1
        if applied == 1:  # forgotten: see it again in a few cards
            self.queue.insert(min(3, len(self.queue)), c)
        self.next_card()

    def suspend(self):
        if not self.revealed:
            return
        self.progress.setdefault(self.card["id"], srs.new_state())["suspended"] = True
        save_progress(self.progress)
        self.next_card()

    def on_key(self, event):
        if self.revealed and event.char in "1234" and event.char:
            self.grade(int(event.char))
        elif self.revealed and event.char.lower() == "s":
            self.suspend()
        elif not self.revealed and event.keysym == "F1":
            self.show_hint()

    def show_hint(self):
        if self.card and not self.revealed:
            name = self.card.get("name") or ""
            self.hint.configure(text=("hint: " + name) if name else "no hint for this card")

    def update_status(self):
        acc = f"{100 * self.correct / self.done:.0f}%" if self.done else "-"
        avg = f"{sum(self.times) / len(self.times):.1f}s" if self.times else "-"
        self.status.configure(text=f"left {len(self.queue) + 1}   done {self.done}   recalled {acc}   "
                                   f"avg time {avg}   renderer {self.renderer.engine}")

    def finish(self):
        if self.timer_job:
            self.root.after_cancel(self.timer_job)
        self.card, self.revealed = None, False
        acc = f"{100 * self.correct / self.done:.0f}%" if self.done else "-"
        avg = f"{sum(self.times) / len(self.times):.1f}s" if self.times else "-"
        self.prompt.configure(image="", text=f"Session over: {self.done} cards, {acc} recalled, avg {avg}.")
        self.answer.configure(image="", text="")
        self.mine.configure(image="", text="")
        self.topic.configure(text="")
        self.clock.configure(text="")
        self.bar["value"] = 0
        for b in self.buttons.values():
            b.configure(state="disabled")


# ---------------------------------------------------------------- CLI extras
def prerender(meta, cards):
    r = Renderer(meta.get("preamble", []), cache_dir=HERE / ".render_cache")
    print("renderer:", r.engine)
    for i, c in enumerate(cards, 1):
        r.render(c["prompt"], not c["prompt_is_text"])
        r.render(c["answer"], True)
        if i % 25 == 0:
            print(f"  {i}/{len(cards)}")
    print("done")


def stats(cards, progress):
    by_id = {c["id"]: c for c in cards}
    rows = []
    for cid, st in progress.items():
        if cid not in by_id or not st["history"]:
            continue
        secs = [h["secs"] for h in st["history"]]
        rows.append((st["lapses"], sum(secs) / len(secs), by_id[cid]))
    rows.sort(key=lambda r: (-r[0], -r[1]))
    print(f"{len(progress)} cards studied, {len(cards)} in deck\n")
    print("Weakest cards (most forgotten, then slowest):")
    for lapses, avg, c in rows[:20]:
        print(f"  lapses {lapses:2d}  avg {avg:5.1f}s  {c['prompt'][:70]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prerender", action="store_true")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()
    if not CARDS.exists():
        sys.exit("cards.json not found: run  python extract_cards.py your_handbook.tex  first")
    meta, cards = load_deck()
    progress = load_progress()
    if args.prerender:
        return prerender(meta, cards)
    if args.stats:
        return stats(cards, progress)
    root = tk.Tk()
    App(root, meta, cards, progress)
    root.mainloop()


if __name__ == "__main__":
    main()
