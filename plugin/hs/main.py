"""Hauptprogramm: startet die Quellen und gibt jede Sekunde einen JSON-Snapshot aus."""
import argparse
import json
import os
import signal
import sys
import time

from . import config as cfgmod
from .agent_source import AgentSource
from .base import StaticFailure
from .qbt_source import QbtSource

SNAPSHOT_VERSION = 2


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
        sources = (StaticFailure(str(e), ("net", "system", "guests")), StaticFailure(str(e), ("qbt",)))

    def shutdown(*_):
        for s in sources:
            s.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    for s in sources:
        s.start()

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
