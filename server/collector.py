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
BIN_DIRS = ("/usr/sbin", "/usr/bin", "/sbin", "/bin")      # feste Suchpfade, nie $PATH


def fixed_bin(name):
    """Absoluter Pfad eines Programms aus BIN_DIRS (sonst der Standardpfad; Start schlaegt dann sauber fehl)."""
    for d in BIN_DIRS:
        p = os.path.join(d, name)
        if os.access(p, os.X_OK):
            return p
    return os.path.join(BIN_DIRS[0], name)


SMART_TYPES = ("scsi", "sat", "ata", "nvme", "auto")     # erlaubte -d Werte (Whitelist)
DEV_RE = re.compile(r"^/dev/(sd[a-z]{1,2}|nvme[0-9]{1,2}(n[0-9]{1,2})?)$")
MAX_DISKS = 8
STORAGE_TOKEN = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
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


def parse_storage(text):
    """Ausgabe von 'pvesm status' (Name Type Status Total Used Available %), Werte in KiB."""
    out = []
    for line in text.splitlines()[1:]:
        cols = line.split()
        if len(cols) < 6 or not STORAGE_TOKEN.match(cols[0]) or not STORAGE_TOKEN.match(cols[1]):
            continue
        try:
            total, used = int(cols[3]), int(cols[4])
        except ValueError:
            continue
        if total < 0 or used < 0:
            continue
        out.append({"name": cols[0], "type": cols[1], "status": "active" if cols[2] == "active" else "inactive",
                    "total_kb": total, "used_kb": used})
    if not out:
        raise ValueError("keine Speicher gefunden")
    return out[:32]


def collect_storage(run=run_fixed):
    return {"ts": int(time.time()), "storages": parse_storage(run([fixed_bin("pvesm"), "status"]))}


def parse_smart_scan(text):
    """'smartctl --scan' -> [(geraet, typ)], nur Geraete und Typen aus der Whitelist."""
    disks = []
    for line in text.splitlines():
        parts = line.split("#")[0].split()
        if len(parts) >= 3 and parts[1] == "-d" and DEV_RE.match(parts[0]) and parts[2] in SMART_TYPES:
            disks.append((parts[0], parts[2]))
    return disks[:MAX_DISKS]


def parse_smart_health(text):
    """JSON von 'smartctl -j -H -A' -> (status, temp|None); status: passed|failed|standby|unknown."""
    try:
        d = json.loads(text)
    except ValueError:
        return "unknown", None
    if not isinstance(d, dict):
        return "unknown", None
    temp = None
    t = d.get("temperature")
    if isinstance(t, dict) and isinstance(t.get("current"), (int, float)) and -50 < t["current"] < 150:
        temp = int(t["current"])
    passed = (d.get("smart_status") or {}).get("passed") if isinstance(d.get("smart_status"), dict) else None
    if passed is True:
        return "passed", temp
    if passed is False:
        return "failed", temp
    if "standby" in text.lower():
        return "standby", None
    return "unknown", temp


def run_smartctl(argv, timeout=20):
    """smartctl liefert Bitmasken als Exit-Code (auch bei gesunden Platten): Code ignorieren, Ausgabe pruefen."""
    res = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False, env=ENV)
    return res.stdout[:MAX_OUTPUT]


def collect_smart(run=run_fixed, run_rc=run_smartctl):
    disks = []
    smartctl = fixed_bin("smartctl")
    for dev, dtype in parse_smart_scan(run([smartctl, "--scan"])):
        types = [dtype] + (["sat"] if dtype == "scsi" else [])       # SATA hinter SCSI-Schicht: -d sat probieren
        status, temp = "unknown", None
        for t in types:
            status, temp = parse_smart_health(run_rc([smartctl, "-j", "-H", "-A", "-n", "standby", "-d", t, dev]))
            if status != "unknown":
                break
        disks.append({"dev": dev[len("/dev/"):], "status": status, "temp": temp})
    return {"ts": int(time.time()), "disks": disks}


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
    "storage.json": (30, collect_storage),
    "smart.json": (600, collect_smart),
}


def run_task(name, collect, run_dir=RUN_DIR):
    """Eine Aufgabe ausfuehren. True bei Erfolg. Bei Fehler bleibt die alte Datei liegen."""
    try:
        write_json_atomic(os.path.join(run_dir, name), collect())
        return True
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError) as e:
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
