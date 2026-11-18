#!/usr/bin/env python3
"""Streamt Heimserver-Traffic als JSON-Zeilen (eine pro Sekunde) auf stdout.

Quellen:
  * Proxmox-Host: dauerhafte SSH-Verbindung mit eingeschraenktem Key
    (command= in authorized_keys). Der Server liefert jede Sekunde
    "<uptime> <nic0_rx> <nic0_tx> <vmbr0_rx> <vmbr0_tx>".
  * qBittorrent: WebUI-API /api/v2/transfer/info (fertige Live-Raten).

Nur Python-Standardbibliothek. Zugangsdaten stehen ausschliesslich in
~/.config/homeserver-traffic/config.ini und werden nie ausgegeben.
"""
import argparse
import configparser
import ctypes
import http.cookiejar
import json
import math
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_CONFIG = "~/.config/homeserver-traffic/config.ini"
STALE_S = 4.0       # laenger ohne Messwert -> Quelle gilt als offline
MAX_GAP_S = 5.0     # groesserer Zeitsprung zwischen Zaehlerstaenden -> neu aufsetzen
IFACES = ("nic0", "vmbr0")


# --------------------------------------------------------------------------
# Reine Logik (ohne Netzwerk, einzeln testbar)
# --------------------------------------------------------------------------
def compute_rate(prev, cur, max_gap=MAX_GAP_S):
    """Rate in Bytes/s aus zwei Zaehlerstaenden (zeit, zaehler).

    Gibt None zurueck, wenn keine verlaessliche Rate moeglich ist: Zeit steht
    still oder laeuft rueckwaerts, die Luecke ist zu gross (Verbindung war weg)
    oder der Zaehler ist gesunken (Reset, z. B. Interface neu gestartet).
    Der Aufrufer setzt dann den Vergleichswert neu auf.
    """
    dt = cur[0] - prev[0]
    if not (0 < dt <= max_gap):
        return None
    delta = cur[1] - prev[1]
    if delta < 0:
        return None
    return delta / dt


def parse_counters(line):
    """'<uptime> <rx0> <tx0> <rx1> <tx1>' -> (uptime, {iface: (rx, tx)}) oder None."""
    parts = line.split()
    if len(parts) != 5:
        return None
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    if not all(math.isfinite(n) and n >= 0 for n in nums):
        return None
    return nums[0], {IFACES[0]: (nums[1], nums[2]), IFACES[1]: (nums[3], nums[4])}


def classify_ssh_error(stderr_text, returncode):
    """Kurzer, geheimnisfreier Grund fuer die Anzeige."""
    s = stderr_text.lower()
    if "host key verification failed" in s or "remote host identification" in s:
        return "hostkey"
    if "permission denied" in s:
        return "auth"
    if "timed out" in s or "timeout" in s or "no route" in s or "unreachable" in s:
        return "timeout"
    if "refused" in s:
        return "refused"
    if "could not resolve" in s:
        return "dns"
    return "ssh"


# --------------------------------------------------------------------------
# Konfiguration
# --------------------------------------------------------------------------
class ConfigError(Exception):
    pass


def load_config(path):
    path = os.path.expanduser(path)
    cp = configparser.ConfigParser(interpolation=None)  # '%' im Passwort erlaubt
    if not cp.read(path):
        raise ConfigError("config-missing")
    try:
        ssh, qb = cp["ssh"], cp["qbittorrent"]
        cfg = {
            "ssh_host": ssh["host"].strip(),
            "ssh_user": ssh["user"].strip(),
            "ssh_key": os.path.expanduser(ssh["key"].strip()),
            "ssh_known_hosts": os.path.expanduser(ssh["known_hosts"].strip()),
            "main_iface": ssh.get("main_iface", "nic0").strip(),
            "qb_url": qb["url"].strip().rstrip("/"),
            "qb_user": qb["username"].strip(),
            "qb_pass": qb["password"].strip(),
        }
    except KeyError:
        raise ConfigError("config-incomplete")
    if cfg["main_iface"] not in IFACES:
        raise ConfigError("config-iface")
    return cfg


# --------------------------------------------------------------------------
# Quellen
# --------------------------------------------------------------------------
def _pdeathsig():
    """Im Kindprozess: SIGTERM, sobald der Elternthread endet (kein ssh-Waisenkind)."""
    ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG


class Source(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.last_ok = None       # time.monotonic() des letzten gueltigen Werts
        self.error = "connecting"
        self.data = {}

    def publish(self, data):
        with self.lock:
            self.data = data
            self.last_ok = time.monotonic()
            self.error = ""

    def fail(self, reason):
        with self.lock:
            self.error = reason

    def snapshot(self):
        with self.lock:
            fresh = self.last_ok is not None and time.monotonic() - self.last_ok < STALE_S
            out = {"ok": fresh}
            if fresh:
                out.update(self.data)
            else:
                out["state"] = "connecting" if self.error == "connecting" else "offline"
                out["error"] = self.error or "timeout"
            return out

    def stop(self):
        self.stop_event.set()


class HostSource(Source):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.proc = None

    def run(self):
        backoff = 1.0
        while not self.stop_event.is_set():
            got_data = self.session()
            backoff = 1.0 if got_data else min(backoff * 2, 10.0)
            self.stop_event.wait(backoff)

    def session(self):
        c = self.cfg
        cmd = ["ssh", "-i", c["ssh_key"], "-o", "IdentitiesOnly=yes",
               "-o", f"UserKnownHostsFile={c['ssh_known_hosts']}",
               "-o", "StrictHostKeyChecking=yes", "-o", "BatchMode=yes",
               "-o", "ConnectTimeout=4", "-o", "ServerAliveInterval=2",
               "-o", "ServerAliveCountMax=2", "-o", "LogLevel=ERROR",
               "-T", f"{c['ssh_user']}@{c['ssh_host']}"]
        try:
            self.proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, preexec_fn=_pdeathsig)
        except OSError:
            self.fail("ssh")
            return False
        prev = {}          # iface -> (uptime, rx, tx); leer = neu aufsetzen
        got = False
        for line in self.proc.stdout:
            parsed = parse_counters(line)
            if parsed is None:
                continue
            up, counters = parsed
            rates = {}
            for name, (rx, tx) in counters.items():
                old = prev.get(name)
                prev[name] = (up, rx, tx)
                if old is None:
                    continue
                r_rx = compute_rate((old[0], old[1]), (up, rx))
                r_tx = compute_rate((old[0], old[2]), (up, tx))
                if r_rx is not None and r_tx is not None:
                    rates[name] = {"rx": round(r_rx, 1), "tx": round(r_tx, 1)}
            if len(rates) == len(IFACES):
                main = self.cfg["main_iface"]
                self.publish({"state": "online", "iface": main,
                              "rx": rates[main]["rx"], "tx": rates[main]["tx"],
                              "ifaces": rates})
                got = True
        try:
            err = self.proc.stderr.read()
        except (OSError, ValueError):
            err = ""
        rc = self.proc.wait()
        if not self.stop_event.is_set():
            self.fail(classify_ssh_error(err, rc))
        return got

    def stop(self):
        super().stop()
        p = self.proc
        if p and p.poll() is None:
            p.terminate()


class LoginBackoff(Exception):
    """Login ist wegen frueherer Fehlversuche noch gesperrt (kein Netzwerkzugriff)."""


class QbtSource(Source):
    # qBittorrent sperrt Clients standardmaessig 1 h nach 5 aufeinanderfolgenden
    # Login-Fehlern. Nach einem abgelehnten Login deshalb lange warten statt
    # jede Sekunde neu zu probieren (Tippfehler im Passwort = sonst Selbstsperre).
    BACKOFF_START_S = 60.0
    BACKOFF_MAX_S = 900.0

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.login_wait = 0.0           # aktuelle Wartezeit nach Auth-Fehler
        self.next_login = 0.0           # monotonic-Zeitpunkt, ab dem wieder versucht wird
        jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),            # nie ueber einen Proxy
            urllib.request.HTTPCookieProcessor(jar))
        self.logged_in = False

    def _request(self, path, data=None):
        base = self.cfg["qb_url"]
        req = urllib.request.Request(
            base + path, data=data,
            headers={"Referer": base})                  # qBittorrent prueft das (CSRF)
        return self.opener.open(req, timeout=2.5)

    def _login_rejected(self):
        self.login_wait = min(self.login_wait * 2 or self.BACKOFF_START_S, self.BACKOFF_MAX_S)
        self.next_login = time.monotonic() + self.login_wait

    def login(self):
        if time.monotonic() < self.next_login:
            raise LoginBackoff
        try:
            self._do_login()
        except PermissionError:
            self._login_rejected()
            raise
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                self._login_rejected()
            raise
        self.login_wait = 0.0
        self.next_login = 0.0

    def _do_login(self):
        body = urllib.parse.urlencode(
            {"username": self.cfg["qb_user"], "password": self.cfg["qb_pass"]}).encode()
        with self._request("/api/v2/auth/login", body) as resp:
            text = resp.read(64).decode("utf-8", "replace").strip()
            if resp.status not in (200, 204) or text == "Fails.":
                raise PermissionError
        self.logged_in = True

    def poll(self):
        if not self.logged_in:
            self.login()
        try:
            with self._request("/api/v2/transfer/info") as resp:
                info = json.loads(resp.read(65536))
        except urllib.error.HTTPError as e:
            if e.code == 403:
                self.logged_in = False      # Sitzung abgelaufen -> naechste Runde neu einloggen
            raise
        dl, ul = info["dl_info_speed"], info["up_info_speed"]
        if not all(isinstance(v, (int, float)) and v >= 0 for v in (dl, ul)):
            raise ValueError
        self.publish({"state": "online", "rx": float(dl), "tx": float(ul)})

    def run(self):
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                self.poll()
            except LoginBackoff:
                pass                    # Fehlergrund ("auth") bleibt stehen, kein Netzwerkzugriff
            except PermissionError:
                self.fail("auth")
            except urllib.error.HTTPError as e:
                self.fail("auth" if e.code == 403 else "http")
            except urllib.error.URLError as e:
                reason = str(getattr(e, "reason", e)).lower()
                self.fail("timeout" if "timed out" in reason else "refused")
            except TimeoutError:
                self.fail("timeout")
            except (OSError, ValueError, KeyError, TypeError):
                self.fail("error")
            self.stop_event.wait(max(0.0, 1.0 - (time.monotonic() - started)))


class StaticFailure(Source):
    """Platzhalter, wenn die Config unbrauchbar ist: Quelle bleibt offline."""
    def __init__(self, reason):
        super().__init__()
        self.error = reason

    def run(self):
        pass


# --------------------------------------------------------------------------
# Hauptprogramm
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--config", default=os.environ.get("HOMESERVER_TRAFFIC_CONFIG", DEFAULT_CONFIG))
    ap.add_argument("--count", type=int, default=0, help="nach N Zeilen beenden (Test)")
    args = ap.parse_args()

    try:
        cfg = load_config(args.config)
        host, qbt = HostSource(cfg), QbtSource(cfg)
    except ConfigError as e:
        print(f"homeserver-traffic: {e}", file=sys.stderr)
        host, qbt = StaticFailure(str(e)), StaticFailure(str(e))

    sources = (host, qbt)

    def shutdown(*_):
        for s in sources:
            s.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    for s in sources:
        s.start()

    emitted = 0
    try:
        while True:
            line = json.dumps({"host": host.snapshot(), "qbt": qbt.snapshot()},
                              separators=(",", ":"))
            print(line, flush=True)
            emitted += 1
            if args.count and emitted >= args.count:
                break
            time.sleep(1.0)
    except (BrokenPipeError, KeyboardInterrupt):
        pass        # Leiste hat uns beendet (z. B. Hot-Reload) -> sauber aufraeumen
    finally:
        for s in sources:
            s.stop()
        try:
            sys.stdout.close()
        except (BrokenPipeError, OSError):
            pass


if __name__ == "__main__":
    main()
