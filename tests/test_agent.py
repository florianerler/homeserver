import io, os, sys, tempfile, threading, unittest
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
