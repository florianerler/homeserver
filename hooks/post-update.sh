#!/bin/bash
# Omarchy-Hook (post-update): stellt das Widget in der Leiste wieder her, falls
# ein Update oder 'omarchy refresh shell' das Layout zurueckgesetzt hat.
# Das Plugin selbst (~/.config/omarchy/plugins/homeserver-traffic) wird davon
# nie beruehrt; nur der Eintrag in shell.json kann verloren gehen.
SHELL_JSON="$HOME/.config/omarchy/shell.json"
if [[ -e "$HOME/.config/omarchy/plugins/homeserver-traffic/manifest.json" ]] \
   && ! grep -q '"homeserver.traffic"' "$SHELL_JSON" 2>/dev/null; then
  cp "$SHELL_JSON" "$SHELL_JSON.bak.$(date +%s)"
  omarchy-shell shell rescanPlugins >/dev/null 2>&1
  omarchy bar put homeserver.traffic --before omarchy.tray
fi
