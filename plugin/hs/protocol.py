"""Zeilenprotokoll Server -> Client und die Rate-Berechnung.

Jede Zeile beginnt mit einem Typ-Praefix, Felder sind durch Leerzeichen getrennt:

    HELLO homeserver-agent <version>
    NET <uptime> <iface> <rx_bytes> <tx_bytes>

Unbekannte Typen werden ignoriert (neuere Agenten bleiben kompatibel), ungueltige
Zeilen ergeben None. Die Zeilenlaenge ist begrenzt.
"""
import math
import re

PROTOCOL_VERSION = 1
AGENT_NAME = "homeserver-agent"
MAX_LINE = 512
IFACE_RE = re.compile(r"^[A-Za-z0-9._-]{1,15}$")
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
    return None


def is_legacy_counters(line):
    """Der alte Agent (traffic-counters.sh) sendet nur fuenf Zahlen pro Zeile."""
    parts = line.split()
    return len(parts) == 5 and all(_number(p) is not None for p in parts)
