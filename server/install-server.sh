#!/bin/sh
# Auf dem Proxmox-Host als root ausfuehren (der Ordner 'server/' muss dort liegen):
#   sh install-server.sh "$(cat id_ed25519.pub)"
# Idempotent. Legt den Benutzer 'traffic' an, installiert den Agenten und bindet den
# SSH-Key per restrict,command= daran. Ueberschreibt eine vorhandene agent.conf nicht.
set -eu
[ "$(id -u)" = 0 ] || { echo "Bitte als root ausfuehren." >&2; exit 1; }
PUBKEY="${1:?Usage: install-server.sh '<ssh-ed25519 AAAA... comment>'}"
case "$PUBKEY" in ssh-ed25519\ *) ;; *) echo "Kein ed25519-Public-Key" >&2; exit 1;; esac
SRC="$(cd "$(dirname "$0")" && pwd)"
LIB=/usr/local/lib/homeserver
CONFDIR=/etc/homeserver

id traffic >/dev/null 2>&1 || useradd -m -s /bin/sh -U traffic
usermod -p '*' traffic          # kein Passwort-Login, Key-Login bleibt moeglich

install -d -m 755 -o root -g root "$LIB" "$CONFDIR"
install -m 755 -o root -g root "$SRC/agent.py" "$LIB/agent.py"
[ -e "$CONFDIR/agent.conf" ] || install -m 644 -o root -g root "$SRC/agent.conf.example" "$CONFDIR/agent.conf"

# Authorized-Keys root-eigen: 'traffic' kann den Key nicht aendern.
mkdir -p /home/traffic/.ssh
printf 'restrict,command="%s/agent.py" %s\n' "$LIB" "$PUBKEY" > /home/traffic/.ssh/authorized_keys
chown -R root:root /home/traffic/.ssh
chmod 755 /home/traffic/.ssh
chmod 644 /home/traffic/.ssh/authorized_keys

echo "== Selbsttest als Benutzer traffic (ein Takt):"
su -s /bin/sh traffic -c "python3 $LIB/agent.py --once" || { echo "Selbsttest FEHLGESCHLAGEN" >&2; exit 1; }
echo "== Fertig. Host-Key-Fingerprint (gegen die Abfrage vom Client pruefen):"
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
echo "Rueckfall auf das alte Skript (falls noetig): /usr/local/bin/traffic-counters.sh liegt noch dort."
