"""qBittorrent: WebUI-API /api/v2/transfer/info (fertige Live-Raten)."""
import http.cookiejar
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config as cfgmod
from . import errors
from .base import Source


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
        self.logged_in = False
        handlers = [urllib.request.ProxyHandler({}),            # nie ueber einen Proxy
                    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())]
        if cfg["qb_url"].startswith("https://"):
            handlers.append(urllib.request.HTTPSHandler(context=cfgmod.tls_context(cfg["qb_ca"])))
        self.opener = urllib.request.build_opener(*handlers)

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
                self.fail("auth" if e.code in (401, 403) else "http")
            except urllib.error.URLError as e:
                self.fail(errors.classify_os_error(e.reason))
            except (OSError, ValueError, KeyError, TypeError) as e:
                self.fail(errors.classify_os_error(e) if isinstance(e, OSError) else "error")
            self.stop_event.wait(max(0.0, 1.0 - (time.monotonic() - started)))
