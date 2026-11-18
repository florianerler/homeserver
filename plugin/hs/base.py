"""Gemeinsame Basis einer Datenquelle (laeuft in einem eigenen Thread)."""
import threading
import time

STALE_S = 4.0       # laenger ohne Messwert -> Quelle gilt als offline


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


class StaticFailure(Source):
    """Platzhalter, wenn die Config unbrauchbar ist: Quelle bleibt offline."""
    def __init__(self, reason):
        super().__init__()
        self.error = reason

    def run(self):
        pass
