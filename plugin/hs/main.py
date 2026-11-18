"""Hauptprogramm: startet die Quellen und gibt jede Sekunde einen JSON-Snapshot aus."""
import argparse
import json
import os
import signal
import sys
import threading
import time

from . import config as cfgmod
from .agent_source import AgentSource
from .base import StaticFailure
from .qbt_source import QbtSource

SNAPSHOT_VERSION = 2


# Befehle, die die Oberflaeche (ueber stdin des Streamers) senden darf. Exakte Namen, keine Argumente.
# Der Server prueft zusaetzlich seine eigene Whitelist.
UI_COMMANDS = frozenset({"speedtest"})


def control_loop(agent, stream):
    """Zeilen von stdin lesen und nur Whitelist-Befehle an den Server weiterreichen."""
    for line in stream:
        line = line.strip()
        if line in UI_COMMANDS:
            agent.send(line)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Homeserver-Streamer fuer die Omarchy-Leiste")
    ap.add_argument("--config", default=os.environ.get("HOMESERVER_CONFIG", cfgmod.DEFAULT_CONFIG))
    ap.add_argument("--count", type=int, default=0, help="nach N Zeilen beenden (Test)")
    args = ap.parse_args(argv)

    try:
        cfg = cfgmod.load_config(args.config)
        sources = (AgentSource(cfg), QbtSource(cfg))
    except cfgmod.ConfigError as e:
        print(f"homeserver: {e}", file=sys.stderr)
        sources = (StaticFailure(str(e), ("net", "system", "guests", "ping", "storage", "smart", "speed")), StaticFailure(str(e), ("qbt",)))

    def shutdown(*_):
        for s in sources:
            s.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    for s in sources:
        s.start()
    agents = [s for s in sources if isinstance(s, AgentSource)]
    if agents and not args.count:
        threading.Thread(target=control_loop, args=(agents[0], sys.stdin), daemon=True).start()

    emitted = 0
    try:
        while True:
            cards = {}
            for source in sources:
                cards.update(source.snapshots())
            snap = {"v": SNAPSHOT_VERSION, "cards": cards}
            print(json.dumps(snap, separators=(",", ":")), flush=True)
            emitted += 1
            if args.count and emitted >= args.count:
                break
            time.sleep(1.0)
    except (BrokenPipeError, KeyboardInterrupt):
        pass        # Leiste hat uns beendet (z. B. Neustart) -> sauber aufraeumen
    finally:
        for s in sources:
            s.stop()
        try:
            sys.stdout.close()
        except (BrokenPipeError, OSError):
            pass
