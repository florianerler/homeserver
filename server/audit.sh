#!/bin/sh
# Reiner Lese-Check der Server-Seite (als root ausfuehren). Aendert nichts.
#   - ueberlebt alles einen Neustart? (Units aktiviert, Verzeichnisse per tmpfiles, Daten unter /var/lib)
#   - sind Dateien und Benutzer so eingeschraenkt wie vorgesehen?
# Ausgabe: OK / WARN je Pruefung. Keine Secrets, nur Metadaten.
LIB=/usr/local/lib/homeserver
bad=0
ok()   { echo "  OK    $*"; }
warn() { echo "  WARN  $*"; bad=$((bad + 1)); }
section() { echo; echo "== $*"; }

section "Neustart-Festigkeit: Units aktiviert und aktiv"
for u in homeserver-collector.service homeserver-speedtest.timer homeserver-speedtest.path; do
  en=$(systemctl is-enabled "$u" 2>/dev/null); ac=$(systemctl is-active "$u" 2>/dev/null)
  if [ "$en" = enabled ] && [ "$ac" = active ]; then ok "$u ($en, $ac)"; else warn "$u: enabled=$en active=$ac"; fi
done

section "Neustart-Festigkeit: Verzeichnisse und Daten"
[ -f /etc/tmpfiles.d/homeserver.conf ] && ok "tmpfiles-Regel vorhanden (legt /run/homeserver-trigger bei jedem Boot an)" || warn "/etc/tmpfiles.d/homeserver.conf fehlt"
[ -d /var/lib/homeserver-speedtest ] && ok "/var/lib/homeserver-speedtest (liegt auf der Platte, Verlauf bleibt erhalten)" || warn "/var/lib/homeserver-speedtest fehlt"
[ -d /run/homeserver-trigger ] && ok "/run/homeserver-trigger vorhanden" || warn "/run/homeserver-trigger fehlt"
for f in guests storage smart; do
  p=/run/homeserver/$f.json
  if [ -f "$p" ]; then age=$(( $(date +%s) - $(stat -c %Y "$p") )); [ "$age" -lt 1800 ] && ok "$p (Alter ${age}s, wird vom Collector laufend neu erzeugt)" || warn "$p ist ${age}s alt"; else warn "$p fehlt"; fi
done
if [ -f /var/lib/homeserver-speedtest/history.jsonl ]; then n=$(wc -l < /var/lib/homeserver-speedtest/history.jsonl); else n=0; fi
echo "  info  Speedtest-Verlauf: $n Messung(en)"

section "Rechte: Programme und Konfiguration (muessen root gehoeren, nicht gruppen-/welt-beschreibbar)"
for f in $LIB $LIB/agent.py $LIB/collector.py $LIB/speedtest-run.py /etc/homeserver /etc/homeserver/agent.conf \
         /etc/systemd/system/homeserver-collector.service /etc/systemd/system/homeserver-speedtest.service \
         /etc/systemd/system/homeserver-speedtest.timer /etc/systemd/system/homeserver-speedtest.path \
         /home/traffic/.ssh /home/traffic/.ssh/authorized_keys; do
  [ -e "$f" ] || { warn "$f fehlt"; continue; }
  line=$(stat -c '%U:%G %a' "$f"); mode=$(stat -c %a "$f")
  g=$(echo "$mode" | cut -c2); o=$(echo "$mode" | cut -c3)
  if [ "${line%% *}" = root:root ] && [ $((g & 2)) -eq 0 ] && [ $((o & 2)) -eq 0 ]; then ok "$line $f"; else warn "$line $f"; fi
done

section "SSH-Key des Agenten"
ak=/home/traffic/.ssh/authorized_keys
cnt=$(grep -c . "$ak" 2>/dev/null || echo 0)
[ "$cnt" = 1 ] && ok "genau ein Eintrag" || warn "$cnt Eintraege (erwartet: 1)"
opts=$(awk 'NR==1{print $1}' "$ak" 2>/dev/null)
case "$opts" in
  restrict,command=\"$LIB/agent.py\") ok "Optionen: $opts" ;;
  *) warn "Optionen unerwartet: $opts" ;;
esac

section "Benutzer"
for u in traffic hs-speedtest; do
  st=$(passwd -S "$u" 2>/dev/null | awk '{print $2}'); sh=$(getent passwd "$u" | cut -d: -f7)
  echo "  info  $u: Passwort-Status=$st Shell=$sh Gruppen=$(id -nG "$u" | tr ' ' ',')"
  case "$st" in L|LK|NP) ok "$u hat kein nutzbares Passwort" ;; *) warn "$u: Passwort-Status $st" ;; esac
done
if grep -rqsE "(^|[[:space:]])(traffic|hs-speedtest)[[:space:]]" /etc/sudoers /etc/sudoers.d 2>/dev/null; then warn "sudoers erwaehnt traffic oder hs-speedtest"; else ok "kein sudo-Eintrag fuer traffic/hs-speedtest"; fi

section "Sandboxing (systemd-analyze security; kleiner = besser, 'EXPOSED' wuerde Handlungsbedarf bedeuten)"
for u in homeserver-collector.service homeserver-speedtest.service; do
  echo "  info  $u: $(systemd-analyze security "$u" --no-pager 2>/dev/null | tail -n 1 | sed 's/^[^A-Za-z]*//')"
done

section "Netzwerk: lauscht unser Dienst irgendwo? (erwartet: nein, nur die ueblichen Proxmox-/SSH-Ports)"
if ss -ltnpH 2>/dev/null | grep -E "homeserver|agent\.py|collector\.py|speedtest-run" >/dev/null; then warn "einer unserer Prozesse lauscht"; else ok "keiner unserer Prozesse lauscht auf einem Port"; fi
ss -ltnH 2>/dev/null | awk '{print "  info  lauscht: " $4}' | sort -u

section "Altlast"
[ -e /usr/local/bin/traffic-counters.sh ] && warn "/usr/local/bin/traffic-counters.sh (altes Skript) liegt noch da; wird nicht mehr benutzt und kann geloescht werden" || ok "kein altes Skript mehr"

echo
[ "$bad" -eq 0 ] && echo "ERGEBNIS: alles OK" || echo "ERGEBNIS: $bad Hinweis(e) oben (WARN)"
exit 0
