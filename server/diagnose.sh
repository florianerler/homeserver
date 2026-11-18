#!/bin/sh
# Nur lesend. Auf dem Proxmox-Host als root ausfuehren und die Ausgabe schicken:
#   sh diagnose.sh
# Gibt keine Secrets, Seriennummern oder MAC-Adressen aus.
echo "== OS / Proxmox"
grep -E '^(PRETTY_NAME|VERSION_CODENAME)=' /etc/os-release
pveversion 2>/dev/null | head -1
dpkg --print-architecture
echo "== Python"
python3 --version 2>&1
echo "== Werkzeuge (Pfad oder FEHLT)"
for c in python3 smartctl pvesh pvesm pct qm ping flock systemctl curl wget; do
  p=$(command -v "$c") && echo "$c: $p" || echo "$c: FEHLT"
done
echo "== ping: Rechte (unprivilegiert nutzbar?)"
ls -l "$(command -v ping)" 2>/dev/null; getcap "$(command -v ping)" 2>/dev/null
sysctl net.ipv4.ping_group_range 2>/dev/null
echo "== Netzwerk-Interfaces"
ip -br link | awk '{print $1, $2}'
echo "== Temperatursensoren (hwmon)"
for d in /sys/class/hwmon/hwmon*; do
  [ -d "$d" ] || continue
  printf '%s name=%s:' "$(basename "$d")" "$(cat "$d/name" 2>/dev/null)"
  for t in "$d"/temp*_input; do
    [ -e "$t" ] || continue
    l="${t%_input}_label"
    printf ' %s(%s)' "$(basename "${t%_input}")" "$(cat "$l" 2>/dev/null || echo -)"
  done
  echo
done
echo "== Storage (pvesm status)"
pvesm status 2>&1 | head -20
echo "== Datentraeger (ohne Seriennummern)"
lsblk -d -o NAME,TYPE,ROTA,SIZE,TRAN 2>/dev/null
smartctl --scan 2>&1 | head -10
echo "== Benutzer traffic / sshd"
id traffic 2>&1
sshd -T 2>/dev/null | grep -iE '^(maxsessions|clientaliveinterval|clientalivecountmax) '
echo "== Gaeste (Anzahl)"
pvesh get /cluster/resources --type vm --output-format json 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d), "Gaeste:", sorted({(g["type"], g["status"]) for g in d}))' 2>&1
echo "== Bestehende Homeserver-Dateien"
ls -l /usr/local/bin/traffic-counters.sh /usr/local/lib/homeserver /etc/homeserver 2>&1 | head
