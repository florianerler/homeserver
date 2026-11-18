#!/bin/bash
# Installiert das Plugin fuer den aktuellen Benutzer. Idempotent: mehrfaches Ausfuehren ist
# ungefaehrlich. Aendert nur Dateien unter ~/.config.
#   ./install.sh            alles (Config, Plugin-Link, Leisten-Eintrag, Update-Hook)
#   ./install.sh --bar-only nur den Leisten-Eintrag herstellen (vom Update-Hook genutzt)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_DIR="$HOME/.config/homeserver"
OLD_CONF_DIR="$HOME/.config/homeserver-traffic"
PLUGINS_DIR="$HOME/.config/omarchy/plugins"
PLUGIN_LINK="$PLUGINS_DIR/homeserver"
OLD_PLUGIN_LINK="$PLUGINS_DIR/homeserver-traffic"
SHELL_JSON="$HOME/.config/omarchy/shell.json"
HOOK_DIR="$HOME/.config/omarchy/hooks/post-update.d"
ID="homeserver"
OLD_ID="homeserver.traffic"
BAR_ONLY=0
[[ ${1:-} == "--bar-only" ]] && BAR_ONLY=1

say() { [[ $BAR_ONLY == 1 ]] || echo "$*"; }

# bar_has <id>: 0 = steht in der Leiste, 1 = nicht, 2 = shell.json fehlt oder ist kaputt.
bar_has() {
  python3 -I - "$SHELL_JSON" "$1" <<'PY'
import json, sys
try:
    layout = json.load(open(sys.argv[1]))["bar"]["layout"]
except (OSError, ValueError, KeyError):
    sys.exit(2)
ids = {w.get("id") for section in layout.values() if isinstance(section, list) for w in section if isinstance(w, dict)}
sys.exit(0 if sys.argv[2] in ids else 1)
PY
}

# Fallback, falls 'omarchy plugin disable' den alten Eintrag nicht aus dem Layout nimmt.
bar_remove() {
  python3 -I - "$SHELL_JSON" "$1" <<'PY'
import json, sys
path, target = sys.argv[1], sys.argv[2]
data = json.load(open(path))
for name, section in data["bar"]["layout"].items():
    if isinstance(section, list):
        data["bar"]["layout"][name] = [w for w in section if not (isinstance(w, dict) and w.get("id") == target)]
with open(path, "w") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
PY
}

backup_shell_json() { cp "$SHELL_JSON" "$SHELL_JSON.bak.$(date +%s)"; }

wait_for_plugin() {
  for _ in 1 2 3 4 5 6; do
    omarchy plugin list 2>/dev/null | grep -q "^$ID " && return 0
    omarchy-shell shell rescanPlugins >/dev/null 2>&1 || true
    sleep 1
  done
  return 1
}

install_bar_entry() {
  local rc=0
  bar_has "$ID" || rc=$?
  if [[ $rc == 2 ]]; then
    echo "Hinweis: $SHELL_JSON fehlt oder ist unlesbar. Leisten-Eintrag uebersprungen." >&2
    echo "         Omarchy-Shell einmal starten und danach './install.sh --bar-only' ausfuehren." >&2
    return 0
  fi
  local old_present=1
  bar_has "$OLD_ID" && old_present=0 || true
  if [[ $rc == 0 && $old_present != 0 ]]; then return 0; fi      # nichts zu tun

  backup_shell_json
  wait_for_plugin || { echo "Plugin '$ID' nicht gefunden (omarchy plugin list)." >&2; return 1; }
  if [[ $rc != 0 ]]; then
    if [[ $old_present == 0 ]]; then
      omarchy bar put "$ID" --after "$OLD_ID" >/dev/null     # an der Stelle des alten Eintrags
    else
      omarchy bar put "$ID" --before omarchy.tray >/dev/null
    fi
    say "Leisten-Eintrag '$ID' gesetzt."
  fi
  if [[ $old_present == 0 ]]; then
    omarchy plugin disable "$OLD_ID" >/dev/null 2>&1 || true
    if bar_has "$OLD_ID"; then bar_remove "$OLD_ID"; fi
    say "Alten Eintrag '$OLD_ID' entfernt."
  fi
}

if [[ $BAR_ONLY == 1 ]]; then
  install_bar_entry
  exit 0
fi

for cmd in python3 ssh omarchy; do
  command -v "$cmd" >/dev/null || { echo "Fehlt: $cmd" >&2; exit 1; }
done

# 1) Config-Ordner: Umzug von ~/.config/homeserver-traffic (alter Name)
if [[ -d $OLD_CONF_DIR && ! -e $CONF_DIR ]]; then
  mv "$OLD_CONF_DIR" "$CONF_DIR"
  [[ -f $CONF_DIR/config.ini ]] && sed -i 's#\.config/homeserver-traffic/#.config/homeserver/#g' "$CONF_DIR/config.ini"
  echo "Config umgezogen: $OLD_CONF_DIR -> $CONF_DIR"
elif [[ -d $OLD_CONF_DIR && -e $CONF_DIR ]]; then
  echo "Hinweis: $OLD_CONF_DIR und $CONF_DIR existieren beide; alter Ordner bleibt unberuehrt." >&2
fi
mkdir -p "$CONF_DIR"; chmod 700 "$CONF_DIR"
if [[ ! -e $CONF_DIR/config.ini ]]; then
  install -m 600 "$REPO/config.example" "$CONF_DIR/config.ini"
  echo "config.ini angelegt: bitte $CONF_DIR/config.ini ausfuellen."
fi
chmod 600 "$CONF_DIR/config.ini"

# 2) Plugin-Link (alten Link nur entfernen, wenn er in dieses Repo zeigt)
mkdir -p "$PLUGINS_DIR"
if [[ -L $OLD_PLUGIN_LINK && "$(readlink -f "$OLD_PLUGIN_LINK")" == "$REPO"/* ]]; then
  rm "$OLD_PLUGIN_LINK"
fi
ln -sfn "$REPO/plugin" "$PLUGIN_LINK"
omarchy-shell shell rescanPlugins >/dev/null 2>&1 || true

# 3) Leisten-Eintrag (neu oder anstelle des alten)
install_bar_entry

# 4) Update-Hook (alten Hook gleicher Herkunft ersetzen)
if [[ -f $HOOK_DIR/post-update.sh ]] && grep -q "$OLD_ID" "$HOOK_DIR/post-update.sh"; then
  rm "$HOOK_DIR/post-update.sh"
fi
omarchy hook install post-update "$REPO/hooks/homeserver-post-update.sh" >/dev/null

echo "Fertig. Falls noetig: Server einrichten (README, 'Server einrichten'), danach 'omarchy restart shell'."
