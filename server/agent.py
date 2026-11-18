#!/usr/bin/env python3
"""homeserver-agent: laeuft als Benutzer 'traffic', gebunden per command= im SSH-Key.

Sendet typisierte Zeilen auf stdout (Protokoll siehe README) und liest Befehle von
stdin. Es gibt KEINE Shell und keine freien Argumente: Befehle werden gegen eine
feste Whitelist (COMMANDS) geprueft. stdin-EOF bedeutet "Client weg" -> Ende.

Nur Python-Standardbibliothek. Root-Aufgaben erledigt NICHT dieser Agent, sondern
ein eigener Dienst (ab Phase 1); der Agent liest nur Dateien und /proc, /sys.
"""
import argparse
import configparser
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


def run(ifaces, out, in_fd, interval=1.0, once=False):
    """Hauptschleife. Gibt 0 zurueck, wenn der Client weg ist."""
    out.write(f"HELLO homeserver-agent {PROTOCOL}\n")
    out.flush()
    buf = b""
    next_tick = time.monotonic()
    while True:
        now = time.monotonic()
        if now >= next_tick:
            try:
                for line in net_lines(ifaces, read_uptime()):
                    out.write(line + "\n")
                out.flush()
            except OSError:
                pass
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
