#!/usr/bin/env python3
"""CITADEL OVERWATCH - Terminal-Dashboard fuer den Heimserver.

Startet den vorhandenen Streamer (homeserver.py) als Unterprozess und zeichnet dessen
JSON-Snapshots (eine pro Sekunde) als TUI. Es gibt keine eigene Datenlogik: Verbindung,
Raten, Frische und Fehlergruende kommen unveraendert aus dem Plugin.

Farben: ausschliesslich ANSI-Indexfarben (30-37), keine festen RGB-/256-Werte. Das Terminal
(foot) belegt sie mit der Palette des aktiven Omarchy-Themes, ein Theme-Wechsel zieht also mit.

Start:  python3 -I dashboard.py            (Beenden mit q)
Test:   python3 -I dashboard.py --once snapshot.json [--cols 80 --rows 14]
"""
import argparse
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import termios
import threading
import time
import tty
from collections import deque
from pathlib import Path

HERE = Path(__file__).resolve().parent
STREAMER = HERE / "homeserver.py"
STALE_AFTER_S = 6          # so lange ohne Snapshot -> "keine Daten"
HISTORY = 40

# --- ANSI -------------------------------------------------------------------------------------
RST, BOLD, DIM = "\x1b[0m", "\x1b[1m", "\x1b[2m"
RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN = 1, 2, 3, 4, 5, 6
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def col(n, text, bold=False):
    return f"{BOLD if bold else ''}\x1b[3{n}m{text}{RST}"


def dim(text):
    return f"{DIM}{text}{RST}"


def vlen(s):
    return len(ANSI_RE.sub("", s))


def clip(s, width):
    """Auf sichtbare Breite kuerzen, Escape-Sequenzen zaehlen nicht mit."""
    out, n, i = [], 0, 0
    while i < len(s):
        m = ANSI_RE.match(s, i)
        if m:
            out.append(m.group())
            i = m.end()
            continue
        if n >= width:
            break
        out.append(s[i])
        n += 1
        i += 1
    return "".join(out) + RST


# --- Texte (aus der i18n des Plugins wiederverwendet) -----------------------------------------
def load_texts():
    try:
        return json.loads((HERE / "i18n" / "de.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


TEXTS = load_texts()


def reason(card):
    code = str(card.get("error") or "")
    return TEXTS.get("err." + code) or code or TEXTS.get("err.timeout", "keine Antwort")


# --- Formatierung -----------------------------------------------------------------------------
SPARK = "▁▂▃▄▅▆▇█"


def rate(bps):
    """Bytes/s -> Text, SI-Einheiten wie Model.js."""
    v = max(0.0, float(bps or 0))
    for unit in ("B/s", "kB/s", "MB/s", "GB/s"):
        if v < 1000 or unit == "GB/s":
            return f"{v:.0f} {unit}" if unit == "B/s" else f"{v:.{2 if v < 10 else 1 if v < 100 else 0}f} {unit}"
        v /= 1000
    return ""


def spark(values, width):
    vals = [float(v) for v in list(values)[-width:] if v is not None]
    if not vals:
        return " " * width
    lo, hi = min(vals), max(vals)
    span = hi - lo
    chars = "".join(SPARK[0 if span <= 0 else min(7, int((v - lo) / span * 7.999))] for v in vals)
    return chars.rjust(width)


def bar(pct, width=8):
    filled = max(0, min(width, round(pct / 100 * width)))
    return "█" * filled + "░" * (width - filled)


def duration(sec):
    sec = int(sec or 0)
    d, h, m = sec // 86400, sec % 86400 // 3600, sec % 3600 // 60
    return f"{d}d {h}h" if d else f"{h}h {m}m" if h else f"{m}m"


def pct_color(p):
    return RED if p >= 95 else YELLOW if p >= 85 else GREEN


# --- Banner -----------------------------------------------------------------------------------
FONT = {
    "C": ["╔═╗", "║  ", "╚═╝"], "I": ["╦", "║", "╩"], "T": ["╔╦╗", " ║ ", " ╩ "],
    "A": ["╔═╗", "╠═╣", "╩ ╩"], "D": ["╔╦╗", " ║║", "═╩╝"], "E": ["╔═╗", "║╣ ", "╚═╝"],
    "L": ["╦  ", "║  ", "╩═╝"], "O": ["╔═╗", "║ ║", "╚═╝"], "V": ["╦  ╦", "╚╗╔╝", " ╚╝ "],
    "R": ["╦═╗", "╠╦╝", "╩╚═"], "W": ["╦ ╦", "║║║", "╚╩╝"], "H": ["╦ ╦", "╠═╣", "╩ ╩"],
}


def banner(width):
    """Dreizeiliges Banner; Wort 1 in Akzentfarbe (Blau), Wort 2 in Cyan. None, wenn zu breit."""
    rows, split = ["", "", ""], None
    for ch in "CITADEL OVERWATCH":
        if ch == " ":
            split = len(rows[0])
            rows = [r + "   " for r in rows]
            continue
        g = FONT[ch]
        w = max(len(x) for x in g)
        rows = [r + g[i].ljust(w) + " " for i, r in enumerate(rows)]
    if len(rows[0]) > width:
        return None
    pad = " " * ((width - len(rows[0])) // 2)
    return [pad + col(BLUE, r[:split], True) + col(CYAN, r[split:], True) for r in rows]


# --- Zustand ----------------------------------------------------------------------------------
class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.cards = {}
        self.rx, self.tx = deque(maxlen=HISTORY), deque(maxlen=HISTORY)
        self.last = 0.0          # Zeitpunkt des letzten Snapshots
        self.version = 0
        self.restarts = 0

    def update(self, snap):
        cards = snap.get("cards") if isinstance(snap, dict) else None
        if not isinstance(cards, dict):
            return
        with self.lock:
            self.cards = cards
            net = cards.get("net") or {}
            if net.get("ok"):
                self.rx.append(net.get("rx") or 0)
                self.tx.append(net.get("tx") or 0)
            self.last = time.monotonic()
            self.version += 1


class Feed(threading.Thread):
    """Haelt den Streamer am Leben; bei Absturz oder Ende nach kurzer Pause neu starten."""

    def __init__(self, state):
        super().__init__(daemon=True)
        self.state, self.proc, self.stopping = state, None, False

    def run(self):
        while not self.stopping:
            try:
                self.proc = subprocess.Popen(
                    [sys.executable, "-I", str(STREAMER)], stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
                for line in self.proc.stdout:
                    try:
                        self.state.update(json.loads(line))
                    except ValueError:
                        pass
                self.proc.wait()
            except OSError:
                pass
            if self.stopping:
                break
            self.state.restarts += 1
            time.sleep(3)

    def stop(self):
        self.stopping = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


# --- Zeilen je Karte --------------------------------------------------------------------------
def lab(text):
    return col(BLUE, text.ljust(10), True)


def down(card):
    """Standardanzeige einer nicht verfuegbaren Karte."""
    if card.get("state") == "connecting":
        return col(YELLOW, "◌ verbinde…")
    return col(RED, "✖ OFFLINE") + " " + dim(reason(card))


def row_host(c, st, w):
    n = c.get("net") or {}
    if not n.get("ok"):
        return lab("HOST") + down(n)
    sp = w >= 80
    return (lab("HOST") + col(GREEN, "↓ ") + rate(n.get("rx")).rjust(10) + (" " + col(GREEN, spark(st.rx, 12)) if sp else "")
            + "   " + col(MAGENTA, "↑ ") + rate(n.get("tx")).rjust(10) + (" " + col(MAGENTA, spark(st.tx, 12)) if sp else ""))


def row_qbt(c, st, w):
    q = c.get("qbt") or {}
    if not q.get("ok"):
        return lab("QBIT") + down(q)
    return lab("QBIT") + col(GREEN, "↓ ") + rate(q.get("rx")).rjust(10) + "   " + col(MAGENTA, "↑ ") + rate(q.get("tx")).rjust(10)


def row_ping(c, st, w):
    p = c.get("ping") or {}
    if not p.get("ok"):
        return lab("PING") + down(p)
    bad = p.get("warn") or (p.get("lossPct") or 0) > 2
    ms = p.get("avgMs")
    txt = f"{ms:.0f} ms" if ms is not None else "– ms"
    return (lab("PING") + col(YELLOW if bad else GREEN, txt.rjust(7)) + dim(f"  Verlust {p.get('lossPct') or 0:.0f}%")
            + dim(f"  → {p.get('target', '')}") + (" " + col(CYAN, spark(p.get("history") or [], 12)) if w >= 80 else ""))


def row_guests(c, st, w):
    g = c.get("guests") or {}
    if not g.get("ok"):
        return lab("GÄSTE") + down(g)
    items = g.get("items") or []
    dots = "".join(col(GREEN if i.get("status") == "running" else RED if i.get("status") == "stopped" else YELLOW, "●")
                   for i in items)
    names = " ".join(i.get("name", "?") for i in items if i.get("status") != "running")
    ok = g.get("running", 0) == g.get("total", 0)
    return (lab("GÄSTE") + col(GREEN if ok else YELLOW, f"{g.get('running', 0)}/{g.get('total', 0)}") + " " + dots
            + (" " + col(RED, "↯ " + names) if names else ""))


def row_storage(c, st, w):
    s = c.get("storage") or {}
    if not s.get("ok"):
        return lab("SPEICHER") + down(s)
    parts = []
    for i in s.get("items") or []:
        if not i.get("active", True):
            parts.append(dim(f"{i.get('name')} inaktiv"))
            continue
        p = float(i.get("pct") or 0)
        parts.append(f"{i.get('name')} " + col(pct_color(p), bar(p, 6)) + f" {p:.0f}%")
    return lab("SPEICHER") + "  ".join(parts)


def row_smart(c, st, w):
    s = c.get("smart") or {}
    if not s.get("ok"):
        return lab("SMART") + down(s)
    sym = {"passed": col(GREEN, "✔"), "failed": col(RED, "✖ FEHLER"), "standby": dim("zzz")}
    parts = []
    for d in s.get("items") or []:
        t = d.get("tempC")
        parts.append(f"{d.get('dev')} {sym.get(d.get('status'), col(YELLOW, '?'))}" + (dim(f" {t:.0f}°C") if t is not None else ""))
    return lab("SMART") + ("  ".join(parts) if parts else dim("keine Platten"))


def row_system(c, st, w):
    s = c.get("system") or {}
    if not s.get("ok"):
        return lab("SYSTEM") + down(s)
    tot, av = s.get("memTotalKb") or 0, s.get("memAvailKb") or 0
    ram = (1 - av / tot) * 100 if tot else 0
    load = (s.get("load") or [0])[0]
    t = s.get("tempC")
    return (lab("SYSTEM") + f"Last {load:.2f}  RAM " + col(pct_color(ram), f"{ram:.0f}%")
            + (f"  CPU {t:.0f}°C" if t is not None else "") + dim(f"  ↑ {duration(s.get('uptime'))}"))


def row_speed(c, st, w):
    s = c.get("speed") or {}
    hist = s.get("history") or []
    if not s.get("ok") or not hist:
        return None
    r = hist[-1]
    return (lab("SPEEDTEST") + col(GREEN, f"↓ {r['down'] / 1000:.0f}") + " " + col(MAGENTA, f"↑ {r['up'] / 1000:.0f}")
            + f" Mbit  {r['ping']:.0f} ms" + dim(f"  vor {duration(r['age'])}"))


ROWS = (row_host, row_qbt, row_ping, row_guests, row_storage, row_smart, row_system, row_speed)
CARDS = ("net", "qbt", "system", "guests", "ping", "storage", "smart")


# --- Gesamtbild -------------------------------------------------------------------------------
def render(cards, st, cols, rows, now_s, fresh=True):
    """Liefert genau `rows` Zeilen, jede hoechstens `cols` sichtbare Zeichen breit."""
    if not fresh:
        cards = {}
    lines = banner(cols) if rows >= 12 else None
    if lines is None:
        lines = ["" if cols < 24 else "◢◤ " + col(BLUE, "CITADEL", True) + " " + col(CYAN, "OVERWATCH", True) + " ◥◣"]
    ok = sum(1 for k in CARDS if (cards.get(k) or {}).get("ok"))
    lines.append(dim("━" * cols))
    budget = max(0, rows - len(lines) - 1)
    body = [r for r in (f(cards, st, cols) for f in ROWS) if r]
    lines += body[:budget]
    lines += [""] * (rows - len(lines) - 1)

    if not fresh and not st.last:
        status = col(YELLOW, "◌ VERBINDE…")
    elif not fresh:
        status = col(RED, "✖ KEINE DATEN") + dim(" · Streamer startet neu")
    elif ok == 0:
        status = col(RED, "✖ SERVER OFFLINE") + dim(" · kein LAN/VPN?")
    elif ok < len(CARDS):
        status = col(YELLOW, f"▲ {ok}/{len(CARDS)} QUELLEN")
    elif any((cards.get(k) or {}).get("warn") for k in CARDS) or (cards.get("guests") or {}).get("running") != (cards.get("guests") or {}).get("total"):
        status = col(YELLOW, f"▲ {len(CARDS)} QUELLEN ONLINE · WARNUNG")
    else:
        status = col(GREEN, f"● ALLE {len(CARDS)} QUELLEN ONLINE")
    right = dim(f"{now_s} · q beenden")
    gap = max(1, cols - vlen(status) - vlen(right) - 1)
    lines.append(" " + status + " " * gap + right if cols > 40 else " " + status)
    return [clip(ln if ln.startswith(" ") or not ln else " " + ln, cols) for ln in lines[:rows]]


def frame(st, cols, rows):
    with st.lock:
        cards, last = dict(st.cards), st.last
    fresh = bool(last) and time.monotonic() - last < STALE_AFTER_S
    return render(cards, st, cols, rows, time.strftime("%H:%M:%S"), fresh)


def draw(lines):
    sys.stdout.write("".join(f"\x1b[{i};1H{ln}\x1b[K" for i, ln in enumerate(lines, 1)) + "\x1b[J")
    sys.stdout.flush()


def main():
    ap = argparse.ArgumentParser(description="CITADEL OVERWATCH")
    ap.add_argument("--once", metavar="SNAPSHOT.json", help="einen Snapshot rendern und beenden (Test)")
    ap.add_argument("--cols", type=int)
    ap.add_argument("--rows", type=int)
    args = ap.parse_args()

    st = State()
    if args.once:
        st.update(json.loads(Path(args.once).read_text()))
        size = shutil.get_terminal_size((80, 14))
        print("\n".join(frame(st, args.cols or size.columns, args.rows or size.lines)))
        return

    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: sys.exit(0))      # finally-Block ausfuehren, Streamer beenden
    feed = Feed(st)
    feed.start()
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        sys.stdout.write("\x1b[?1049h\x1b[?25l")
        resized, seen, last_draw = [True], -1, 0.0
        signal.signal(signal.SIGWINCH, lambda *_: resized.__setitem__(0, True))
        while True:
            if select.select([fd], [], [], 0.25)[0]:
                if os.read(fd, 16).lower().startswith(b"q"):
                    break
            if resized[0] or st.version != seen or time.monotonic() - last_draw >= 1.0:
                size = shutil.get_terminal_size((80, 14))
                draw(frame(st, size.columns, size.lines))
                resized[0], seen, last_draw = False, st.version, time.monotonic()
    except KeyboardInterrupt:
        pass
    finally:
        feed.stop()
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\x1b[?25h\x1b[?1049l")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
