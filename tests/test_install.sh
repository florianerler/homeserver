#!/bin/bash
# Testet install.sh in einer Sandbox (eigenes HOME, Attrappen fuer 'omarchy'). Beruehrt nichts Echtes.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
export HOME="$T/home"; mkdir -p "$HOME/.config/omarchy/plugins" "$T/bin"
LOG="$T/calls.log"; : > "$LOG"

# Attrappen: protokollieren Aufrufe; 'bar put' fuegt den Eintrag wie Omarchy in shell.json ein.
cat > "$T/bin/omarchy" <<'STUB'
#!/bin/bash
echo "omarchy $*" >> "$LOG"
case "$1 $2" in
  "plugin list") echo "homeserver   disabled  third-party bar-widget  Homeserver" ;;
  "bar put")
    python3 -I - "$HOME/.config/omarchy/shell.json" "$3" <<'PY'
import json, sys
p, i = sys.argv[1], sys.argv[2]
d = json.load(open(p)); d["bar"]["layout"]["right"].append({"id": i})
json.dump(d, open(p, "w"), indent=2)
PY
    ;;
esac
STUB
printf '#!/bin/bash\necho "omarchy-shell $*" >> "$LOG"\n' > "$T/bin/omarchy-shell"
chmod +x "$T/bin/"*; export PATH="$T/bin:$PATH" LOG

ok() { echo "  ok: $1"; }
fail() { echo "  FEHLER: $1" >&2; exit 1; }
has_id() { python3 -I -c "import json,sys; d=json.load(open('$HOME/.config/omarchy/shell.json'))['bar']['layout']; sys.exit(0 if '$1' in [w['id'] for s in d.values() for w in s] else 1)"; }

echo "== Fall 1: Migration von der alten Installation"
mkdir -p "$HOME/.config/homeserver-traffic" "$HOME/.config/omarchy/hooks/post-update.d"
printf '[ssh]\nkey = ~/.config/homeserver-traffic/id_ed25519\n' > "$HOME/.config/homeserver-traffic/config.ini"
echo '{"bar":{"layout":{"right":[{"id":"omarchy.tray"},{"id":"homeserver.traffic"}]}}}' > "$HOME/.config/omarchy/shell.json"
ln -s "$REPO/plugin" "$HOME/.config/omarchy/plugins/homeserver-traffic"
echo '# homeserver.traffic alter hook' > "$HOME/.config/omarchy/hooks/post-update.d/post-update.sh"
"$REPO/install.sh" >/dev/null
[[ -d $HOME/.config/homeserver && ! -e $HOME/.config/homeserver-traffic ]] && ok "Config umgezogen" || fail "Config nicht umgezogen"
grep -q '.config/homeserver/id_ed25519' "$HOME/.config/homeserver/config.ini" && ok "Pfade in config.ini angepasst" || fail "Pfade nicht angepasst"
[[ $(stat -c %a "$HOME/.config/homeserver/config.ini") == 600 ]] && ok "config.ini 600" || fail "Rechte"
[[ ! -e $HOME/.config/omarchy/plugins/homeserver-traffic && -L $HOME/.config/omarchy/plugins/homeserver ]] && ok "Plugin-Link ersetzt" || fail "Plugin-Link"
has_id homeserver && ok "neuer Eintrag in der Leiste" || fail "neuer Eintrag fehlt"
has_id homeserver.traffic && fail "alter Eintrag noch da" || ok "alter Eintrag entfernt"
grep -q "bar put homeserver --after homeserver.traffic" "$LOG" && ok "Platzierung an der Stelle des alten" || fail "Platzierung"
ls "$HOME/.config/omarchy/shell.json.bak."* >/dev/null 2>&1 && ok "Backup von shell.json" || fail "kein Backup"
grep -q "hook install post-update" "$LOG" && ok "Hook installiert" || fail "Hook"
[[ ! -e $HOME/.config/omarchy/hooks/post-update.d/post-update.sh ]] && ok "alter Hook entfernt" || fail "alter Hook"

echo "== Fall 2: zweiter Lauf aendert nichts"
: > "$LOG"; B1=$(ls "$HOME/.config/omarchy/shell.json.bak."* | wc -l)
"$REPO/install.sh" >/dev/null
! grep -q "bar put" "$LOG" && ok "kein erneutes 'bar put'" || fail "doppelter Eintrag"
[[ $(ls "$HOME/.config/omarchy/shell.json.bak."* | wc -l) == "$B1" ]] && ok "kein neues Backup" || fail "unnoetiges Backup"

echo "== Fall 3: shell.json fehlt (frische Installation): kein Abbruch"
rm "$HOME/.config/omarchy/shell.json"
"$REPO/install.sh" >/dev/null 2>"$T/err" && ok "install.sh endet erfolgreich" || fail "Abbruch bei fehlender shell.json"
grep -q "shell.json" "$T/err" && ok "verstaendlicher Hinweis" || fail "kein Hinweis"
"$REPO/install.sh" --bar-only 2>/dev/null && ok "--bar-only bricht nicht ab" || fail "--bar-only"

echo "== Fall 4: Hook folgt dem Plugin-Link"
: > "$LOG"; echo '{"bar":{"layout":{"right":[{"id":"omarchy.tray"}]}}}' > "$HOME/.config/omarchy/shell.json"
bash "$REPO/hooks/homeserver-post-update.sh"
has_id homeserver && ok "Hook stellt den Eintrag wieder her" || fail "Hook"
echo "Alle Install-Tests ok"
