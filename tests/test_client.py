import errno, os, socket, ssl, sys, tempfile, time, unittest, urllib.error
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "plugin"))
from hs import base, config, errors, protocol
from hs.agent_source import AgentSource
from hs.qbt_source import LoginBackoff, QbtSource

AGENT = os.path.join(ROOT, "server", "agent.py")


class ComputeRate(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(protocol.compute_rate((100.0, 1000), (101.0, 3000)), 2000.0)

    def test_uses_time_difference_not_one_second(self):
        self.assertEqual(protocol.compute_rate((100.0, 0), (102.0, 3000)), 1500.0)

    def test_counter_reset_returns_none(self):
        self.assertIsNone(protocol.compute_rate((100.0, 5000), (101.0, 100)))

    def test_time_standing_still_or_backwards(self):
        self.assertIsNone(protocol.compute_rate((100.0, 0), (100.0, 10)))
        self.assertIsNone(protocol.compute_rate((100.0, 0), (99.0, 10)))   # Server-Neustart

    def test_large_gap_returns_none(self):
        self.assertIsNone(protocol.compute_rate((100.0, 0), (160.0, 10 ** 9)))

    def test_zero_traffic(self):
        self.assertEqual(protocol.compute_rate((1.0, 50), (2.0, 50)), 0.0)


class ParseLine(unittest.TestCase):
    def test_hello(self):
        self.assertEqual(protocol.parse_line("HELLO homeserver-agent 1\n"), ("HELLO", ("homeserver-agent", 1)))

    def test_hello_wrong_name_or_version(self):
        for bad in ["HELLO other 1", "HELLO homeserver-agent x", "HELLO homeserver-agent"]:
            self.assertIsNone(protocol.parse_line(bad), bad)

    def test_net(self):
        self.assertEqual(protocol.parse_line("NET 482177.70 nic0 10 20\n"), ("NET", (482177.7, "nic0", 10.0, 20.0)))

    def test_net_garbage(self):
        for bad in ["", "Terminated", "NET 1 nic0 2", "NET 1 nic0 x 3", "NET -1 nic0 1 1",
                    "NET nan nic0 1 1", "NET inf nic0 1 1", "NET 1 bad/name 1 1", "NET 1 " + "a" * 20 + " 1 1",
                    "NET 1 nic0 1 1 extra"]:
            self.assertIsNone(protocol.parse_line(bad), bad)

    def test_unknown_type_ignored(self):
        self.assertIsNone(protocol.parse_line("SYS 1 2 3"))

    def test_too_long(self):
        self.assertIsNone(protocol.parse_line("NET 1 nic0 1 " + "9" * 600))

    def test_legacy_detection(self):
        self.assertTrue(protocol.is_legacy_counters("482177.70 1 2 3 4\n"))
        self.assertFalse(protocol.is_legacy_counters("NET 1 nic0 1 1"))
        self.assertFalse(protocol.is_legacy_counters("1 2 3"))


class ErrorClassification(unittest.TestCase):
    def test_os_errors(self):
        f = errors.classify_os_error
        self.assertEqual(f(ConnectionRefusedError()), "refused")
        self.assertEqual(f(OSError(errno.ECONNREFUSED, "x")), "refused")
        self.assertEqual(f(OSError(errno.EHOSTUNREACH, "No route to host")), "unreachable")
        self.assertEqual(f(OSError(errno.ENETUNREACH, "Network is unreachable")), "unreachable")
        self.assertEqual(f(TimeoutError()), "timeout")
        self.assertEqual(f(socket.timeout()), "timeout")
        self.assertEqual(f(OSError(errno.ETIMEDOUT, "x")), "timeout")
        self.assertEqual(f(socket.gaierror(-2, "Name or service not known")), "dns")
        self.assertEqual(f(ssl.SSLCertVerificationError()), "tls")
        self.assertEqual(f("unknown url type"), "error")
        self.assertEqual(f(OSError(errno.EPIPE, "x")), "error")

    def test_ssh_errors(self):
        f = errors.classify_ssh_error
        self.assertEqual(f("Host key verification failed."), "hostkey")
        self.assertEqual(f("traffic@x: Permission denied (publickey)."), "auth")
        self.assertEqual(f("ssh: connect to host x port 22: No route to host"), "unreachable")
        self.assertEqual(f("ssh: connect to host x port 22: Network is unreachable"), "unreachable")
        self.assertEqual(f("ssh: connect to host x port 22: Connection timed out"), "timeout")
        self.assertEqual(f("Timeout, server x not responding."), "timeout")
        self.assertEqual(f("Connection refused"), "refused")
        self.assertEqual(f("???"), "ssh")


def write_conf(text):
    fh = tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False)
    fh.write(text)
    fh.close()
    return fh.name


BASE = ("[ssh]\nhost=h\nuser=u\nkey=k\nknown_hosts=kh\n"
        "[qbittorrent]\nurl={url}\nusername=a\npassword=p%ss%w\n{extra}")


class Config(unittest.TestCase):
    def load(self, url="http://x:1/", extra="", ssh_extra=""):
        path = write_conf(BASE.format(url=url, extra=extra).replace("known_hosts=kh\n", "known_hosts=kh\n" + ssh_extra))
        try:
            return config.load_config(path)
        finally:
            os.unlink(path)

    def test_percent_in_password_survives(self):
        cfg = self.load()
        self.assertEqual(cfg["qb_pass"], "p%ss%w")
        self.assertEqual(cfg["qb_url"], "http://x:1")

    def test_missing_config(self):
        with self.assertRaises(config.ConfigError):
            config.load_config("/nonexistent/config.ini")

    def test_bad_url_scheme(self):
        with self.assertRaises(config.ConfigError) as cm:
            self.load(url="ftp://x")
        self.assertEqual(str(cm.exception), "config-url")

    def test_bad_main_iface(self):
        with self.assertRaises(config.ConfigError) as cm:
            self.load(ssh_extra="main_iface=../etc\n")
        self.assertEqual(str(cm.exception), "config-iface")

    def test_ca_file_must_exist(self):
        with self.assertRaises(config.ConfigError) as cm:
            self.load(url="https://x", extra="ca_file=/nonexistent/ca.pem\n")
        self.assertEqual(str(cm.exception), "config-ca")

    def test_broken_ca_file_is_config_error(self):
        pem = write_conf("kein zertifikat")
        try:
            with self.assertRaises(config.ConfigError) as cm:
                self.load(url="https://x", extra=f"ca_file={pem}\n")
            self.assertEqual(str(cm.exception), "config-ca")
        finally:
            os.unlink(pem)

    def test_tls_context_never_disables_verification(self):
        ctx = config.tls_context("")
        self.assertTrue(ctx.check_hostname)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)


class QbtLoginBackoff(unittest.TestCase):
    def make(self, behaviour):
        src = QbtSource({"qb_url": "http://x:1", "qb_user": "u", "qb_pass": "p", "qb_ca": ""})
        src.calls = []

        def fake(path, data=None):
            src.calls.append(path)
            raise behaviour
        src._request = fake
        return src

    def test_rejected_login_is_not_retried_every_second(self):
        src = self.make(urllib.error.HTTPError("u", 403, "Forbidden", {}, __import__("io").BytesIO()))
        for _ in range(10):
            with self.assertRaises((urllib.error.HTTPError, LoginBackoff)):
                src.poll()
        self.assertEqual(src.calls.count("/api/v2/auth/login"), 1)

    def test_backoff_grows_and_is_capped(self):
        src = self.make(PermissionError())
        waits = []
        for _ in range(8):
            src.next_login = 0.0
            with self.assertRaises(PermissionError):
                src.poll()
            waits.append(src.login_wait)
        self.assertEqual(waits[:3], [60.0, 120.0, 240.0])
        self.assertEqual(waits[-1], QbtSource.BACKOFF_MAX_S)

    def test_network_error_does_not_trigger_backoff(self):
        src = self.make(ConnectionRefusedError())
        for _ in range(3):
            with self.assertRaises(ConnectionRefusedError):
                src.poll()
        self.assertEqual(src.calls.count("/api/v2/auth/login"), 3)
        self.assertEqual(src.login_wait, 0.0)

    def test_https_uses_verifying_handler(self):
        src = QbtSource({"qb_url": "https://x:1", "qb_user": "u", "qb_pass": "p", "qb_ca": ""})
        https = [h for h in src.opener.handlers if h.__class__.__name__ == "HTTPSHandler"]
        self.assertEqual(len(https), 1)


class NoThreadAttributeClash(unittest.TestCase):
    """Source erbt von threading.Thread (hat z. B. _stderr/_stop/_started). Eigene
    Methoden duerfen diese Namen nicht verwenden, sonst stirbt der Thread beim Aufruf."""
    def test_no_own_attribute_shadows_thread_internals(self):
        import threading
        probe = threading.Thread()
        for cls in (AgentSource, QbtSource, base.Source):
            for name in vars(cls):
                if name.startswith("__"):
                    continue
                self.assertFalse(
                    hasattr(probe, name) and name not in ("run", "start", "join", "name", "daemon"),
                    f"{cls.__name__}.{name} kollidiert mit threading.Thread")


class StaleSnapshot(unittest.TestCase):
    def test_stale_without_error_reads_as_timeout(self):
        s = base.Source()
        s.publish({"state": "online", "rx": 1.0, "tx": 1.0})
        s.last_ok -= 100
        snap = s.snapshot()
        self.assertFalse(snap["ok"])
        self.assertEqual(snap["error"], "timeout")


CFG = {"ssh_key": "k", "ssh_known_hosts": "kh", "ssh_user": "u", "ssh_host": "h", "main_iface": "lo"}


class FakeAgentSource(AgentSource):
    """AgentSource mit lokalem Programm statt ssh (gleicher Code, andere Gegenstelle)."""
    def __init__(self, argv, **cfg):
        super().__init__(dict(CFG, **cfg))
        self.argv = argv

    def _command(self):
        return self.argv


def run_until(src, predicate, timeout=8.0):
    src.start()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        snap = src.snapshot()
        if predicate(snap):
            return snap
        time.sleep(0.1)
    return src.snapshot()


class AgentSourceIntegration(unittest.TestCase):
    def test_real_agent_delivers_rates_and_stops_cleanly(self):
        conf = write_conf("[agent]\ninterfaces = lo\n")
        src = FakeAgentSource([sys.executable, "-I", AGENT, "--conf", conf])
        try:
            snap = run_until(src, lambda s: s["ok"])
            self.assertTrue(snap["ok"], snap)
            self.assertEqual(snap["iface"], "lo")
            self.assertIn("lo", snap["ifaces"])
            proc = src.proc
        finally:
            src.stop()
            os.unlink(conf)
        self.assertIsNotNone(proc.poll())           # Agent ist nach stop() beendet

    def test_agent_exits_on_stdin_eof(self):
        import subprocess
        conf = write_conf("[agent]\ninterfaces = lo\n")
        try:
            p = subprocess.Popen([sys.executable, "-I", AGENT, "--conf", conf],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            with p:
                self.assertTrue(p.stdout.readline().startswith("HELLO"))
                p.stdin.close()                     # Client weg (z. B. kill -9)
                self.assertEqual(p.wait(timeout=3), 0)
        finally:
            os.unlink(conf)

    def test_legacy_agent_is_reported(self):
        src = FakeAgentSource([sys.executable, "-c", "print('482177.70 1 2 3 4', flush=True)"])
        try:
            snap = run_until(src, lambda s: s.get("error") == "agent-outdated", 5)
        finally:
            src.stop()
        self.assertEqual(snap["error"], "agent-outdated")

    def test_wrong_protocol_version_is_reported(self):
        src = FakeAgentSource([sys.executable, "-c", "print('HELLO homeserver-agent 99', flush=True)"])
        try:
            snap = run_until(src, lambda s: s.get("error") == "agent-protocol", 5)
        finally:
            src.stop()
        self.assertEqual(snap["error"], "agent-protocol")

    def test_missing_main_interface_is_reported(self):
        code = ("import time\nprint('HELLO homeserver-agent 1', flush=True)\n"
                "for i in range(1, 8):\n    print(f'NET {i}.0 other {i*10} {i*10}', flush=True)\n    time.sleep(0.05)\n")
        src = FakeAgentSource([sys.executable, "-c", code])
        try:
            snap = run_until(src, lambda s: s.get("error") == "iface-missing", 5)
        finally:
            src.stop()
        self.assertEqual(snap["error"], "iface-missing")

    def test_send_validates_and_needs_connection(self):
        src = FakeAgentSource(["true"])
        self.assertFalse(src.send("speedtest"))                  # nicht verbunden
        self.assertFalse(src.send("rm -rf /\x00"))
        self.assertFalse(src.send("a" * 200))


def fake_stream(*lines, loops=40):
    """Python-Code fuer ein Fake-Programm, das HELLO und dann die Zeilen wiederholt ausgibt."""
    return ("import time\nprint('HELLO homeserver-agent 1', flush=True)\n"
            f"L = {list(lines)!r}\nfor i in range({loops}):\n"
            "    for l in L: print(l.replace('@', str(i + 1)), flush=True)\n    time.sleep(0.05)\n")


GOOD_SYS = "SYS @.0 8 0.50 0.40 0.30 16000000 4000000 51.5"
GOOD_GUESTS = ["BEGIN GUESTS 7", "G 100 lxc running pihole", "G 200 qemu stopped win11", "END GUESTS"]


class ParseNewLines(unittest.TestCase):
    def test_sys(self):
        kind, d = protocol.parse_line("SYS 10.5 8 0.5 0.4 0.3 16000000 4000000 51.5")
        self.assertEqual(kind, "SYS")
        self.assertEqual(d["load"], (0.5, 0.4, 0.3)); self.assertEqual(d["temp_c"], 51.5)
        self.assertIsNone(protocol.parse_line("SYS 10.5 8 0.5 0.4 0.3 16000000 4000000 -")[1]["temp_c"])

    def test_sys_rejects_garbage(self):
        for bad in ["SYS 1 8 0.5 0.4 0.3 100 200 50",          # frei > gesamt
                    "SYS 1 0 0.5 0.4 0.3 100 50 50", "SYS 1 8 x 0.4 0.3 100 50 50",
                    "SYS 1 8 0.5 0.4 0.3 100 50 999", "SYS 1 8 0.5 0.4 0.3 100 50", "SYS 1 8 -1 0.4 0.3 100 50 50"]:
            self.assertIsNone(protocol.parse_line(bad), bad)

    def test_guest_lines(self):
        self.assertEqual(protocol.parse_line("G 100 lxc running pihole"), ("G", (100, "lxc", "running", "pihole")))
        self.assertEqual(protocol.parse_line("BEGIN GUESTS 7"), ("BEGIN", ("GUESTS", 7)))
        self.assertEqual(protocol.parse_line("END GUESTS"), ("END", "GUESTS"))
        self.assertEqual(protocol.parse_line("MISSING GUESTS"), ("MISSING", "GUESTS"))
        for bad in ["G 0 lxc running x", "G 1 evil running x", "G 1 lxc bogus x", "G 1 lxc running a;b",
                    "G 1 lxc running", "BEGIN GUESTS x", "BEGIN OTHER 1", "END X"]:
            self.assertIsNone(protocol.parse_line(bad), bad)


class PingCardTests(unittest.TestCase):
    def ping_snapshot(self, lines, until, loops=30):
        src = FakeAgentSource([sys.executable, "-c", fake_stream("NET @.0 lo @0 @0", *lines, loops=loops)])
        try:
            src.start()
            end = time.monotonic() + 6
            while time.monotonic() < end:
                snap = src.snapshots()["ping"]
                if until(snap):
                    return snap
                time.sleep(0.1)
            return src.snapshots()["ping"]
        finally:
            src.stop()

    def test_parse(self):
        self.assertEqual(protocol.parse_line("PING 5.0 1.1.1.1 3 2 12.5")[1]["recv"], 2)
        for bad in ["PING 5.0 1.1.1.1 3 4 1", "PING 5.0 evil 3 3 1", "PING 5.0 1.1.1.1 3 0 5", "PING 5.0 1.1.1.1 3 3 -",
                    "PING 5.0 1.1.1.1 0 0 -", "PINGERR kaputt", "PING 5.0 1.1.1.1 3 3"]:
            self.assertIsNone(protocol.parse_line(bad), bad)

    def test_good_latency_no_warning(self):
        snap = self.ping_snapshot(["PING @.0 1.1.1.1 3 3 12.00"], lambda s: s["ok"])
        self.assertTrue(snap["ok"], snap)
        self.assertEqual(snap["avgMs"], 12.0); self.assertEqual(snap["lossPct"], 0.0)
        self.assertFalse(snap["warn"])

    def test_loss_window_and_warning(self):
        snap = self.ping_snapshot(["PING @.0 1.1.1.1 3 2 12.00"], lambda s: s["ok"] and len(s["history"]) >= 3)
        self.assertAlmostEqual(snap["lossPct"], 33.3, places=1)
        self.assertTrue(snap["warn"])

    def test_high_latency_warns(self):
        snap = self.ping_snapshot(["PING @.0 1.1.1.1 3 3 150.00"], lambda s: s["ok"])
        self.assertTrue(snap["warn"])

    def test_everything_lost_warns_and_keeps_gap(self):
        snap = self.ping_snapshot(["PING @.0 1.1.1.1 3 0 -"], lambda s: s["ok"] and len(s["history"]) >= 2)
        self.assertIsNone(snap["avgMs"]); self.assertEqual(snap["lossPct"], 100.0)
        self.assertTrue(snap["warn"]); self.assertEqual(snap["history"][-1], None)

    def test_ping_error_stays_local(self):
        src = FakeAgentSource([sys.executable, "-c", fake_stream("NET @.0 lo @0 @0", "PINGERR unavailable")])
        try:
            src.start()
            end = time.monotonic() + 6
            while time.monotonic() < end and src.snapshots()["ping"].get("error") != "ping-unavailable":
                time.sleep(0.1)
            snaps = src.snapshots()
        finally:
            src.stop()
        self.assertEqual(snaps["ping"]["error"], "ping-unavailable")
        self.assertFalse(snaps["ping"]["ok"]); self.assertTrue(snaps["net"]["ok"])


class MultiCardIntegration(unittest.TestCase):
    def run_fake(self, *lines, until, loops=40):
        src = FakeAgentSource([sys.executable, "-c", fake_stream(*lines, loops=loops)])
        try:
            end = time.monotonic() + 6
            src.start()
            while time.monotonic() < end:
                snaps = src.snapshots()
                if until(snaps):
                    return snaps
                time.sleep(0.1)
            return src.snapshots()
        finally:
            src.stop()

    def test_system_and_guests_cards(self):
        snaps = self.run_fake("NET @.0 lo @0 @0", GOOD_SYS, *GOOD_GUESTS,
                              until=lambda s: s["system"]["ok"] and s["guests"]["ok"])
        self.assertTrue(snaps["net"]["ok"], snaps["net"])
        self.assertEqual(snaps["system"]["ncpu"], 8); self.assertEqual(snaps["system"]["tempC"], 51.5)
        g = snaps["guests"]
        self.assertEqual((g["running"], g["total"]), (1, 2))
        self.assertEqual(g["items"][1]["name"], "win11")

    def test_collector_missing_marks_only_guests_offline(self):
        snaps = self.run_fake("NET @.0 lo @0 @0", GOOD_SYS, "MISSING GUESTS",
                              until=lambda s: s["guests"].get("error") == "collector-offline" and s["system"]["ok"])
        self.assertEqual(snaps["guests"]["error"], "collector-offline")
        self.assertFalse(snaps["guests"]["ok"])
        self.assertTrue(snaps["system"]["ok"] and snaps["net"]["ok"])      # Fehler bleibt lokal

    def test_old_collector_file_is_offline(self):
        snaps = self.run_fake("NET @.0 lo @0 @0", "BEGIN GUESTS 9999", "G 1 lxc running a", "END GUESTS",
                              until=lambda s: s["guests"].get("error") == "collector-offline")
        self.assertEqual(snaps["guests"]["error"], "collector-offline")

    def test_incomplete_block_is_not_published(self):
        snaps = self.run_fake("NET @.0 lo @0 @0", "BEGIN GUESTS 1", "G 1 lxc running a",
                              until=lambda s: False, loops=15)
        self.assertFalse(snaps["guests"]["ok"])

    def test_connection_loss_takes_all_cards_offline(self):
        src = FakeAgentSource([sys.executable, "-c", fake_stream("NET @.0 lo @0 @0", GOOD_SYS, *GOOD_GUESTS, loops=6)])
        try:
            src.start()
            end = time.monotonic() + 6
            while time.monotonic() < end and not src.snapshots()["system"]["ok"]:
                time.sleep(0.05)
            self.assertTrue(src.snapshots()["system"]["ok"])
            end = time.monotonic() + 6
            while time.monotonic() < end and src.snapshots()["system"]["ok"]:     # Fake-Agent endet nach 6 Takten
                time.sleep(0.1)
            snaps = src.snapshots()
        finally:
            src.stop()
        self.assertFalse(snaps["system"]["ok"]); self.assertFalse(snaps["guests"]["ok"])
        self.assertNotEqual(snaps["guests"]["state"], "online")


class SnapshotShape(unittest.TestCase):
    def test_every_source_has_a_proper_card_name(self):
        import re
        cfg = dict(CFG, qb_url="http://127.0.0.1:1", qb_user="u", qb_pass="p", qb_ca="")
        names = []
        for src in (AgentSource(CFG), QbtSource(cfg)):
            names += list(src.snapshots())
        self.assertEqual(sorted(names), ["guests", "net", "ping", "qbt", "system"])
        self.assertTrue(all(re.match(r"^[a-z]{1,16}$", n) for n in names))

    def test_static_failure_covers_all_cards(self):
        names = ("net", "system", "guests")
        snaps = base.StaticFailure("config-missing", names).snapshots()
        self.assertEqual(sorted(snaps), sorted(names))
        self.assertEqual(snaps["net"]["error"], "config-missing")


if __name__ == "__main__":
    unittest.main()
