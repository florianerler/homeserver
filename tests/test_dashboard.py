import json, os, re, sys, unittest
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "plugin"))
import dashboard

OFF = {"v": 2, "cards": {k: {"ok": False, "state": "offline", "error": "unreachable"}
                          for k in ("net", "qbt", "system", "guests", "ping", "storage", "smart", "speed")}}
ON = {"v": 2, "cards": {
    "net": {"ok": True, "state": "online", "rx": 1500.0, "tx": 2e6},
    "qbt": {"ok": True, "state": "online", "rx": 0.0, "tx": 5e3},
    "system": {"ok": True, "state": "online", "load": [0.5, 0.4, 0.3], "memTotalKb": 8e6, "memAvailKb": 4e6, "tempC": 50.0, "uptime": 99999},
    "guests": {"ok": True, "state": "online", "items": [{"vmid": 1, "type": "lxc", "status": "stopped", "name": "x"}], "total": 1, "running": 0},
    "ping": {"ok": True, "state": "online", "target": "1.1.1.1", "avgMs": 12.0, "lossPct": 0.0, "history": [10.0, 12.0], "warn": False},
    "storage": {"ok": True, "state": "online", "items": [{"name": "local", "type": "dir", "active": True, "pct": 97.0}]},
    "smart": {"ok": True, "state": "online", "items": [{"dev": "sda", "status": "failed", "tempC": 40}], "warn": True},
}}


def lines(snap, cols, rows, fresh=True):
    st = dashboard.State()
    st.update(snap)
    return dashboard.render(snap["cards"], st, cols, rows, "12:00:00", fresh)


class Render(unittest.TestCase):
    def test_fits_every_size(self):
        for snap in (OFF, ON, {"cards": {}}):
            for cols, rows in ((112, 18), (80, 14), (60, 8), (20, 3), (200, 50)):
                out = lines(snap, cols, rows)
                self.assertEqual(len(out), rows)
                for ln in out:
                    self.assertLessEqual(dashboard.vlen(ln), cols, (cols, rows, ln))

    def test_only_theme_colors(self):
        # Keine festen RGB- (38;2 / 48;2) oder 256-Farben (38;5 / 48;5): nur ANSI-Index 30-37.
        text = "\n".join(lines(ON, 112, 18) + lines(OFF, 112, 18))
        self.assertIsNone(re.search(r"\x1b\[[0-9;]*[34]8;", text))

    def test_offline_shows_reason_not_crash(self):
        text = "\n".join(lines(OFF, 112, 18))
        self.assertIn("OFFLINE", text)
        self.assertIn("Host nicht erreichbar", text)      # Text aus i18n/de.json des Plugins

    def test_no_data_is_marked(self):
        self.assertIn("KEINE DATEN", "\n".join(lines(ON, 112, 18, fresh=False)))

    def test_warnings_visible(self):
        text = "\n".join(lines(ON, 112, 18))
        self.assertIn("FEHLER", text)
        self.assertIn("WARNUNG", text)                           # alle online, aber Gast gestoppt -> Warnung statt "alles gruen"

    def test_rate_units(self):
        self.assertEqual(dashboard.rate(0), "0 B/s")
        self.assertEqual(dashboard.rate(1500), "1.50 kB/s")
        self.assertEqual(dashboard.rate(2e6), "2.00 MB/s")

    def test_banner_width(self):
        self.assertIsNotNone(dashboard.banner(80))
        self.assertIsNone(dashboard.banner(40))


if __name__ == "__main__":
    unittest.main()
