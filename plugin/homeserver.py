#!/usr/bin/env python3
"""Einstiegspunkt des Homeserver-Streamers. Start: python3 -I homeserver.py"""
import os
import sys

# Mit 'python3 -I' ist weder das aktuelle noch das Skript-Verzeichnis im Suchpfad.
# Wir fuegen bewusst NUR das eigene (aufgeloeste) Verzeichnis hinzu, nie das CWD.
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from hs.main import main  # noqa: E402

if __name__ == "__main__":
    main()
