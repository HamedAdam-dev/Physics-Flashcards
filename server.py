#!/usr/bin/env python3
"""Question bank: a small local flashcard game.

Reads the deck in ~/Downloads/physics_flashcards and stores scores in
progress.json next to this file. A card may set "image" or "answer_image"
to a picture inside that folder (png, jpg, gif, webp, svg).

    python3 server.py
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

ROOT = Path(__file__).resolve().parent
FLASH = Path.home() / "Downloads" / "physics_flashcards"
DECK = FLASH / "cards.json"
CUSTOM = FLASH / "custom_cards.json"
PROGRESS = ROOT / "progress.json"
INDEX = ROOT / "index.html"
PORT = 8765
IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
}


def empty_progress():
    return {"points": 0, "bestStreak": 0, "section": "All", "length": 15, "weak": False, "cards": {}}


def media_path(name):
    if not name:
        return None
    raw = Path(name)
    path = raw.resolve() if raw.is_absolute() else (FLASH / name).resolve()
    try:
        path.relative_to(FLASH.resolve())
    except ValueError:
        return None
    if path.suffix.lower() not in IMAGES or not path.is_file():
        return None
    return path


def media_url(name):
    path = media_path(name)
    if not path:
        return ""
    rel = path.relative_to(FLASH.resolve()).as_posix()
    return "/media/" + quote(rel, safe="/")


def load_cards():
    data = json.loads(DECK.read_text(encoding="utf-8"))
    extra = json.loads(CUSTOM.read_text(encoding="utf-8")) if CUSTOM.exists() else []
    cards = []
    for c in data["cards"] + extra:
        prompt = c.get("prompt") or ""
        if "prompt_is_text" in c:
            text = bool(c["prompt_is_text"])
        else:
            text = "\\" not in prompt and "$" not in prompt
        cards.append({
            "id": c["id"],
            "section": c.get("section") or "General",
            "subsection": c.get("subsection") or "",
            "name": c.get("name") or "",
            "prompt": prompt,
            "text": text,
            "answer": c.get("answer") or c.get("expected") or "",
            "answer_is_text": bool(c.get("answer_is_text")),
            "image": media_url(c.get("image") or c.get("prompt_image") or ""),
            "answer_image": media_url(c.get("answer_image") or ""),
        })
    return cards


def read_progress():
    if not PROGRESS.exists():
        return empty_progress()
    saved = json.loads(PROGRESS.read_text(encoding="utf-8"))
    base = empty_progress()
    base.update({k: saved[k] for k in base if k in saved})
    base["cards"] = saved.get("cards") or {}
    return base


def write_progress(obj):
    if not isinstance(obj, dict):
        raise ValueError("progress")
    length = int(obj.get("length") or 15)
    clean = empty_progress()
    clean["points"] = min(10_000_000, max(0, int(obj.get("points") or 0)))
    clean["bestStreak"] = min(10_000_000, max(0, int(obj.get("bestStreak") or 0)))
    clean["section"] = str(obj.get("section") or "All")[:160]
    clean["length"] = length if length in (10, 15, 25) else 15
    clean["weak"] = bool(obj.get("weak"))
    cards = {}
    for cid, st in list((obj.get("cards") or {}).items())[:2000]:
        if not isinstance(cid, str) or not isinstance(st, dict):
            continue
        recent = [1 if x else 0 for x in (st.get("recent") or [])[-12:]]
        cards[cid[:80]] = {
            "recent": recent,
            "correct": max(0, int(st.get("correct") or 0)),
            "wrong": max(0, int(st.get("wrong") or 0)),
        }
    clean["cards"] = cards
    tmp = PROGRESS.with_suffix(".tmp")
    tmp.write_text(json.dumps(clean, indent=2), encoding="utf-8")
    tmp.replace(PROGRESS)
    return clean


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("%s %s" % (self.command, self.path.split("?")[0]))

    def send(self, code, body, content_type):
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, obj, code=200):
        self.send(code, json.dumps(obj), "application/json")

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self.send(200, INDEX.read_text(encoding="utf-8"), "text/html; charset=utf-8")
        elif path == "/api/deck":
            self.send_json({"cards": load_cards()})
        elif path == "/api/progress":
            self.send_json(read_progress())
        elif path.startswith("/media/"):
            file = media_path(unquote(path[len("/media/"):]))
            if not file:
                self.send(404, "not found", "text/plain; charset=utf-8")
                return
            self.send(200, file.read_bytes(), TYPES[file.suffix.lower()])
        else:
            self.send(404, "not found", "text/plain; charset=utf-8")

    def do_POST(self):
        if urlparse(self.path).path != "/api/progress":
            self.send(404, "not found", "text/plain; charset=utf-8")
            return
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if 0 < n <= 1_000_000 else b""
        try:
            self.send_json(write_progress(json.loads(raw)))
        except (json.JSONDecodeError, TypeError, ValueError):
            self.send(400, "bad progress", "text/plain; charset=utf-8")


def main():
    if not DECK.exists():
        raise SystemExit("Deck not found: %s" % DECK)
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as err:
        raise SystemExit("Port %s is in use (%s)." % (PORT, err))
    print("Recall   http://127.0.0.1:%s" % PORT)
    print("Stop with Ctrl+C")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
