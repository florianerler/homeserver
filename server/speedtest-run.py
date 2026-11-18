#!/usr/bin/env python3
"""homeserver-speedtest-run: fuehrt EINEN Speedtest aus und speichert das Ergebnis.

Laeuft als eigener unprivilegierter Benutzer (hs-speedtest), gestartet von systemd
(Timer alle 3 h und Trigger-Datei per .path-Unit). Keine Eingabe von aussen:
kein Netzwerk-Listener, keine Argumente.

  * flock: es laeuft hoechstens ein Test gleichzeitig
  * Mindestabstand 10 min zwischen zwei Tests (Timer und Klick teilen sich das)
  * fester Befehl ohne Shell, Zeitlimit, Ausgabe wird validiert
  * Ergebnisse: history.jsonl (30 Tage), Zustand: state.json (atomar ersetzt)
"""
import fcntl
import json
import os
import subprocess
import sys
import time

STATE_DIR = "/var/lib/homeserver-speedtest"
MIN_SPACING_S = 600
KEEP_DAYS = 30
MAX_ENTRIES = 300
TIMEOUT_S = 150
MAX_OUTPUT = 1 << 20
ENV = {"PATH": "/usr/bin:/bin", "LANG": "C", "HOME": STATE_DIR}
# Feste Kandidaten (nie $PATH): Ookla-CLI bevorzugt, sonst das Debian-Paket speedtest-cli.
TOOLS = (
    ("ookla", "/usr/bin/speedtest", ["--accept-license", "--accept-gdpr", "--format=json"]),
    ("ookla", "/usr/local/bin/speedtest", ["--accept-license", "--accept-gdpr", "--format=json"]),
    ("cli", "/usr/bin/speedtest-cli", ["--json"]),
)


def failure_detail(exc):
    """Kurzer Fehlergrund fuer das Journal: Fehlertyp und die letzte Zeile der Fehlerausgabe des Programms
    (nur druckbares ASCII, begrenzt). Enthaelt keine Messwerte."""
    text = getattr(exc, "stderr", None) or ""
    if isinstance(text, bytes):
        text = text.decode("ascii", "replace")
    last = text.strip().splitlines()[-1] if text.strip() else ""
    last = "".join(c for c in last if " " <= c <= "~")[:200]
    return f"{type(exc).__name__}" + (f": {last}" if last else "")


def find_tool(tools=TOOLS):
    for kind, path, args in tools:
        if os.access(path, os.X_OK):
            return kind, [path] + args
    return None


def _valid(down_kbit, up_kbit, ping_ms):
    if not (0 < down_kbit < 10_000_000 and 0 < up_kbit < 10_000_000 and 0 <= ping_ms < 10_000):
        raise ValueError("unplausible Werte")
    return int(down_kbit), int(up_kbit), round(float(ping_ms), 1)


def parse_ookla(text):
    """Ookla-CLI (--format=json): bandwidth in Byte/s -> kbit/s."""
    d = json.loads(text)
    return _valid(d["download"]["bandwidth"] * 8 / 1000, d["upload"]["bandwidth"] * 8 / 1000, d["ping"]["latency"])


def parse_speedtest_cli(text):
    """speedtest-cli (--json): download/upload in bit/s -> kbit/s."""
    d = json.loads(text)
    return _valid(d["download"] / 1000, d["upload"] / 1000, d["ping"])


PARSERS = {"ookla": parse_ookla, "cli": parse_speedtest_cli}


def write_json_atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, separators=(",", ":"))
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def read_state(state_dir=STATE_DIR):
    try:
        with open(os.path.join(state_dir, "state.json")) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def prune_and_append(history_path, entry, now, keep_days=KEEP_DAYS, max_entries=MAX_ENTRIES):
    """Eintrag anhaengen, alte (> keep_days) und ueberzaehlige verwerfen, atomar neu schreiben."""
    entries = []
    try:
        with open(history_path) as fh:
            for line in fh:
                try:
                    e = json.loads(line)
                    if isinstance(e, dict) and now - int(e["ts"]) <= keep_days * 86400:
                        entries.append(e)
                except (ValueError, KeyError, TypeError):
                    continue
    except OSError:
        pass
    entries.append(entry)
    entries = entries[-max_entries:]
    tmp = history_path + ".tmp"
    with open(tmp, "w") as fh:
        for e in entries:
            fh.write(json.dumps(e, separators=(",", ":")) + "\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, history_path)


def run_once(state_dir=STATE_DIR, now=None, tools=TOOLS, runner=None):
    """-> 'locked' | 'cooldown' | 'notinstalled' | 'ok' | 'failed'."""
    runner = runner or (lambda argv: subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT_S,
                                                    check=True, env=ENV).stdout[:MAX_OUTPUT])
    lock = open(os.path.join(state_dir, "lock"), "a")
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return "locked"
        now = int(time.time() if now is None else now)
        state_path = os.path.join(state_dir, "state.json")
        last = read_state(state_dir).get("finished")
        if isinstance(last, int) and 0 <= now - last < MIN_SPACING_S:
            return "cooldown"
        tool = find_tool(tools)
        if tool is None:
            write_json_atomic(state_path, {"state": "notinstalled", "finished": now})
            return "notinstalled"
        kind, argv = tool
        write_json_atomic(state_path, {"state": "running", "started": now, "finished": last if isinstance(last, int) else None})
        try:
            down, up, ping = PARSERS[kind](runner(argv))
        except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError) as exc:
            print(f"homeserver-speedtest: Fehler: {failure_detail(exc)}", file=sys.stderr, flush=True)
            write_json_atomic(state_path, {"state": "idle", "finished": now, "ok": False})
            return "failed"
        done = int(time.time())
        prune_and_append(os.path.join(state_dir, "history.jsonl"), {"ts": done, "down": down, "up": up, "ping": ping}, done)
        write_json_atomic(state_path, {"state": "idle", "finished": done, "ok": True})
        return "ok"
    finally:
        lock.close()


def main():
    result = run_once()
    print(f"homeserver-speedtest: {result}", file=sys.stderr, flush=True)     # nie Messdaten oder Pfade mit Nutzdaten
    return 0


if __name__ == "__main__":
    sys.exit(main())
