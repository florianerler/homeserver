#!/usr/bin/env python3
"""homeserver-collector: root-eigener Dienst mit FESTEN Befehlen.

Sammelt, was nur root lesen darf, und legt es als Dateien nach /run/homeserver/ ab.
Der SSH-Agent (Benutzer 'traffic') liest diese Dateien. Dadurch braucht 'traffic' kein sudo.

Sicherheitsregeln:
  * Es gibt keine Eingabe von aussen: kein Netzwerk, keine Argumente, kein stdin.
  * Befehle sind feste Argumentlisten (kein Shell, keine Platzhalter) mit Zeitlimit.
  * Ausgaben werden validiert, begrenzt und bereinigt, bevor sie geschrieben werden.
  * Dateien werden atomar ersetzt (os.replace) und sind fuer alle lesbar (0644), aber nur
    von root schreibbar.
  * Schlaegt ein Befehl fehl, bleibt die alte Datei liegen und altert aus (der Agent meldet
    dann 'Collector offline'); der Grund geht ins Journal, nie Ausgaben mit Nutzdaten.
"""
import json
import os
import re
import subprocess
import sys
import time

RUN_DIR = "/run/homeserver"
PVESH = ["/usr/bin/pvesh", "get", "/cluster/resources", "--type", "vm", "--output-format", "json"]
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C"}
MAX_OUTPUT = 1 << 20        # 1 MiB
MAX_GUESTS = 64
NAME_RE = re.compile(r"[^A-Za-z0-9._-]")


def run_fixed(argv, timeout=15):
    """Festen Befehl ausfuehren (ohne Shell) und stdout zurueckgeben."""
    res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=True, env=ENV)
    if len(res.stdout) > MAX_OUTPUT:
        raise ValueError("Ausgabe zu gross")
    return res.stdout


def sanitize_name(name):
    return NAME_RE.sub("_", str(name))[:32] or "_"


def parse_guests(text):
    """Ausgabe von 'pvesh get /cluster/resources --type vm' -> bereinigte Gaesteliste."""
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("unerwartetes Format")
    guests = []
    for g in data:
        if not isinstance(g, dict) or g.get("template") in (1, True):
            continue
        try:
            vmid = int(g["vmid"])
        except (KeyError, TypeError, ValueError):
            continue
        kind = g.get("type")
        if not 1 <= vmid <= 999999999 or kind not in ("qemu", "lxc"):
            continue
        status = g.get("status")
        guests.append({"vmid": vmid, "type": kind,
                       "status": status if status in ("running", "stopped") else "other",
                       "name": sanitize_name(g.get("name", ""))})
    guests.sort(key=lambda x: x["vmid"])
    return guests[:MAX_GUESTS]


def collect_guests(run=run_fixed):
    return {"ts": int(time.time()), "guests": parse_guests(run(PVESH))}


def write_json_atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(obj, fh, separators=(",", ":"))
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


# Name der Datei -> (Intervall in Sekunden, Sammelfunktion). Spaetere Phasen ergaenzen hier.
TASKS = {
    "guests.json": (30, collect_guests),
}


def run_task(name, collect, run_dir=RUN_DIR):
    """Eine Aufgabe ausfuehren. True bei Erfolg. Bei Fehler bleibt die alte Datei liegen."""
    try:
        write_json_atomic(os.path.join(run_dir, name), collect())
        return True
    except (subprocess.SubprocessError, OSError, ValueError, KeyError) as e:
        print(f"homeserver-collector: {name}: {type(e).__name__}", file=sys.stderr, flush=True)
        return False


def main():
    os.makedirs(RUN_DIR, exist_ok=True)
    due = {name: 0.0 for name in TASKS}
    while True:
        now = time.monotonic()
        for name, (interval, collect) in TASKS.items():
            if now >= due[name]:
                run_task(name, collect)
                due[name] = time.monotonic() + interval
        time.sleep(1.0)


if __name__ == "__main__":
    main()
