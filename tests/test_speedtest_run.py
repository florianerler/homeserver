import json, os, subprocess, sys, tempfile, time, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "server"))
import importlib.util
spec = importlib.util.spec_from_file_location("speedtest_run", os.path.join(os.path.dirname(__file__), "..", "server", "speedtest-run.py"))
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)

OOKLA = json.dumps({"ping": {"latency": 12.34}, "download": {"bandwidth": 60000000}, "upload": {"bandwidth": 6500000}})
CLI = json.dumps({"download": 480000000.5, "upload": 52000000.1, "ping": 11.9})
SH = os.path.join(os.path.dirname(__file__), "..")


def tools_with(path):
    return (("cli", path, []),)


class Parsers(unittest.TestCase):
    def test_ookla_bytes_per_second(self):
        self.assertEqual(run.parse_ookla(OOKLA), (480000, 52000, 12.3))

    def test_speedtest_cli_bits_per_second(self):
        self.assertEqual(run.parse_speedtest_cli(CLI), (480000, 52000, 11.9))

    def test_garbage_and_implausible_values(self):
        for parser, bad in [(run.parse_ookla, "kein json"), (run.parse_ookla, "{}"), (run.parse_speedtest_cli, "[1]"),
                            (run.parse_speedtest_cli, json.dumps({"download": 0, "upload": 5, "ping": 1})),
                            (run.parse_speedtest_cli, json.dumps({"download": 1e15, "upload": 5e6, "ping": 1})),
                            (run.parse_speedtest_cli, json.dumps({"download": 5e6, "upload": 5e6, "ping": -4}))]:
            with self.assertRaises((ValueError, KeyError, TypeError)):
                parser(bad)


class RunOnce(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.tool = os.path.join(self.dir, "fake-speedtest-cli")
        with open(self.tool, "w") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(self.tool, 0o755)
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def runner(self, text=CLI, exc=None):
        def r(argv):
            self.calls.append(argv)
            if exc:
                raise exc
            return text
        return r

    def state(self):
        return run.read_state(self.dir)

    def hist(self):
        with open(os.path.join(self.dir, "history.jsonl")) as fh:
            return [json.loads(l) for l in fh]

    def test_success_writes_history_and_state(self):
        self.assertEqual(run.run_once(self.dir, 1000, tools_with(self.tool), self.runner()), "ok")
        self.assertEqual(self.state()["state"], "idle"); self.assertTrue(self.state()["ok"])
        h = self.hist()
        self.assertEqual((h[0]["down"], h[0]["up"]), (480000, 52000))
        self.assertEqual(oct(os.stat(os.path.join(self.dir, "history.jsonl")).st_mode & 0o777), "0o644")
        self.assertEqual(self.calls, [[self.tool]])                     # fester Befehl, keine Extra-Argumente

    def test_cooldown_blocks_second_run(self):
        now = int(time.time())
        run.write_json_atomic(os.path.join(self.dir, "state.json"), {"state": "idle", "finished": now - 60, "ok": True})
        self.assertEqual(run.run_once(self.dir, now, tools_with(self.tool), self.runner()), "cooldown")
        self.assertEqual(self.calls, [])
        run.write_json_atomic(os.path.join(self.dir, "state.json"), {"state": "idle", "finished": now - 700, "ok": True})
        self.assertEqual(run.run_once(self.dir, now, tools_with(self.tool), self.runner()), "ok")

    def test_not_installed(self):
        self.assertEqual(run.run_once(self.dir, 1000, tools_with("/nicht/da"), self.runner()), "notinstalled")
        self.assertEqual(self.state()["state"], "notinstalled")
        self.assertEqual(self.calls, [])

    def test_failure_keeps_history_and_records_state(self):
        run.run_once(self.dir, 1000, tools_with(self.tool), self.runner())
        before = self.hist()
        os.unlink(os.path.join(self.dir, "state.json"))
        for exc in (subprocess.TimeoutExpired("x", 1), OSError("x"), ValueError("x")):
            self.assertEqual(run.run_once(self.dir, 5000, tools_with(self.tool), self.runner(exc=exc)), "failed")
            os.unlink(os.path.join(self.dir, "state.json")) if False else None
            run.write_json_atomic(os.path.join(self.dir, "state.json"), {"state": "idle", "finished": 1})
        self.assertEqual(self.hist(), before)
        self.assertEqual(run.run_once(self.dir, 9000, tools_with(self.tool), self.runner(text="muell")), "failed")

    def test_lock_prevents_parallel_runs(self):
        import fcntl
        holder = open(os.path.join(self.dir, "lock"), "a")
        fcntl.flock(holder, fcntl.LOCK_EX)
        try:
            self.assertEqual(run.run_once(self.dir, 1000, tools_with(self.tool), self.runner()), "locked")
        finally:
            holder.close()
        self.assertEqual(self.calls, [])

    def test_history_is_pruned_to_30_days_and_limit(self):
        path = os.path.join(self.dir, "history.jsonl")
        now = 100 * 86400
        with open(path, "w") as fh:
            fh.write(json.dumps({"ts": now - 31 * 86400, "down": 1, "up": 1, "ping": 1}) + "\nmuell\n")
            fh.write(json.dumps({"ts": now - 29 * 86400, "down": 2, "up": 2, "ping": 2}) + "\n")
        run.prune_and_append(path, {"ts": now, "down": 3, "up": 3, "ping": 3}, now)
        with open(path) as fh:
            self.assertEqual([json.loads(l)["down"] for l in fh], [2, 3])
        for i in range(5):
            run.prune_and_append(path, {"ts": now + i, "down": 10 + i, "up": 1, "ping": 1}, now + i, max_entries=3)
        with open(path) as fh:
            self.assertEqual(len(fh.readlines()), 3)

    def test_tool_preference_ookla_first(self):
        t = (("ookla", self.tool, ["--x"]), ("cli", self.tool, []))
        self.assertEqual(run.find_tool(t)[0], "ookla")
        self.assertIsNone(run.find_tool((("cli", "/nicht/da", []),)))


class Units(unittest.TestCase):
    def read(self, name):
        with open(os.path.join(SH, "server", name)) as fh:
            return fh.read()

    def test_service_is_sandboxed_and_unprivileged(self):
        s = self.read("homeserver-speedtest.service")
        for needle in ("User=hs-speedtest", "NoNewPrivileges=yes", "ProtectSystem=strict", "PrivateTmp=yes",
                       "StateDirectory=homeserver-speedtest", "MemoryMax=", "TimeoutStartSec="):
            self.assertIn(needle, s)
        self.assertEqual([l for l in s.splitlines() if l.startswith("ExecStartPre")],
                         ["ExecStartPre=+/bin/rm -f /run/homeserver-trigger/speedtest"])    # einziger Root-Schritt

    def test_trigger_dir_is_only_for_agent_user(self):
        self.assertIn("0700 traffic traffic", self.read("homeserver-tmpfiles.conf"))
        self.assertIn("PathExists=/run/homeserver-trigger/speedtest", self.read("homeserver-speedtest.path"))
        self.assertIn("0/3", self.read("homeserver-speedtest.timer"))


if __name__ == "__main__":
    unittest.main()
