"""Gemeinsame Basis: Zustand einer Karte und Datenquelle (Thread)."""
import threading
import time

STALE_S = 4.0       # laenger ohne Messwert -> Karte gilt als offline


class CardState:
    """Zustand einer Karte im Popup: letzter Wert, Zeitpunkt, Fehlergrund."""

    def __init__(self, stale_s=STALE_S):
        self.lock = threading.Lock()
        self.stale_s = stale_s
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

    def invalidate(self):
        """Wert sofort verwerfen (z. B. Verbindung weg): Karte wird offline gemeldet."""
        with self.lock:
            self.last_ok = None

    def snapshot(self):
        with self.lock:
            fresh = self.last_ok is not None and time.monotonic() - self.last_ok < self.stale_s
            out = {"ok": fresh}
            if fresh:
                out.update(self.data)
            else:
                out["state"] = "connecting" if self.error == "connecting" else "offline"
                out["error"] = self.error or "timeout"
            return out


class Source(CardState, threading.Thread):
    """Datenquelle in eigenem Thread. Ist selbst die Hauptkarte (card_name); Unterklassen
    koennen ueber snapshots() weitere Karten liefern."""
    card_name = "?"

    def __init__(self, stale_s=STALE_S):
        CardState.__init__(self, stale_s)
        threading.Thread.__init__(self, daemon=True)
        self.stop_event = threading.Event()

    def snapshots(self):
        return {self.card_name: self.snapshot()}

    def stop(self):
        self.stop_event.set()


class StaticFailure(Source):
    """Platzhalter, wenn die Config unbrauchbar ist: Karten bleiben offline mit Grund."""

    def __init__(self, reason, names):
        super().__init__()
        self.error = reason
        self.names = tuple(names)

    def snapshots(self):
        return {n: self.snapshot() for n in self.names}

    def run(self):
        pass
