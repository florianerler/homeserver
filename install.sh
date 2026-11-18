#!/bin/bash
# Installiert das Plugin fuer den aktuellen Benutzer. Idempotent: mehrfaches
# Ausfuehren ist ungefaehrlich. Aendert nur Dateien unter ~/.config.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_DIR="$HOME/.config/homeserver-traffic"
PLUGIN_LINK="$HOME/.config/omarchy/plugins/homeserver-traffic"
SHELL_JSON="$HOME/.config/omarchy/shell.json"

for cmd in python3 ssh omarchy; do
  command -v "$cmd" >/dev/null || { echo "Fehlt: $cmd" >&2; exit 1; }
done

mkdir -p "$CONF_DIR"; chmod 700 "$CONF_DIR"
if [[ ! -e "$CONF_DIR/config.ini" ]]; then
  install -m 600 "$REPO/config.example" "$CONF_DIR/config.ini"
  echo "config.ini angelegt: bitte $CONF_DIR/config.ini ausfuellen."
fi
chmod 600 "$CONF_DIR/config.ini"

mkdir -p "$(dirname "$PLUGIN_LINK")"
ln -sfn "$REPO/plugin" "$PLUGIN_LINK"
omarchy-shell shell rescanPlugins >/dev/null 2>&1 || true

if ! grep -q '"homeserver.traffic"' "$SHELL_JSON"; then
  cp "$SHELL_JSON" "$SHELL_JSON.bak.$(date +%s)"
  omarchy bar put homeserver.traffic --before omarchy.network
fi

omarchy hook install post-update "$REPO/hooks/post-update.sh" >/dev/null
echo "Fertig. Fehlt noch: SSH-Key + Host-Key (README, Schritt 2) und Zugangsdaten in config.ini."
