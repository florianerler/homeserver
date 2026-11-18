import io, json, os, sys, tempfile, threading, time, unittest
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "server"))
import agent


def fake_sys_net(tmp, ifaces, physical=()):
    for name, (rx, tx) in ifaces.items():
        d = os.path.join(tmp, name, "statistics")
        os.makedirs(d)
        with open(os.path.join(d, "rx_bytes"), "w") as fh:
            fh.write(f"{rx}\n")
        with open(os.path.join(d, "tx_bytes"), "w") as fh:
            fh.write(f"{tx}\n")
        if name in physical:
            os.makedirs(os.path.join(tmp, name, "device"))


def conf_file(text):
    fh = tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False)
    fh.write(text)
    fh.close()
    return fh.name


class Interfaces(unittest.TestCase):
    def test_validation_dedup_and_limit(self):
        path = conf_file("[agent]\ninterfaces = nic0 vmbr0 nic0 bad/name ../etc " + " ".join(f"if{i}" for i in range(20)) + "\n")
        try:
            got = agent.load_interfaces(path)
        finally:
            os.unlink(path)
        self.assertEqual(got[:2], ["nic0", "vmbr0"])
        self.assertNotIn("bad/name", got)
        self.assertNotIn("../etc", got)
        self.assertEqual(len(got), agent.MAX_INTERFACES)
        self.assertEqual(len(set(got)), len(got))

    def test_fallback_physical_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_sys_net(tmp, {"nic0": (1, 1), "veth1": (2, 2), "lo": (3, 3)}, physical=("nic0",))
            self.assertEqual(agent.load_interfaces("/nonexistent.conf", sys_net=tmp), ["nic0"])


class NetLines(unittest.TestCase):
    def test_lines_and_missing_interface_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_sys_net(tmp, {"nic0": (100, 200)})
            self.assertEqual(agent.net_lines(["nic0", "gone"], 12.345, sys_net=tmp), ["NET 12.35 nic0 100 200"])

    def test_unreadable_counter_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_sys_net(tmp, {"nic0": (1, 2)})
            with open(os.path.join(tmp, "nic0", "statistics", "rx_bytes"), "w") as fh:
                fh.write("müll\n")
            self.assertEqual(agent.net_lines(["nic0"], 1.0, sys_net=tmp), [])


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(text)


def fake_hwmon(tmp, sensors):
    """sensors: {hwmonN: (name, {tempN: (milligrad, label|None)})}"""
    for hw, (name, temps) in sensors.items():
        write(os.path.join(tmp, hw, "name"), name + "\n")
        for t, (milli, label) in temps.items():
            write(os.path.join(tmp, hw, f"{t}_input"), f"{milli}\n")
            if label:
                write(os.path.join(tmp, hw, f"{t}_label"), label + "\n")


class Temperature(unittest.TestCase):
    def test_prefers_package_over_cores_regardless_of_hwmon_number(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_hwmon(tmp, {"hwmon0": ("acpitz", {"temp1": (99000, None)}),
                             "hwmon7": ("coretemp", {"temp1": (54000, "Package id 0"),
                                                     "temp2": (61000, "Core 0")})})
            self.assertEqual(agent.read_temperature(tmp), 54.0)

    def test_falls_back_to_maximum_without_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_hwmon(tmp, {"hwmon1": ("k10temp", {"temp1": (40500, None), "temp2": (47200, None)})})
            self.assertEqual(agent.read_temperature(tmp), 47.2)

    def test_no_cpu_sensor_gives_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_hwmon(tmp, {"hwmon0": ("nvme", {"temp1": (30000, None)})})
            self.assertIsNone(agent.read_temperature(tmp))
            self.assertIsNone(agent.read_temperature(os.path.join(tmp, "gibt-es-nicht")))

    def test_garbage_values_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_hwmon(tmp, {"hwmon0": ("coretemp", {"temp1": (999999999, "Package id 0")})})
            self.assertIsNone(agent.read_temperature(tmp))


class SysLine(unittest.TestCase):
    def files(self, tmp, load="0.42 0.38 0.30 1/200 999\n", mem="MemTotal: 16000000 kB\nMemFree: 1 kB\nMemAvailable: 8000000 kB\n"):
        write(os.path.join(tmp, "loadavg"), load)
        write(os.path.join(tmp, "meminfo"), mem)
        return os.path.join(tmp, "loadavg"), os.path.join(tmp, "meminfo")

    def test_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            la, mi = self.files(tmp)
            fake_hwmon(tmp, {"hwmon1": ("coretemp", {"temp1": (52000, "Package id 0")})})
            line = agent.sys_line(485686.94, la, mi, tmp)
        parts = line.split()
        self.assertEqual(parts[0], "SYS")
        self.assertEqual(parts[1], "485686.94")
        self.assertEqual(parts[3:6], ["0.42", "0.38", "0.30"])
        self.assertEqual(parts[6:9], ["16000000", "8000000", "52.0"])

    def test_missing_temperature_is_dash(self):
        with tempfile.TemporaryDirectory() as tmp:
            la, mi = self.files(tmp)
            self.assertTrue(agent.sys_line(1.0, la, mi, tmp).endswith(" -"))

    def test_unreadable_sources_give_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            la, mi = self.files(tmp, load="müll\n")
            self.assertIsNone(agent.sys_line(1.0, la, mi, tmp))
            la, mi = self.files(tmp, mem="MemTotal: 1 kB\n")     # MemAvailable fehlt
            self.assertIsNone(agent.sys_line(1.0, la, mi, tmp))


class Guests(unittest.TestCase):
    def file(self, tmp, obj, raw=None):
        path = os.path.join(tmp, "guests.json")
        write(path, raw if raw is not None else json.dumps(obj))
        return path

    def test_ok_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.file(tmp, {"ts": 1000, "guests": [
                {"vmid": 100, "type": "lxc", "status": "running", "name": "pihole"},
                {"vmid": 200, "type": "qemu", "status": "stopped", "name": "win 11; $(reboot)"}]})
            res = agent.read_guests(path, now=1012)
        self.assertEqual(res[0], "ok")
        self.assertEqual(res[1], 12)
        lines = agent.guests_lines(res)
        self.assertEqual(lines[0], "BEGIN GUESTS 12")
        self.assertEqual(lines[1], "G 100 lxc running pihole")
        self.assertRegex(lines[2], r"^G 200 qemu stopped [A-Za-z0-9._-]+$")
        self.assertEqual(lines[-1], "END GUESTS")

    def test_missing_or_broken_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(agent.read_guests(os.path.join(tmp, "nope")), ("missing",))
            for raw in ["", "kein json", "[1,2]", '{"ts":"x","guests":[]}', '{"ts":1}', '{"ts":1,"guests":"x"}']:
                self.assertEqual(agent.read_guests(self.file(tmp, None, raw)), ("missing",), raw)
        self.assertEqual(agent.guests_lines(("missing",)), ["MISSING GUESTS"])

    def test_oversized_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.file(tmp, None, '{"ts":1,"guests":[]}' + " " * (agent.MAX_FILE_BYTES + 10))
            self.assertEqual(agent.read_guests(path), ("missing",))

    def test_bad_entries_skipped_and_count_limited(self):
        with tempfile.TemporaryDirectory() as tmp:
            guests = [{"vmid": i, "type": "lxc", "status": "running", "name": "x"} for i in range(1, 200)]
            guests += [{"vmid": -5, "type": "lxc", "status": "running"}, {"type": "lxc"}, {"vmid": 1, "type": "evil"}, "x"]
            res = agent.read_guests(self.file(tmp, {"ts": 1, "guests": guests}), now=1)
        self.assertEqual(len(res[2]), agent.MAX_GUESTS)

    def test_future_timestamp_gives_age_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = agent.read_guests(self.file(tmp, {"ts": 5000, "guests": []}), now=1000)
        self.assertEqual(res[1], 0)


class StorageSmart(unittest.TestCase):
    def file(self, tmp, obj, name="f.json"):
        path = os.path.join(tmp, name)
        write(path, json.dumps(obj))
        return path

    def test_storage_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.file(tmp, {"ts": 100, "storages": [
                {"name": "local", "type": "dir", "status": "active", "total_kb": 1000, "used_kb": 100},
                {"name": "bad name", "type": "dir", "status": "active", "total_kb": 1, "used_kb": 1},
                {"name": "neg", "type": "dir", "status": "active", "total_kb": -1, "used_kb": 1},
                {"name": "off", "type": "pbs", "status": "weird", "total_kb": 0, "used_kb": 0}, "x"]})
            res = agent.read_storage(path, now=110)
        self.assertEqual(agent.storage_lines(res), ["BEGIN STORAGE 10", "S local dir active 1000 100",
                                                    "S off pbs inactive 0 0", "END STORAGE"])

    def test_smart_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.file(tmp, {"ts": 100, "disks": [
                {"dev": "sda", "status": "passed", "temp": 34}, {"dev": "nvme0", "status": "failed", "temp": None},
                {"dev": "../x", "status": "passed", "temp": 1}, {"dev": "sdb", "status": "bogus", "temp": 9999}]})
            res = agent.read_smart(path, now=100)
        self.assertEqual(agent.smart_lines(res), ["BEGIN SMART 0", "D sda passed 34", "D nvme0 failed -",
                                                  "D sdb unknown -", "END SMART"])

    def test_missing(self):
        self.assertEqual(agent.storage_lines(agent.read_storage("/nicht/da")), ["MISSING STORAGE"])
        self.assertEqual(agent.smart_lines(agent.read_smart("/nicht/da")), ["MISSING SMART"])
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "x.json")
            write(p, "{kaputt")
            self.assertEqual(agent.read_smart(p), ("missing",))


class Schedule(unittest.TestCase):
    def test_slow_data_is_sent_less_often(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_sys_net(tmp, {"nic0": (1, 2)})
            orig = agent.net_lines.__defaults__
            agent.net_lines.__defaults__ = (tmp,)
            try:
                kinds = []
                for tick in range(11):
                    kinds.append([l.split()[0] for l in agent.tick_lines(["nic0"], tick, sys_every=5, guests_every=10)])
            finally:
                agent.net_lines.__defaults__ = orig
        self.assertTrue(all("NET" in k for k in kinds))                     # jede Sekunde
        self.assertEqual([i for i, k in enumerate(kinds) if "SYS" in k], [0, 5, 10])
        self.assertEqual([i for i, k in enumerate(kinds) if "BEGIN" in k or "MISSING" in k], [0, 10])


GOOD_PING = """PING 8.8.8.8 (8.8.8.8) 56(84) bytes of data.
64 bytes from 8.8.8.8: icmp_seq=1 ttl=117 time=12.1 ms

--- 8.8.8.8 ping statistics ---
3 packets transmitted, 3 received, 0% packet loss, time 602ms
rtt min/avg/max/mdev = 11.000/12.345/14.000/1.200 ms
"""
LOST_PING = """--- 1.1.1.1 ping statistics ---
3 packets transmitted, 0 received, 100% packet loss, time 2050ms
"""


class PingParsing(unittest.TestCase):
    def test_good_and_lost(self):
        self.assertEqual(agent.parse_ping(GOOD_PING), (3, 3, 12.345))
        self.assertEqual(agent.parse_ping(LOST_PING), (3, 0, None))
        self.assertEqual(agent.ping_line(5.0, "1.1.1.1", (3, 2, 12.0)), "PING 5.00 1.1.1.1 3 2 12.00")
        self.assertEqual(agent.ping_line(5.0, "1.1.1.1", (3, 0, None)), "PING 5.00 1.1.1.1 3 0 -")

    def test_garbage(self):
        for bad in ["", "ping: socket: Operation not permitted", "3 packets transmitted, 9 received",
                    "0 packets transmitted, 0 received"]:
            self.assertIsNone(agent.parse_ping(bad), bad)

    def test_target_only_ip_literals(self):
        with tempfile.TemporaryDirectory() as tmp:
            def conf(v):
                p = os.path.join(tmp, "c.ini")
                write(p, f"[agent]\nping_target = {v}\n")
                return agent.load_ping_target(p)
            self.assertEqual(conf("9.9.9.9"), "9.9.9.9")
            self.assertEqual(conf("2606:4700:4700::1111"), "2606:4700:4700::1111")
            self.assertIsNone(conf(""))                                  # aus
            self.assertIsNone(conf("example.com"))                       # kein DNS
            self.assertIsNone(conf("1.1.1.1; reboot"))
            self.assertIsNone(conf("-f"))                                # keine Option einschleusen
            self.assertEqual(agent.load_ping_target(os.path.join(tmp, "fehlt")), "1.1.1.1")   # Standard


class FakeProc:
    def __init__(self, text, rc=0, finish_after=1):
        self.text, self.rc, self.polls, self.finish_after, self.killed = text, rc, 0, finish_after, False

    def poll(self):
        self.polls += 1
        return self.rc if self.polls > self.finish_after else None

    def communicate(self, timeout=None):
        return (self.text, None)

    def kill(self):
        self.killed = True
        self.finish_after = 0


class PingerTests(unittest.TestCase):
    def pinger(self, proc, **kw):
        calls = []

        def popen(argv, **kwargs):
            calls.append((argv, kwargs))
            if isinstance(proc, Exception):
                raise proc
            return proc
        p = agent.Pinger("1.1.1.1", popen=popen, path="/usr/bin/ping", **kw)
        return p, calls

    def test_fixed_argv_no_shell(self):
        p, calls = self.pinger(FakeProc(GOOD_PING))
        p.step(100.0, 1.0)
        argv, kwargs = calls[0]
        self.assertEqual(argv, ["/usr/bin/ping", "-n", "-c", "3", "-i", "0.3", "-W", "1", "1.1.1.1"])
        self.assertNotIn("shell", kwargs)
        self.assertEqual(kwargs["env"]["LANG"], "C")

    def test_result_line_and_interval(self):
        p, calls = self.pinger(FakeProc(GOOD_PING))
        self.assertIsNone(p.step(100.0, 7.0))                        # gestartet
        self.assertIsNone(p.step(100.1, 7.1))                        # laeuft noch
        self.assertEqual(p.step(100.2, 7.2), "PING 7.20 1.1.1.1 3 3 12.35")
        self.assertIsNone(p.step(102.0, 9.0))                        # Abstand noch nicht erreicht
        self.assertEqual(len(calls), 1)
        p.step(105.1, 12.0)
        self.assertEqual(len(calls), 2)                              # naechste Messung nach 5 s

    def test_timeout_kills(self):
        proc = FakeProc("", finish_after=10 ** 6)
        p, _ = self.pinger(proc, timeout=8.0)
        p.step(100.0, 1.0)
        self.assertIsNone(p.step(105.0, 2.0))
        self.assertEqual(p.step(109.0, 3.0), "PINGERR failed")
        self.assertTrue(proc.killed)
        self.assertFalse(p.busy)

    def test_errors(self):
        p, _ = self.pinger(OSError("kein ping"))
        self.assertEqual(p.step(100.0, 1.0), "PINGERR unavailable")
        p, _ = self.pinger(FakeProc("ping: socket: Operation not permitted\n", rc=2, finish_after=0))
        p.step(100.0, 1.0)
        self.assertEqual(p.step(100.1, 1.1), "PINGERR unavailable")
        p, _ = self.pinger(FakeProc("irgendwas\n", rc=2, finish_after=0))
        p.step(100.0, 1.0)
        self.assertEqual(p.step(100.1, 1.1), "PINGERR failed")
        p = agent.Pinger("1.1.1.1", path=None)
        p.path = None
        self.assertEqual(p.step(0.0, 1.0), "PINGERR unavailable")

    def test_disabled_target_is_silent(self):
        self.assertIsNone(agent.Pinger(None).step(100.0, 1.0))

    def test_run_loop_emits_ping_and_cleans_up(self):
        proc = FakeProc(GOOD_PING)
        p, _ = self.pinger(proc)
        r, w = os.pipe()
        out = io.StringIO()
        t = threading.Thread(target=lambda: (time.sleep(1.2), os.close(w)))
        t.start()
        try:
            self.assertEqual(agent.run([], out, r, interval=0.2, pinger=p), 0)
        finally:
            t.join()
            os.close(r)
        self.assertRegex(out.getvalue(), r"PING \d+\.\d+ 1\.1\.1\.1 3 3 12\.35")


class Commands(unittest.TestCase):
    def test_everything_denied_without_whitelist(self):
        self.assertEqual(agent.handle_command("speedtest"), "CMDRESULT speedtest denied")
        self.assertEqual(agent.handle_command("rm -rf /"), "CMDRESULT rm denied")

    def test_hostile_name_is_not_echoed(self):
        self.assertEqual(agent.handle_command("$(reboot)"), "CMDRESULT ? denied")
        self.assertEqual(agent.handle_command(""), "CMDRESULT ? denied")

    def test_whitelisted_command_runs_but_arguments_must_be_tokens(self):
        calls = []
        agent.COMMANDS["demo"] = lambda args: (calls.append(args), "ok")[1]
        try:
            self.assertEqual(agent.handle_command("demo alias-1"), "CMDRESULT demo ok")
            self.assertEqual(calls, [["alias-1"]])
            self.assertEqual(agent.handle_command("demo ; reboot"), "CMDRESULT demo denied")
            self.assertEqual(agent.handle_command("demo $(id)"), "CMDRESULT demo denied")
            self.assertEqual(len(calls), 1)
        finally:
            del agent.COMMANDS["demo"]


class RunLoop(unittest.TestCase):
    def test_once_prints_hello_and_net(self):
        r, w = os.pipe()
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            fake_sys_net(tmp, {"nic0": (5, 6)})
            orig = agent.SYS_NET
            agent.net_lines.__defaults__ = (tmp,)
            try:
                self.assertEqual(agent.run(["nic0"], out, r, once=True), 0)
            finally:
                agent.net_lines.__defaults__ = (orig,)
        os.close(r)
        os.close(w)
        lines = out.getvalue().splitlines()
        self.assertEqual(lines[0], "HELLO homeserver-agent 1")
        self.assertRegex(lines[1], r"^NET \d+\.\d\d nic0 5 6$")

    def test_eof_ends_loop_and_commands_are_answered(self):
        r, w = os.pipe()
        out = io.StringIO()
        os.write(w, b"bogus\n" + b"x" * 300 + b"\n")
        os.close(w)                                 # danach EOF
        done = []
        t = threading.Thread(target=lambda: done.append(agent.run([], out, r, interval=0.01)))
        t.start(); t.join(3)
        os.close(r)
        self.assertEqual(done, [0])
        text = out.getvalue()
        self.assertIn("CMDRESULT bogus denied", text)
        self.assertIn("CMDRESULT ? denied", text)   # zu lange Zeile

    def test_binary_garbage_does_not_crash(self):
        r, w = os.pipe()
        out = io.StringIO()
        os.write(w, bytes(range(256)) + b"\n")
        os.close(w)
        self.assertEqual(agent.run([], out, r, interval=0.01), 0)
        os.close(r)


if __name__ == "__main__":
    unittest.main()
