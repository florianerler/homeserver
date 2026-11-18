import json, os, subprocess, sys, tempfile, unittest
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "server"))
import collector

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "pvesh_cluster_resources.json")


def load_json(path):
    with open(path) as fh:
        return json.load(fh)


def fixture_text():
    with open(FIXTURE) as fh:
        return fh.read()


class ParseGuests(unittest.TestCase):
    def setUp(self):
        self.guests = collector.parse_guests(fixture_text())

    def test_only_real_guests_sorted_by_vmid(self):
        self.assertEqual([g["vmid"] for g in self.guests], [100, 104, 200, 201])

    def test_templates_storage_and_broken_entries_dropped(self):
        ids = {g["vmid"] for g in self.guests}
        self.assertNotIn(9000, ids)       # Vorlage
        self.assertNotIn(5, ids)          # Storage
        self.assertEqual(len(self.guests), 4)

    def test_status_normalised(self):
        by = {g["vmid"]: g["status"] for g in self.guests}
        self.assertEqual(by, {100: "running", 104: "running", 200: "stopped", 201: "other"})

    def test_hostile_name_is_sanitised(self):
        name = next(g["name"] for g in self.guests if g["vmid"] == 200)
        self.assertRegex(name, r"^[A-Za-z0-9._-]+$")
        self.assertNotIn("$", name)
        self.assertNotIn(" ", name)

    def test_limits(self):
        many = json.dumps([{"type": "lxc", "vmid": i, "name": "x" * 100, "status": "running"} for i in range(1, 200)])
        out = collector.parse_guests(many)
        self.assertEqual(len(out), collector.MAX_GUESTS)
        self.assertTrue(all(len(g["name"]) <= 32 for g in out))

    def test_garbage_raises(self):
        for bad in ["", "nicht json", '{"a":1}', "42"]:
            with self.assertRaises(ValueError):
                collector.parse_guests(bad)


class Tasks(unittest.TestCase):
    def test_collect_guests_uses_fixed_command_only(self):
        seen = []

        def fake_run(argv, timeout=15):
            seen.append(argv)
            return fixture_text()
        data = collector.collect_guests(fake_run)
        self.assertEqual(seen, [collector.PVESH])                 # genau der feste Befehl
        self.assertEqual(seen[0][0], "/usr/bin/pvesh")
        self.assertIsInstance(data["ts"], int)
        self.assertEqual(len(data["guests"]), 4)

    def test_file_is_written_atomically_world_readable(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(collector.run_task("guests.json", lambda: {"ts": 1, "guests": []}, d))
            path = os.path.join(d, "guests.json")
            self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o644")
            self.assertFalse(os.path.exists(path + ".tmp"))
            self.assertEqual(load_json(path), {"ts": 1, "guests": []})

    def test_failure_keeps_old_file(self):
        with tempfile.TemporaryDirectory() as d:
            collector.run_task("guests.json", lambda: {"ts": 1, "guests": []}, d)

            def boom():
                raise subprocess.TimeoutExpired("pvesh", 15)
            self.assertFalse(collector.run_task("guests.json", boom, d))
            self.assertEqual(load_json(os.path.join(d, "guests.json"))["ts"], 1)

    def test_error_log_has_no_payload(self):
        import io, contextlib
        buf = io.StringIO()

        def boom():
            raise ValueError("geheime nutzdaten 12345")
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stderr(buf):
            collector.run_task("guests.json", boom, d)
        self.assertNotIn("geheime", buf.getvalue())
        self.assertIn("ValueError", buf.getvalue())

    def test_run_fixed_has_no_shell_and_clean_env(self):
        out = collector.run_fixed([sys.executable, "-c", "import os;print(sorted(os.environ))"])
        self.assertNotIn("HOME", out)                              # minimale Umgebung


if __name__ == "__main__":
    unittest.main()
