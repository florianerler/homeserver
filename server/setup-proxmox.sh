#!/bin/sh
# Auf dem Proxmox-Host als root ausfuehren:
#   sh setup-proxmox.sh "$(cat id_ed25519.pub)"
# Legt einen unprivilegierten Benutzer 'traffic' an, dessen SSH-Key NUR das
# Zaehler-Skript ausfuehren darf (restrict + command=).
set -eu
PUBKEY="${1:?Usage: setup-proxmox.sh '<ssh-ed25519 AAAA... comment>'}"
case "$PUBKEY" in ssh-ed25519\ *) ;; *) echo "Kein ed25519-Public-Key" >&2; exit 1;; esac

id traffic >/dev/null 2>&1 || useradd -m -s /bin/sh -U traffic
usermod -p '*' traffic      # kein Passwort-Login, Key-Login bleibt moeglich

cat > /usr/local/bin/traffic-counters.sh <<'SCRIPT'
#!/bin/sh
# Jede Sekunde eine Zeile: <uptime> <nic0_rx> <nic0_tx> <vmbr0_rx> <vmbr0_tx>
# Endet von selbst, sobald die SSH-Verbindung getrennt wird (SIGPIPE).
while :; do
  read up _ < /proc/uptime
  echo "$up $(cat /sys/class/net/nic0/statistics/rx_bytes) $(cat /sys/class/net/nic0/statistics/tx_bytes) $(cat /sys/class/net/vmbr0/statistics/rx_bytes) $(cat /sys/class/net/vmbr0/statistics/tx_bytes)"
  sleep 1
done
SCRIPT
chown root:root /usr/local/bin/traffic-counters.sh
chmod 755 /usr/local/bin/traffic-counters.sh

mkdir -p /home/traffic/.ssh
printf 'restrict,command="/usr/local/bin/traffic-counters.sh" %s\n' "$PUBKEY" > /home/traffic/.ssh/authorized_keys
chown -R root:root /home/traffic/.ssh      # root-eigen: 'traffic' kann den Key nicht aendern
chmod 755 /home/traffic/.ssh
chmod 644 /home/traffic/.ssh/authorized_keys
echo "Fertig. Host-Key-Fingerprint (gegen die Abfrage vom Client pruefen):"
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
