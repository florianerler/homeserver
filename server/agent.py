#!/usr/bin/env python3
"""homeserver-agent: laeuft als Benutzer 'traffic', gebunden per command= im SSH-Key.

Sendet typisierte Zeilen auf stdout (Protokoll siehe README) und liest Befehle von
stdin. Es gibt KEINE Shell und keine freien Argumente: Befehle werden gegen eine
feste Whitelist (COMMANDS) geprueft. stdin-EOF bedeutet "Client weg" -> Ende.

Nur Python-Standardbibliothek. Root-Aufgaben erledigt NICHT dieser Agent, sondern
der root-eigene Collector (collector.py): er schreibt Dateien nach /run/homeserver/, der
Agent liest nur diese Dateien sowie /proc und /sys. Dadurch braucht 'traffic' kein sudo.

Zeilen (Server -> Client):
  HELLO homeserver-agent 1
  NET <uptime> <iface> <rx> <tx>                                  jede Sekunde
  SYS <uptime> <ncpu> <l1> <l5> <l15> <memtotal_kB> <memavail_kB> <temp_C|->   alle 5 s
  BEGIN GUESTS <alter_s> / G <vmid> <qemu|lxc> <running|stopped|other> <name> / END GUESTS   alle 30 s
  MISSING GUESTS                                                  wenn der Collector keine Datei liefert
"""
import argparse
import configparser
import json
import os
import re
import select
import sys
import time

PROTOCOL = 1
CONF = "/etc/homeserver/agent.conf"
IFACE_RE = re.compile(r"^[A-Za-z0-9._-]{1,15}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9._:-]{1,32}$")
MAX_INTERFACES = 8
MAX_CMD_BYTES = 128
SYS_NET = "/sys/class/net"
PROC_UPTIME = "/proc/uptime"
PROC_LOADAVG = "/proc/loadavg"
PROC_MEMINFO = "/proc/meminfo"
HWMON = "/sys/class/hwmon"
GUESTS_FILE = "/run/homeserver/guests.json"
TEMP_DRIVERS = ("coretemp", "k10temp", "zenpower", "cpu_thermal")     # Reihenfolge = Vorrang
TEMP_LABELS = ("package id 0", "tctl", "tdie", "cpu")
SYS_EVERY = 5               # Takte (Sekunden) zwischen SYS-Zeilen
GUESTS_EVERY = 30           # Takte zwischen Gaeste-Bloecken
MAX_GUESTS = 64
MAX_FILE_BYTES = 65536
NAME_RE = re.compile(r"[^A-Za-z0-9._-]")

# Befehls-Whitelist: Name -> Funktion(argumente) -> Ergebnis-Code.
# Phase 0: noch keine Befehle. Spaetere Phasen tragen hier ihre festen Befehle ein.
COMMANDS = {}


def physical_interfaces(sys_net=SYS_NET):
    """Fallback ohne Konfiguration: echte NICs (haben ein 'device'-Verzeichnis)."""
    try:
        names = sorted(os.listdir(sys_net))
    except OSError:
        return []
    return [n for n in names if IFACE_RE.match(n) and os.path.exists(os.path.join(sys_net, n, "device"))]


def load_interfaces(path=CONF, sys_net=SYS_NET):
    """Konfigurierte Interface-Namen (validiert, ohne Duplikate, begrenzt)."""
    cp = configparser.ConfigParser(interpolation=None)
    if not cp.read(path):
        return physical_interfaces(sys_net)[:MAX_INTERFACES]
    raw = cp.get("agent", "interfaces", fallback="").split()
    seen, out = set(), []
    for name in raw:
        if IFACE_RE.match(name) and name not in seen:
            seen.add(name)
            out.append(name)
    return out[:MAX_INTERFACES]


def read_uptime(path=PROC_UPTIME):
    with open(path) as fh:
        return float(fh.read().split()[0])


def read_counter(iface, name, sys_net=SYS_NET):
    with open(os.path.join(sys_net, iface, "statistics", name)) as fh:
        return int(fh.read().strip())


def net_lines(ifaces, uptime, sys_net=SYS_NET):
    """Eine NET-Zeile je lesbarem Interface. Fehlende Interfaces werden uebersprungen."""
    lines = []
    for name in ifaces:
        try:
            rx = read_counter(name, "rx_bytes", sys_net)
            tx = read_counter(name, "tx_bytes", sys_net)
        except (OSError, ValueError):
            continue
        lines.append(f"NET {uptime:.2f} {name} {rx} {tx}")
    return lines


def _read(path):
    with open(path) as fh:
        return fh.read()


def read_temperature(hwmon=HWMON):
    """CPU-Temperatur in Grad C oder None. Sucht den Treiber nach Namen, nicht nach hwmonN
    (die Nummern aendern sich), und nimmt bevorzugt 'Package id 0' bzw. Tctl/Tdie, sonst das Maximum."""
    try:
        dirs = sorted(os.listdir(hwmon))
    except OSError:
        return None
    by_driver = {}
    for d in dirs:
        base = os.path.join(hwmon, d)
        try:
            name = _read(os.path.join(base, "name")).strip()
        except OSError:
            continue
        if name in TEMP_DRIVERS and name not in by_driver:
            by_driver[name] = base
    for driver in TEMP_DRIVERS:
        base = by_driver.get(driver)
        if not base:
            continue
        values, preferred = [], None
        for fn in sorted(os.listdir(base)):
            if not (fn.startswith("temp") and fn.endswith("_input")):
                continue
            try:
                milli = int(_read(os.path.join(base, fn)).strip())
            except (OSError, ValueError):
                continue
            if not -50000 < milli < 150000:                  # Muell-Werte verwerfen
                continue
            values.append(milli)
            try:
                label = _read(os.path.join(base, fn[:-6] + "_label")).strip().lower()
            except OSError:
                label = ""
            if label in TEMP_LABELS and preferred is None:
                preferred = milli
        chosen = preferred if preferred is not None else (max(values) if values else None)
        if chosen is not None:
            return round(chosen / 1000.0, 1)
    return None


def sys_line(uptime, loadavg=PROC_LOADAVG, meminfo=PROC_MEMINFO, hwmon=HWMON):
    """SYS-Zeile oder None, wenn Last oder Speicher nicht lesbar sind."""
    try:
        l1, l5, l15 = (float(x) for x in _read(loadavg).split()[:3])
        mem = {}
        for line in _read(meminfo).splitlines():
            key, _, rest = line.partition(":")
            if key in ("MemTotal", "MemAvailable"):
                mem[key] = int(rest.split()[0])
        total, avail = mem["MemTotal"], mem["MemAvailable"]
    except (OSError, ValueError, KeyError, IndexError):
        return None
    temp = read_temperature(hwmon)
    return (f"SYS {uptime:.2f} {os.cpu_count() or 1} {l1:.2f} {l5:.2f} {l15:.2f} "
            f"{total} {avail} {'-' if temp is None else f'{temp:.1f}'}")


def sanitize_name(name):
    return NAME_RE.sub("_", str(name))[:32] or "_"


def read_guests(path=GUESTS_FILE, now=None):
    """Gaeste-Datei des Collectors lesen und validieren.
    -> ("ok", alter_s, [(vmid, typ, status, name), ...]) oder ("missing",)."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            return ("missing",)
        data = json.loads(raw)
        ts = int(data["ts"])
        guests = data["guests"]
        if not isinstance(guests, list):
            return ("missing",)
    except (OSError, ValueError, KeyError, TypeError):
        return ("missing",)
    items = []
    for g in guests[:MAX_GUESTS]:
        try:
            vmid = int(g["vmid"])
            kind = g["type"]
            status = {"running": "running", "stopped": "stopped"}.get(g["status"], "other")
        except (KeyError, TypeError, ValueError):
            continue
        if not 1 <= vmid <= 999999999 or kind not in ("qemu", "lxc"):
            continue
        items.append((vmid, kind, status, sanitize_name(g.get("name", ""))))
    now = time.time() if now is None else now
    return ("ok", max(0, int(now - ts)), items)


def guests_lines(result):
    if result[0] != "ok":
        return ["MISSING GUESTS"]
    _, age, items = result
    return ([f"BEGIN GUESTS {age}"] + [f"G {v} {k} {st} {n}" for v, k, st, n in items] + ["END GUESTS"])


def handle_command(line):
    """Eine Befehlszeile pruefen. Immer eine CMDRESULT-Zeile als Antwort.

    Nur exakte Whitelist-Treffer werden ausgefuehrt; alles andere ergibt 'denied'.
    Der Name wird in der Antwort nur gespiegelt, wenn er ein harmloses Token ist.
    """
    parts = line.split()
    name = parts[0] if parts else "?"
    shown = name if TOKEN_RE.match(name) else "?"
    handler = COMMANDS.get(name)
    if handler is None or not all(TOKEN_RE.match(a) for a in parts[1:]):
        return f"CMDRESULT {shown} denied"
    return f"CMDRESULT {shown} {handler(parts[1:])}"


def tick_lines(ifaces, tick, sys_every=SYS_EVERY, guests_every=GUESTS_EVERY):
    """Alle Zeilen, die in diesem Takt faellig sind (NET immer, SYS/GUESTS seltener)."""
    uptime = read_uptime()
    lines = net_lines(ifaces, uptime)
    if tick % sys_every == 0:
        line = sys_line(uptime)
        if line:
            lines.append(line)
    if tick % guests_every == 0:
        lines.extend(guests_lines(read_guests()))
    return lines


def run(ifaces, out, in_fd, interval=1.0, once=False, sys_every=SYS_EVERY, guests_every=GUESTS_EVERY):
    """Hauptschleife. Gibt 0 zurueck, wenn der Client weg ist."""
    out.write(f"HELLO homeserver-agent {PROTOCOL}\n")
    out.flush()
    buf = b""
    tick = 0
    next_tick = time.monotonic()
    while True:
        now = time.monotonic()
        if now >= next_tick:
            try:
                for line in tick_lines(ifaces, tick, sys_every, guests_every):
                    out.write(line + "\n")
                out.flush()
            except BrokenPipeError:
                return 0
            except OSError:
                pass
            tick += 1
            if once:
                return 0
            next_tick += interval
            if next_tick < now:
                next_tick = now + interval
        ready, _, _ = select.select([in_fd], [], [], max(0.0, next_tick - time.monotonic()))
        if not ready:
            continue
        chunk = os.read(in_fd, 4096)
        if not chunk:
            return 0                                # EOF: Client ist weg
        buf += chunk
        while b"\n" in buf:
            raw, buf = buf.split(b"\n", 1)
            if len(raw) > MAX_CMD_BYTES:
                out.write("CMDRESULT ? denied\n")
            else:
                out.write(handle_command(raw.decode("ascii", "replace")) + "\n")
            out.flush()
        if len(buf) > MAX_CMD_BYTES:                # Zeile ohne Ende: verwerfen
            buf = b""


def main(argv=None):
    # Der Client kann dem Agenten keine Argumente mitgeben (command= im Key);
    # die Optionen sind nur fuer Tests und die Installation gedacht.
    ap = argparse.ArgumentParser()
    ap.add_argument("--conf", default=CONF)
    ap.add_argument("--once", action="store_true", help="einen Takt ausgeben und beenden")
    args = ap.parse_args(argv)
    ifaces = load_interfaces(args.conf)
    try:
        return run(ifaces, sys.stdout, sys.stdin.fileno(), once=args.once)
    except (BrokenPipeError, KeyboardInterrupt):
        return 0


if __name__ == "__main__":
    sys.exit(main())
