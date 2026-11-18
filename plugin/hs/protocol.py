"""Zeilenprotokoll Server -> Client und die Rate-Berechnung.

Jede Zeile beginnt mit einem Typ-Praefix, Felder sind durch Leerzeichen getrennt:

    HELLO homeserver-agent <version>
    NET <uptime> <iface> <rx_bytes> <tx_bytes>
    SYS <uptime> <ncpu> <l1> <l5> <l15> <memtotal_kB> <memavail_kB> <temp_C|->
    BEGIN GUESTS <alter_s> / G <vmid> <qemu|lxc> <running|stopped|other> <name> / END GUESTS
    MISSING GUESTS
    PING <uptime> <ip> <sent> <received> <avg_ms|->
    PINGERR <unavailable|failed>

Unbekannte Typen werden ignoriert (neuere Agenten bleiben kompatibel), ungueltige
Zeilen ergeben None. Die Zeilenlaenge ist begrenzt.
"""
import math
import ipaddress
import re

PROTOCOL_VERSION = 1
AGENT_NAME = "homeserver-agent"
MAX_LINE = 512
IFACE_RE = re.compile(r"^[A-Za-z0-9._-]{1,15}$")
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
MAX_GUESTS = 64
MAX_SECTION_ITEMS = 64
SECTIONS = ("GUESTS", "STORAGE", "SMART", "SPEED")      # Bloecke: BEGIN <name> <alter> ... END <name>
TOKEN_RE = re.compile(r"^[A-Za-z0-9._:-]{1,32}$")
CMD_RESULTS = ("ok", "busy", "cooldown", "notinstalled", "unavailable", "failed", "denied")
KIND_RE = re.compile(r"^[A-Za-z0-9._-]{1,16}$")
DEV_RE = re.compile(r"^[a-z0-9]{1,16}$")
MAX_GAP_S = 5.0     # groesserer Zeitsprung zwischen Zaehlerstaenden -> neu aufsetzen


def compute_rate(prev, cur, max_gap=MAX_GAP_S):
    """Rate in Bytes/s aus zwei Zaehlerstaenden (zeit, zaehler).

    Gibt None zurueck, wenn keine verlaessliche Rate moeglich ist: Zeit steht
    still oder laeuft rueckwaerts (Server-Neustart), die Luecke ist zu gross
    (Verbindung war weg) oder der Zaehler ist gesunken (Reset). Der Aufrufer
    setzt dann den Vergleichswert neu auf.
    """
    dt = cur[0] - prev[0]
    if not (0 < dt <= max_gap):
        return None
    delta = cur[1] - prev[1]
    if delta < 0:
        return None
    return delta / dt


def _number(text):
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) and value >= 0 else None


def parse_line(line):
    """Eine Agent-Zeile -> (typ, daten) oder None.

    HELLO -> ("HELLO", (name, version))
    NET   -> ("NET", (uptime, iface, rx, tx))
    """
    if len(line) > MAX_LINE:
        return None
    parts = line.split()
    if not parts:
        return None
    kind = parts[0]
    if kind == "HELLO":
        if len(parts) == 3 and parts[1] == AGENT_NAME and parts[2].isdigit():
            return "HELLO", (parts[1], int(parts[2]))
        return None
    if kind == "NET":
        if len(parts) != 5 or not IFACE_RE.match(parts[2]):
            return None
        up, rx, tx = _number(parts[1]), _number(parts[3]), _number(parts[4])
        if None in (up, rx, tx):
            return None
        return "NET", (up, parts[2], rx, tx)
    if kind == "SYS":
        return _parse_sys(parts)
    if kind == "PING":
        return _parse_ping(parts)
    if kind == "PINGERR":
        return ("PINGERR", parts[1]) if len(parts) == 2 and parts[1] in ("unavailable", "failed") else None
    if kind == "BEGIN":
        if len(parts) == 3 and parts[1] in SECTIONS and parts[2].isdigit():
            return "BEGIN", (parts[1], int(parts[2]))
        return None
    if kind in ("END", "MISSING"):
        return (kind, parts[1]) if len(parts) == 2 and parts[1] in SECTIONS else None
    if kind == "SPEEDSTATE":
        return _parse_speedstate(parts)
    if kind == "R":
        return _parse_speed_result(parts)
    if kind == "CMDRESULT":
        if len(parts) == 3 and TOKEN_RE.match(parts[1]) and parts[2] in CMD_RESULTS:
            return "CMDRESULT", (parts[1], parts[2])
        return None
    if kind == "S":
        return _parse_storage(parts)
    if kind == "D":
        return _parse_disk(parts)
    if kind == "G":
        if len(parts) != 5 or not parts[1].isdigit() or not NAME_RE.match(parts[4]):
            return None
        vmid = int(parts[1])
        if not 1 <= vmid <= 999999999 or parts[2] not in ("qemu", "lxc") \
                or parts[3] not in ("running", "stopped", "other"):
            return None
        return "G", (vmid, parts[2], parts[3], parts[4])
    return None


def _parse_storage(parts):
    if len(parts) != 6 or not NAME_RE.match(parts[1]) or not KIND_RE.match(parts[2]) \
            or parts[3] not in ("active", "inactive") or not parts[4].isdigit() or not parts[5].isdigit():
        return None
    return "S", (parts[1], parts[2], parts[3], int(parts[4]), int(parts[5]))


def _parse_disk(parts):
    if len(parts) != 4 or not DEV_RE.match(parts[1]) or parts[2] not in ("passed", "failed", "standby", "unknown"):
        return None
    if parts[3] == "-":
        temp = None
    else:
        if not re.match(r"^-?\d{1,3}$", parts[3]) or not -50 < int(parts[3]) < 150:
            return None
        temp = int(parts[3])
    return "D", (parts[1], parts[2], temp)


def _parse_speedstate(parts):
    if len(parts) != 4 or parts[1] not in ("running", "idle", "notinstalled", "unavailable") \
            or not parts[2].isdigit() or int(parts[2]) > 86400 or parts[3] not in ("0", "1", "-"):
        return None
    return "SPEEDSTATE", {"state": parts[1], "cooldown": int(parts[2]),
                          "ok": None if parts[3] == "-" else parts[3] == "1"}


def _parse_speed_result(parts):
    if len(parts) != 5 or not all(p.isdigit() for p in parts[1:4]):
        return None
    age, down, up = int(parts[1]), int(parts[2]), int(parts[3])
    ping = _number(parts[4])
    if ping is None or not (0 < down < 10_000_000 and 0 < up < 10_000_000 and 0 <= ping < 10_000 and age < 10 * 365 * 86400):
        return None
    return "R", (age, down, up, ping)


def _parse_ping(parts):
    if len(parts) != 6 or not parts[3].isdigit() or not parts[4].isdigit():
        return None
    try:
        ipaddress.ip_address(parts[2])
    except ValueError:
        return None
    sent, recv = int(parts[3]), int(parts[4])
    if not 0 < sent <= 100 or recv > sent or _number(parts[1]) is None:
        return None
    if parts[5] == "-":
        avg = None
    else:
        avg = _number(parts[5])
        if avg is None or avg >= 100000:
            return None
    if (avg is None) != (recv == 0):
        return None
    return "PING", {"target": parts[2], "sent": sent, "recv": recv, "avg_ms": avg}


def _parse_sys(parts):
    if len(parts) != 9 or not parts[2].isdigit() or not 1 <= int(parts[2]) <= 4096:
        return None
    up, l1, l5, l15, total, avail = (_number(p) for p in (parts[1], *parts[3:8]))
    if None in (up, l1, l5, l15, total, avail) or total <= 0 or avail > total:
        return None
    if parts[8] == "-":
        temp = None
    else:
        try:
            temp = float(parts[8])
        except ValueError:
            return None
        if not -50.0 < temp < 150.0:
            return None
    return "SYS", {"uptime": up, "ncpu": int(parts[2]), "load": (l1, l5, l15),
                   "mem_total_kb": total, "mem_avail_kb": avail, "temp_c": temp}


def is_legacy_counters(line):
    """Der alte Agent (traffic-counters.sh) sendet nur fuenf Zahlen pro Zeile."""
    parts = line.split()
    return len(parts) == 5 and all(_number(p) is not None for p in parts)
