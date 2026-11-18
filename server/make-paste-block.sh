#!/bin/bash
# Erzeugt EINEN Block zum Einfuegen in eine Web-Shell (kein scp/ssh noetig):
#   server/make-paste-block.sh ~/.config/homeserver/id_ed25519.pub > block.sh
# Der Block legt die Server-Dateien per Heredoc an und startet install-server.sh.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PUB="${1:?Usage: make-paste-block.sh <public-key-file>}"
KEY="$(tr -d '\n' < "$PUB")"
[[ $KEY == ssh-ed25519\ * && $KEY != *"'"* ]] || { echo "Kein gueltiger ed25519-Public-Key" >&2; exit 1; }

echo "set -eu"
echo "rm -rf /tmp/hs && mkdir -p /tmp/hs/server && cd /tmp/hs/server"
for f in agent.py collector.py homeserver-collector.service agent.conf.example install-server.sh; do
  tag="HS_EOF_$(echo "$f" | tr -c 'A-Za-z0-9\n' '_')"
  grep -qx "$tag" "$DIR/$f" && { echo "Trennzeichen $tag kommt in $f vor" >&2; exit 1; }
  echo "cat > $f <<'$tag'"
  cat "$DIR/$f"
  echo "$tag"
done
echo "sh install-server.sh '$KEY'"
