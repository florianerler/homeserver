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
  BEGIN STORAGE <alter_s> / S <name> <typ> <active|inactive> <total_kB> <used_kB> / END STORAGE   alle 30 s
  BEGIN SMART <alter_s> / D <geraet> <passed|failed|standby|unknown> <temp_C|-> / END SMART       alle 30 s
  MISSING STORAGE | MISSING SMART
  SPEEDSTATE <running|idle|notinstalled|unavailable> <cooldown_s> <1|0|->   alle 5 s
  BEGIN SPEED 0 / R <alter_s> <down_kbit> <up_kbit> <ping_ms> / END SPEED         bei neuem Ergebnis, sonst alle 5 min
  CMDRESULT <name> <ok|busy|cooldown|notinstalled|unavailable|failed|denied>      Antwort auf einen Befehl
  PING <uptime> <ziel-ip> <gesendet> <empfangen> <avg_ms|->       alle 5 s (unprivilegiertes ping, 3 Pakete)
  PINGERR <unavailable|failed>                                    ping nicht nutzbar
"""
import argparse
import ipaddress
import configparser
import json
import os
import re
import shutil
import subprocess
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
STORAGE_FILE = "/run/homeserver/storage.json"
SMART_FILE = "/run/homeserver/smart.json"
DEV_RE = re.compile(r"^[a-z0-9]{1,16}$")
SPEED_DIR = "/var/lib/homeserver-speedtest"
SPEED_TRIGGER = "/run/homeserver-trigger/speedtest"
SPEED_MIN_SPACING_S = 600
SPEED_RUNNING_MAX_S = 300           # laenger "running" ohne Ende = Absturz, nicht ewig "laeuft"
SPEED_BLOCK_EVERY = 300             # Takte
SPEED_MAX_ENTRIES = 240
TEMP_DRIVERS = ("coretemp", "k10temp", "zenpower", "cpu_thermal")     # Reihenfolge = Vorrang
TEMP_LABELS = ("package id 0", "tctl", "tdie", "cpu")
SYS_EVERY = 5               # Takte (Sekunden) zwischen SYS-Zeilen
GUESTS_EVERY = 30           # Takte zwischen Gaeste-Bloecken
MAX_GUESTS = 64
MAX_FILE_BYTES = 65536
NAME_RE = re.compile(r"[^A-Za-z0-9._-]")
PING_DEFAULT_TARGET = "1.1.1.1"
PING_EVERY_S = 5.0
PING_TIMEOUT_S = 8.0
PING_COUNT = 3
PING_PATHS = ("/usr/bin/ping", "/bin/ping")
PING_SUMMARY_RE = re.compile(r"(\d+) packets transmitted, (\d+) (?:packets )?received")
PING_RTT_RE = re.compile(r"= [\d.]+/([\d.]+)/[\d.]+")

# Befehls-Whitelist: Name -> Funktion(argumente) -> Ergebnis-Code.
# Phase 0: noch keine Befehle. Spaetere Phasen tragen hier ihre festen Befehle ein.
COMMANDS = {}               # wird unten (nach den Funktionen) gefuellt: feste Whitelist


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


def _load_file(path):
    """JSON-Datei des Collectors lesen (begrenzt): (ts, dict) oder None."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            return None
        data = json.loads(raw)
        return int(data["ts"]), data
    except (OSError, ValueError, KeyError, TypeError):
        return None


def read_storage(path=STORAGE_FILE, now=None):
    loaded = _load_file(path)
    if not loaded or not isinstance(loaded[1].get("storages"), list):
        return ("missing",)
    ts, data = loaded
    items = []
    for g in data["storages"][:32]:
        try:
            name, kind = str(g["name"]), str(g["type"])
            total, used = int(g["total_kb"]), int(g["used_kb"])
            status = "active" if g["status"] == "active" else "inactive"
        except (KeyError, TypeError, ValueError):
            continue
        if name != sanitize_name(name) or kind != sanitize_name(kind) or len(kind) > 16 or total < 0 or used < 0:
            continue
        items.append((name, kind, status, total, used))
    now = time.time() if now is None else now
    return ("ok", max(0, int(now - ts)), items)


def read_smart(path=SMART_FILE, now=None):
    loaded = _load_file(path)
    if not loaded or not isinstance(loaded[1].get("disks"), list):
        return ("missing",)
    ts, data = loaded
    items = []
    for g in data["disks"][:8]:
        try:
            dev = str(g["dev"])
            status = g["status"] if g["status"] in ("passed", "failed", "standby", "unknown") else "unknown"
            temp = g.get("temp")
            temp = int(temp) if isinstance(temp, (int, float)) and -50 < temp < 150 else None
        except (KeyError, TypeError, ValueError):
            continue
        if not DEV_RE.match(dev):
            continue
        items.append((dev, status, temp))
    now = time.time() if now is None else now
    return ("ok", max(0, int(now - ts)), items)


def section_lines(name, result, fmt):
    """Block 'BEGIN <name> <alter> ... END <name>' oder 'MISSING <name>'."""
    if result[0] != "ok":
        return [f"MISSING {name}"]
    _, age, items = result
    return [f"BEGIN {name} {age}"] + [fmt(i) for i in items] + [f"END {name}"]


def storage_lines(result):
    return section_lines("STORAGE", result, lambda i: f"S {i[0]} {i[1]} {i[2]} {i[3]} {i[4]}")


def smart_lines(result):
    return section_lines("SMART", result, lambda i: f"D {i[0]} {i[1]} {'-' if i[2] is None else i[2]}")


def guests_lines(result):
    if result[0] != "ok":
        return ["MISSING GUESTS"]
    _, age, items = result
    return ([f"BEGIN GUESTS {age}"] + [f"G {v} {k} {st} {n}" for v, k, st, n in items] + ["END GUESTS"])


def load_ping_target(path=CONF):
    """Ping-Ziel aus der Config: nur IP-Literale (kein DNS, keine Argument-Einschleusung).
    Leer = Ping aus (None)."""
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(path)
    raw = cp.get("agent", "ping_target", fallback=PING_DEFAULT_TARGET).strip()
    if not raw:
        return None
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        return None


def parse_ping(text):
    """Ausgabe von ping -> (gesendet, empfangen, avg_ms|None) oder None."""
    m = PING_SUMMARY_RE.search(text)
    if not m:
        return None
    sent, recv = int(m.group(1)), int(m.group(2))
    if not 0 < sent <= 100 or recv > sent:
        return None
    avg = None
    r = PING_RTT_RE.search(text)
    if r and recv > 0:
        avg = float(r.group(1))
        if not 0 <= avg < 100000:
            return None
    return sent, recv, avg


def ping_line(uptime, target, result):
    sent, recv, avg = result
    return f"PING {uptime:.2f} {target} {sent} {recv} {'-' if avg is None else f'{avg:.2f}'}"


class Pinger:
    """Startet alle PING_EVERY_S s ein kurzes ping (nicht blockierend, damit die Netz-Zaehler
    weiter jede Sekunde kommen). Festes Programm, feste Argumente, IP-Literal als Ziel, keine Shell."""

    def __init__(self, target, popen=subprocess.Popen, every=PING_EVERY_S, timeout=PING_TIMEOUT_S, path=None):
        self.target = target
        self.popen = popen
        self.every = every
        self.timeout = timeout
        self.path = path or next((p for p in PING_PATHS if os.access(p, os.X_OK)), shutil.which("ping"))
        self.proc = None
        self.started = 0.0
        self.next_start = 0.0

    def argv(self):
        return [self.path, "-n", "-c", str(PING_COUNT), "-i", "0.3", "-W", "1", self.target]

    def step(self, now, uptime):
        """Gibt eine Zeile zurueck, wenn ein Ergebnis vorliegt, sonst None."""
        if self.target is None:
            return None
        if self.proc is None:
            if now < self.next_start:
                return None
            self.next_start = now + self.every
            if not self.path:
                return "PINGERR unavailable"
            try:
                self.proc = self.popen(self.argv(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, env={"PATH": "/usr/bin:/bin", "LANG": "C"})
            except OSError:
                return "PINGERR unavailable"
            self.started = now
            return None
        if self.proc.poll() is None:
            if now - self.started > self.timeout:
                self.proc.kill()
                self._reap()
                return "PINGERR failed"
            return None
        text = self._reap()
        result = parse_ping(text)
        if result:
            return ping_line(uptime, self.target, result)
        return "PINGERR unavailable" if "not permitted" in text or "denied" in text else "PINGERR failed"

    def _reap(self):
        proc, self.proc = self.proc, None
        try:
            text = proc.communicate(timeout=2)[0] or ""
        except (subprocess.SubprocessError, OSError):
            proc.kill()
            text = ""
        return text[:4096]

    def close(self):
        if self.proc is not None:
            self.proc.kill()
            self._reap()

    @property
    def busy(self):
        return self.proc is not None


def read_speed_state(speed_dir=SPEED_DIR, trigger=SPEED_TRIGGER, now=None):
    """-> (zustand, cooldown_s, letzter_lauf_ok|None). zustand: running|idle|notinstalled|unavailable."""
    now = int(time.time() if now is None else now)
    if not os.path.isdir(speed_dir):
        return "unavailable", 0, None
    try:
        with open(os.path.join(speed_dir, "state.json"), "rb") as fh:
            d = json.loads(fh.read(MAX_FILE_BYTES))
        if not isinstance(d, dict):
            d = {}
    except (OSError, ValueError):
        d = {}
    state = d.get("state")
    finished = d.get("finished") if isinstance(d.get("finished"), int) else None
    started = d.get("started") if isinstance(d.get("started"), int) else None
    if state == "notinstalled":
        return "notinstalled", 0, None
    running = state == "running" and started is not None and 0 <= now - started <= SPEED_RUNNING_MAX_S
    if os.path.exists(trigger):
        running = True                                  # angefordert, Dienst startet gleich
    cooldown = max(0, finished + SPEED_MIN_SPACING_S - now) if finished is not None else 0
    ok = d.get("ok") if isinstance(d.get("ok"), bool) else None
    return ("running" if running else "idle"), cooldown, ok


def speed_state_line(state):
    st, cooldown, ok = state
    return f"SPEEDSTATE {st} {cooldown} {'-' if ok is None else int(ok)}"


def read_speed_history(speed_dir=SPEED_DIR, now=None):
    """history.jsonl -> [(alter_s, down_kbit, up_kbit, ping_ms)], aelteste zuerst, validiert."""
    now = int(time.time() if now is None else now)
    out = []
    try:
        with open(os.path.join(speed_dir, "history.jsonl"), "rb") as fh:
            raw = fh.read(MAX_FILE_BYTES * 4)
    except OSError:
        return out
    for line in raw.decode("utf-8", "replace").splitlines():
        try:
            e = json.loads(line)
            ts, down, up, ping = int(e["ts"]), int(e["down"]), int(e["up"]), float(e["ping"])
        except (ValueError, KeyError, TypeError):
            continue
        if 0 < down < 10_000_000 and 0 < up < 10_000_000 and 0 <= ping < 10_000 and ts <= now:
            out.append((now - ts, down, up, round(ping, 1)))
    return out[-SPEED_MAX_ENTRIES:]


def speed_block_lines(speed_dir=SPEED_DIR, now=None):
    if not os.path.isdir(speed_dir):
        return ["MISSING SPEED"]
    return ["BEGIN SPEED 0"] + [f"R {a} {d} {u} {p:.1f}" for a, d, u, p in read_speed_history(speed_dir, now)] + ["END SPEED"]


class SpeedReporter:
    """Speedtest-Zeilen: Zustand alle 5 s, Verlauf nur bei Aenderung der Datei (oder alle 5 min)."""

    def __init__(self, speed_dir=SPEED_DIR, trigger=SPEED_TRIGGER):
        self.dir, self.trigger, self.mtime = speed_dir, trigger, None

    def state_line(self):
        return speed_state_line(read_speed_state(self.dir, self.trigger))

    def lines(self, tick, sys_every=SYS_EVERY):
        out = []
        if tick % sys_every == 0:
            out.append(self.state_line())
        try:
            mtime = os.stat(os.path.join(self.dir, "history.jsonl")).st_mtime_ns
        except OSError:
            mtime = None
        if self.mtime != mtime or tick % SPEED_BLOCK_EVERY == 0:
            self.mtime = mtime
            out.extend(speed_block_lines(self.dir))
        return out


def cmd_speedtest(args, speed_dir=SPEED_DIR, trigger=SPEED_TRIGGER):
    """Speedtest anfordern: legt nur die Trigger-Datei an; der Test laeuft als eigener Dienst."""
    if args:
        return "denied"
    state, cooldown, _ = read_speed_state(speed_dir, trigger)
    if state in ("notinstalled", "unavailable"):
        return state
    if state == "running":
        return "busy"
    if cooldown > 0:
        return "cooldown"
    try:
        with open(trigger, "w"):
            pass
    except OSError:
        return "failed"
    return "ok"


COMMANDS["speedtest"] = cmd_speedtest


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


def tick_lines(ifaces, tick, sys_every=SYS_EVERY, guests_every=GUESTS_EVERY, speed=None):
    """Alle Zeilen, die in diesem Takt faellig sind (NET immer, SYS/GUESTS seltener)."""
    uptime = read_uptime()
    lines = net_lines(ifaces, uptime)
    if tick % sys_every == 0:
        line = sys_line(uptime)
        if line:
            lines.append(line)
    if tick % guests_every == 0:
        lines.extend(guests_lines(read_guests()))
        lines.extend(storage_lines(read_storage()))
        lines.extend(smart_lines(read_smart()))
    if speed is not None:
        lines.extend(speed.lines(tick, sys_every))
    return lines


def run(ifaces, out, in_fd, interval=1.0, once=False, sys_every=SYS_EVERY, guests_every=GUESTS_EVERY, pinger=None):
    """Hauptschleife. Gibt 0 zurueck, wenn der Client weg ist."""
    try:
        return _run(ifaces, out, in_fd, interval, once, sys_every, guests_every, pinger)
    finally:
        if pinger:
            pinger.close()


def _run(ifaces, out, in_fd, interval, once, sys_every, guests_every, pinger):
    speed = SpeedReporter()
    out.write(f"HELLO homeserver-agent {PROTOCOL}\n")
    out.flush()
    buf = b""
    tick = 0
    next_tick = time.monotonic()
    while True:
        now = time.monotonic()
        if now >= next_tick:
            try:
                for line in tick_lines(ifaces, tick, sys_every, guests_every, speed):
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
        if pinger:
            try:
                line = pinger.step(time.monotonic(), read_uptime())
                if line:
                    out.write(line + "\n")
                    out.flush()
            except BrokenPipeError:
                return 0
            except OSError:
                pass
        wait = max(0.0, next_tick - time.monotonic())
        if pinger and pinger.busy:
            wait = min(wait, 0.2)                    # laufendes ping regelmaessig abfragen
        ready, _, _ = select.select([in_fd], [], [], wait)
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
                out.write(speed.state_line() + "\n")          # Zustand sofort nachliefern (z. B. "laeuft")
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
        target = load_ping_target(args.conf)
        pinger = Pinger(target) if target and not args.once else None
        return run(ifaces, sys.stdout, sys.stdin.fileno(), once=args.once, pinger=pinger)
    except (BrokenPipeError, KeyboardInterrupt):
        return 0


if __name__ == "__main__":
    sys.exit(main())
