import os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin"))
import homeserver_traffic as ht


class ComputeRate(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(ht.compute_rate((100.0, 1000), (101.0, 3000)), 2000.0)

    def test_uses_time_difference_not_one_second(self):
        self.assertEqual(ht.compute_rate((100.0, 0), (102.0, 3000)), 1500.0)

    def test_counter_reset_returns_none(self):
        self.assertIsNone(ht.compute_rate((100.0, 5000), (101.0, 100)))

    def test_time_standing_still_or_backwards(self):
        self.assertIsNone(ht.compute_rate((100.0, 0), (100.0, 10)))
        self.assertIsNone(ht.compute_rate((100.0, 0), (99.0, 10)))   # Server-Reboot

    def test_large_gap_returns_none(self):
        self.assertIsNone(ht.compute_rate((100.0, 0), (160.0, 10 ** 9)))

    def test_zero_traffic(self):
        self.assertEqual(ht.compute_rate((1.0, 50), (2.0, 50)), 0.0)


class ParseCounters(unittest.TestCase):
    def test_valid(self):
        up, c = ht.parse_counters("482177.70 10 20 30 40\n")
        self.assertEqual(up, 482177.70)
        self.assertEqual(c, {"nic0": (10.0, 20.0), "vmbr0": (30.0, 40.0)})

    def test_garbage_rejected(self):
        for bad in ["", "Terminated", "1 2 3", "1 2 3 4 x", "1 2 3 4 5 6",
                    "1 -2 3 4 5", "nan 1 2 3 4", "inf 1 2 3 4"]:
            self.assertIsNone(ht.parse_counters(bad), bad)


class SshErrors(unittest.TestCase):
    def test_classify(self):
        f = ht.classify_ssh_error
        self.assertEqual(f("Host key verification failed.", 255), "hostkey")
        self.assertEqual(f("traffic@x: Permission denied (publickey).", 255), "auth")
        self.assertEqual(f("ssh: connect to host x port 22: Connection timed out", 255), "timeout")
        self.assertEqual(f("Timeout, server x not responding.", 255), "timeout")
        self.assertEqual(f("Connection refused", 255), "refused")
        self.assertEqual(f("???", 1), "ssh")


class QbtLoginBackoff(unittest.TestCase):
    def make(self, behaviour):
        cfg = {"qb_url": "http://x:1", "qb_user": "u", "qb_pass": "p"}
        src = ht.QbtSource(cfg)
        src.calls = []

        def fake(path, data=None):
            src.calls.append(path)
            raise behaviour
        src._request = fake
        return src

    def test_rejected_login_is_not_retried_every_second(self):
        import urllib.error
        src = self.make(urllib.error.HTTPError("u", 403, "Forbidden", {}, None))
        for _ in range(10):                       # 10 Sekunden-Runden
            with self.assertRaises((urllib.error.HTTPError, ht.LoginBackoff)):
                src.poll()
        self.assertEqual(src.calls.count("/api/v2/auth/login"), 1)   # nur EIN Versuch

    def test_backoff_grows_and_is_capped(self):
        src = self.make(PermissionError())
        waits = []
        for _ in range(8):
            src.next_login = 0.0                  # Wartezeit gilt als abgelaufen
            with self.assertRaises(PermissionError):
                src.poll()
            waits.append(src.login_wait)
        self.assertEqual(waits[:3], [60.0, 120.0, 240.0])
        self.assertEqual(waits[-1], ht.QbtSource.BACKOFF_MAX_S)

    def test_network_error_does_not_trigger_backoff(self):
        src = self.make(ConnectionRefusedError())
        for _ in range(3):
            with self.assertRaises(ConnectionRefusedError):
                src.poll()
        self.assertEqual(src.calls.count("/api/v2/auth/login"), 3)   # darf schnell neu versuchen
        self.assertEqual(src.login_wait, 0.0)


class StaleSnapshot(unittest.TestCase):
    def test_stale_without_error_reads_as_timeout(self):
        s = ht.Source()
        s.publish({"state": "online", "rx": 1.0, "tx": 1.0})
        s.last_ok -= 100                          # lange keine Messung
        snap = s.snapshot()
        self.assertFalse(snap["ok"])
        self.assertEqual(snap["error"], "timeout")


class Config(unittest.TestCase):
    def test_percent_in_password_survives(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False) as fh:
            fh.write("[ssh]\nhost=h\nuser=u\nkey=k\nknown_hosts=kh\n"
                     "[qbittorrent]\nurl=http://x:1/\nusername=a\npassword=p%ss%w\n")
        try:
            cfg = ht.load_config(fh.name)
            self.assertEqual(cfg["qb_pass"], "p%ss%w")
            self.assertEqual(cfg["qb_url"], "http://x:1")
        finally:
            os.unlink(fh.name)

    def test_missing_config(self):
        with self.assertRaises(ht.ConfigError):
            ht.load_config("/nonexistent/config.ini")


if __name__ == "__main__":
    unittest.main()
