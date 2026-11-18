"""Proxmox-Host: dauerhafte SSH-Verbindung zum Server-Agenten.

Der Key auf dem Server ist per command= an den Agenten gebunden. Der Agent sendet
typisierte Zeilen (siehe protocol.py) und liest Befehle von stdin. stdin ist
zugleich das Lebenszeichen: schliesst der Client (auch wenn er abgeschossen wird),
meldet der Kernel EOF, der Agent beendet sich und damit die Sitzung. Deshalb
braucht der Client kein preexec_fn (das ist mit Threads laut Python-Doku nicht
sicher) und hinterlaesst keine Waisen-Prozesse.
"""
import re
import subprocess
import threading
import time

from . import errors, protocol
from .base import STALE_S, CardState, Source

MISSING_IFACE_TICKS = 3     # so viele Takte ohne die Haupt-Schnittstelle -> Fehler
SYSTEM_STALE_S = 15.0       # Agent sendet SYS alle 5 s
GUESTS_STALE_S = 90.0       # Agent sendet Gaeste alle 30 s
COLLECTOR_MAX_AGE_S = 120   # aelter -> der Collector auf dem Server liefert nicht mehr
CMD_RE = re.compile(r"^[ -~]{1,128}$")      # druckbares ASCII, begrenzte Laenge


class AgentSource(Source):
    """Eine SSH-Verbindung, mehrere Karten: net (Hauptkarte), system, guests."""
    card_name = "net"

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.proc = None
        self.send_lock = threading.Lock()
        self.extra = {"system": CardState(SYSTEM_STALE_S), "guests": CardState(GUESTS_STALE_S)}

    def snapshots(self):
        out = {"net": self.snapshot()}
        out.update({name: card.snapshot() for name, card in self.extra.items()})
        return out

    def fail(self, reason):
        """Grund fuer ALLE Karten dieser Verbindung (Verbindungsfehler betrifft alle)."""
        super().fail(reason)
        for card in self.extra.values():
            card.fail(reason)

    # ---- Lebenszyklus ------------------------------------------------------
    def run(self):
        backoff = 1.0
        while not self.stop_event.is_set():
            got_data = self.session()
            backoff = 1.0 if got_data else min(backoff * 2, 10.0)
            self.stop_event.wait(backoff)

    def _command(self):
        c = self.cfg
        return ["ssh", "-i", c["ssh_key"], "-o", "IdentitiesOnly=yes",
                "-o", f"UserKnownHostsFile={c['ssh_known_hosts']}",
                "-o", "StrictHostKeyChecking=yes", "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=4", "-o", "ServerAliveInterval=2",
                "-o", "ServerAliveCountMax=2", "-o", "LogLevel=ERROR",
                "-T", f"{c['ssh_user']}@{c['ssh_host']}"]

    def session(self):
        try:
            proc = subprocess.Popen(
                self._command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True)
        except OSError:
            self.fail("ssh")
            return False
        self.proc = proc
        reason = self._read_stream(proc)
        got = reason is None
        self._close(proc)
        for card in self.extra.values():            # Verbindung weg: sofort offline, nicht erst nach Ablauf
            card.invalidate()
        err_text = self._read_stderr(proc)
        for pipe in (proc.stdout, proc.stderr):     # sonst leckt jeder Reconnect Deskriptoren
            try:
                pipe.close()
            except (OSError, ValueError):
                pass
        if not self.stop_event.is_set():
            if reason is None or reason == "":
                reason = errors.classify_ssh_error(err_text)
            self.fail(reason)
        return got

    def _read_stream(self, proc):
        """Liest den Strom bis zum Ende. Gibt einen Grund-Code zurueck, wenn der Agent
        nicht passt, '' bei normalem Ende, None wenn Daten flossen."""
        main = self.cfg["main_iface"]
        handshake = False
        got = False
        prev = {}               # iface -> (uptime, rx, tx); leer = neu aufsetzen
        rates = {}              # iface -> (monotonic, {"rx":..,"tx":..})
        tick_up, tick_names, misses = None, set(), 0
        block = None            # laufender Gaeste-Block: {"age": s, "items": [...]}
        for line in proc.stdout:
            if self.stop_event.is_set():
                break
            if not handshake:
                parsed = protocol.parse_line(line)
                if parsed and parsed[0] == "HELLO":
                    if parsed[1][1] != protocol.PROTOCOL_VERSION:
                        return "agent-protocol"
                    handshake = True
                    continue
                return "agent-outdated" if protocol.is_legacy_counters(line) else "agent-protocol"
            parsed = protocol.parse_line(line)
            if not parsed:
                continue
            kind = parsed[0]
            if kind == "SYS":
                self._publish_system(parsed[1])
                continue
            if kind in ("BEGIN", "G", "END", "MISSING"):
                block = self._guests_block(block, kind, parsed[1])
                continue
            if kind != "NET":
                continue
            up, name, rx, tx = parsed[1]
            if up != tick_up:                       # neuer Takt: letzten bewerten
                if tick_up is not None:
                    misses = 0 if main in tick_names else misses + 1
                    if misses >= MISSING_IFACE_TICKS:
                        return "iface-missing"
                tick_up, tick_names = up, set()
            tick_names.add(name)
            old = prev.get(name)
            prev[name] = (up, rx, tx)
            if old is not None:
                r_rx = protocol.compute_rate((old[0], old[1]), (up, rx))
                r_tx = protocol.compute_rate((old[0], old[2]), (up, tx))
                if r_rx is not None and r_tx is not None:
                    rates[name] = (time.monotonic(), {"rx": round(r_rx, 1), "tx": round(r_tx, 1)})
                else:
                    rates.pop(name, None)
            if name == main and main in rates:
                now = time.monotonic()
                fresh = {k: v for k, (t, v) in rates.items() if now - t < STALE_S}
                self.publish({"state": "online", "iface": main,
                              "rx": fresh[main]["rx"], "tx": fresh[main]["tx"], "ifaces": fresh})
                got = True
        return None if got else ""

    def _publish_system(self, d):
        self.extra["system"].publish({
            "state": "online", "ncpu": d["ncpu"], "load": list(d["load"]),
            "memTotalKb": d["mem_total_kb"], "memAvailKb": d["mem_avail_kb"],
            "tempC": d["temp_c"], "uptime": d["uptime"]})

    def _guests_block(self, block, kind, data):
        """Gaeste-Bloecke zusammensetzen; ein Block ersetzt die ganze Liste atomar."""
        card = self.extra["guests"]
        if kind == "BEGIN":
            return {"age": data[1], "items": []}
        if kind == "G":
            if block is not None and len(block["items"]) < protocol.MAX_GUESTS:
                vmid, gtype, status, name = data
                block["items"].append({"vmid": vmid, "type": gtype, "status": status, "name": name})
            return block
        if kind == "MISSING":
            card.fail("collector-offline")
            card.invalidate()
            return None
        if block is not None:                       # END
            if block["age"] > COLLECTOR_MAX_AGE_S:
                card.fail("collector-offline")
                card.invalidate()
            else:
                items = block["items"]
                card.publish({"state": "online", "age": block["age"], "items": items,
                              "running": sum(1 for g in items if g["status"] == "running"),
                              "total": len(items)})
        return None

    def _close(self, proc):
        """EOF auf stdin lassen (Agent beendet sich), sonst nachhelfen."""
        try:
            proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    def _read_stderr(self, proc):
        try:
            return proc.stderr.read()
        except (OSError, ValueError):
            return ""

    def stop(self):
        super().stop()
        proc = self.proc
        if proc and proc.poll() is None:
            self._close(proc)

    # ---- Befehle an den Agenten (feste Whitelist liegt serverseitig) ---------
    def send(self, line):
        """Eine Befehlszeile ueber dieselbe Verbindung senden. True bei Erfolg."""
        if not CMD_RE.match(line):
            return False
        proc = self.proc
        if proc is None or proc.stdin is None or proc.poll() is not None:
            return False
        with self.send_lock:
            try:
                proc.stdin.write(line + "\n")
                proc.stdin.flush()
            except (OSError, ValueError):
                return False
        return True
