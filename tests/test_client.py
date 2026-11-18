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


if __name__ == "__main__":
    unittest.main()
