"""The review: every item a person has to decide, old code left, new code right.

Two ways, same answers (saved in the state file after every answer):
- a local web page (``serve``): opens in the browser, one card per item, the guessed
  value prefilled; buttons for "accept", "own value", "disable"; links that open the
  line in VS Code. The terminal waits until "Fertig" is pressed.
- the terminal (``terminal``): the same, one item at a time, the two snippets side by side.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .common import Item, vscode_link

PAGE = Path(__file__).with_name("review.html")


def _payload(items: list[Item], title: str) -> dict:
    out = []
    for it in items:
        d = it.to_json()
        for side in ("old", "new"):
            if d.get(side):
                d[side]["link"] = vscode_link(d[side]["file"], d[side]["line"])
        out.append(d)
    return {"title": title, "items": out}


def open_in_code(file: str, line: int) -> bool:
    code = shutil.which("code")
    if not code:
        return False
    subprocess.Popen([code, "-g", f"{file}:{line}"], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL)
    return True


def serve(items: list[Item], save, title: str, port: int = 8765, browser: bool = True) -> None:
    """Runs the review page until the person presses "Fertig". ``save()`` persists answers."""
    done = threading.Event()
    by_id = {it.id: it for it in items}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(200, PAGE.read_bytes(), "text/html")
            elif self.path == "/api/items":
                self._send(200, _payload(items, title))
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            if self.path == "/api/answer":
                for a in body.get("answers", [body]):
                    it = by_id.get(a.get("id"))
                    if it is not None:
                        it.answer = a.get("answer") or None
                save()
                self._send(200, {"ok": True})
            elif self.path == "/api/open":
                self._send(200, {"ok": open_in_code(body["file"], int(body["line"]))})
            elif self.path == "/api/done":
                save()
                self._send(200, {"ok": True})
                done.set()
            else:
                self._send(404, {"error": "not found"})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"\n  Prüfseite: {url}")
    print("  (links alt, rechts neu; dort \"Fertig\" drücken. Strg+C bricht ab, Antworten bleiben gespeichert.)")
    if browser:
        _open_browser(url)
    try:
        done.wait()
    finally:
        server.shutdown()


def _open_browser(url: str) -> None:
    # In WSL the Windows browser is reached through explorer.exe / wslview.
    for cmd in (["wslview", url], ["explorer.exe", url]):
        if shutil.which(cmd[0]):
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except OSError:
                continue
    webbrowser.open(url)


# -- terminal -------------------------------------------------------------------------

def _side_by_side(old, new, width: int) -> list[str]:
    half = (width - 3) // 2
    def lines(sn):
        if not sn:
            return ["(keine Stelle)"]
        out = []
        for k, text in enumerate(sn["lines"] if isinstance(sn, dict) else sn.lines):
            first = sn["first"] if isinstance(sn, dict) else sn.first
            line = sn["line"] if isinstance(sn, dict) else sn.line
            mark = ">" if first + k == line else " "
            out.append(f"{mark}{first + k:>6} {text.strip()}"[:half])
        return out
    a, b = lines(old), lines(new)
    n = max(len(a), len(b))
    a += [""] * (n - len(a))
    b += [""] * (n - len(b))
    return [f"{x:<{half}} │ {y}" for x, y in zip(a, b)]


def terminal(items: list[Item], save) -> None:
    width = max(80, shutil.get_terminal_size((160, 40)).columns)
    open_items = [it for it in items if it.answer is None]
    for k, it in enumerate(open_items, 1):
        print("\n" + "━" * width)
        print(f"  [{k}/{len(open_items)}] {it.title}" + ("   (sicher)" if it.sure else ""))
        print("  " + "\n  ".join(textwrap.wrap(it.question, width - 4)))
        if it.note:
            print("  " + "\n  ".join(textwrap.wrap(it.note, width - 4)))
        print("  alt                                  " + " " * max(0, (width - 3) // 2 - 39) + "neu")
        for line in _side_by_side(it.old, it.new, width):
            print(line)
        if it.old_value:
            print(f"  bisher:    {it.old_value}")
        if it.guess:
            print(f"  Vorschlag: {it.guess}")
        for n, ch in enumerate(it.choices, 1):
            print(f"  [{n}] {ch}")
        hint = "Enter = Vorschlag, Wert eintippen, s = überspringen, q = später weiter"
        if it.old:
            hint += ", o = in VS Code öffnen"
        while True:
            ans = input(f"  {hint}\n  > ").strip()
            if ans == "o" and it.old:
                open_in_code(it.old.file, it.old.line)
                if it.new:
                    open_in_code(it.new.file, it.new.line)
                continue
            break
        if ans == "q":
            break
        if ans == "s":
            continue
        if ans == "" and it.guess:
            it.answer = "übernehmen"
        elif ans == "" and it.kind == "info":
            it.answer = "ok"
        elif ans.isdigit() and 1 <= int(ans) <= len(it.choices):
            it.answer = it.choices[int(ans) - 1]
        elif ans:
            it.answer = ans
        save()
