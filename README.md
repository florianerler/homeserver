# homeserver

Leisten-Widget für **Omarchy** (Quickshell-Leiste `omarchy-shell`, nicht Waybar), das den
Heimserver im Blick behält. Das Popup besteht aus Karten, eine pro Bereich; jede Karte fällt
einzeln aus, ohne die anderen zu stören.

| Karte | Quelle | Stand |
|---|---|---|
| Proxmox-Host: Download / Upload | SSH-Agent, liest Zählerstände | ✅ |
| qBittorrent: Download / Upload | WebUI-API `/api/v2/transfer/info` | ✅ |
| System, Gäste, Ping, Speicher/SMART, Speedtest, Aktionen | siehe `server/` | geplant (Phasen 1–5) |

- **Leiste:** nur das Server-Symbol (ausgegraut, wenn der Host offline ist).
- **Tooltip:** die aktuellen Werte.
- **Klick:** Popup (wie das WLAN-Panel) mit Live-Werten, 90-s-Verlauf, Status je Quelle samt Grund und
  der zweiten Schnittstelle als Zusatzzeile. Schließen mit `Esc` oder Klick daneben.
- Einheiten skalieren automatisch (B/s, kB/s, MB/s, GB/s; SI, 1 kB = 1000 B).

## Aufbau

```
plugin/homeserver.py        Einstieg des Streamers (python3 -I), startet das Paket hs/
plugin/hs/                  Streamer: Protokoll, Config, SSH-Agent-Quelle, qBittorrent-Quelle (nur stdlib)
plugin/*.qml, Model.js, i18n/   Oberfläche (Quickshell/QML), Texte de/en
server/agent.py             Server-Agent (läuft als Benutzer traffic, per command= im SSH-Key)
server/agent.conf.example   Server-Konfiguration (welche Schnittstellen gesendet werden)
server/install-server.sh    einmalig auf dem Proxmox-Host: Benutzer, Agent, Key-Bindung
server/diagnose.sh          nur lesend: Fakten über den Server sammeln
hooks/homeserver-post-update.sh   stellt den Leisten-Eintrag nach Omarchy-Updates wieder her
install.sh                  Installation und Migration (idempotent)
tests/                      Python-, Node- und Installer-Tests
```

Der Streamer läuft **dauerhaft** (ein Prozess, **eine** SSH-Verbindung pro Leiste), statt bei jedem
Intervall neu zu starten. So bleiben Vorwerte und Verlauf im Speicher. Die Leiste startet ihn und liest
einen JSON-Snapshot pro Sekunde.

### Protokoll Server → Client

Zeilenbasiert, ein Typ-Präfix pro Zeile, Felder per Leerzeichen getrennt. Unbekannte Typen werden
ignoriert, ungültige Zeilen verworfen (max. 512 Zeichen).

```
HELLO homeserver-agent 1                 Protokollversion, erste Zeile
NET <uptime> <iface> <rx_bytes> <tx_bytes>     jede Sekunde, eine Zeile je konfigurierter Schnittstelle
```

Der Client rechnet Raten selbst aus der **Server-Uptime**-Differenz (nicht aus der lokalen Uhr, damit
Netzwerk-Jitter nichts verfälscht). Bei Zähler-Reset, rückwärts laufender Zeit oder großer Lücke wird
neu aufgesetzt. Ein alter Agent (`traffic-counters.sh`) wird erkannt und als „Server-Agent veraltet"
gemeldet. Befehle vom Client zum Server laufen über **stdin derselben Verbindung** und müssen auf einer
festen Whitelist stehen (aktuell: keine). Schließt der Client, meldet der Kernel EOF auf stdin, der Agent
beendet sich und damit die Sitzung, auch wenn der Client hart abgeschossen wird.

## Installation

Voraussetzungen: Omarchy 4.x, `python3`, `ssh` (alles ohnehin vorhanden).

```bash
git clone https://github.com/florianerler/homeserver-traffic ~/Work/homeserver && cd ~/Work/homeserver
./install.sh
```

Der Installer legt `~/.config/homeserver/config.ini` aus `config.example` an (chmod 600), verlinkt das
Plugin nach `~/.config/omarchy/plugins/homeserver`, trägt es per `omarchy bar put` in die Leiste ein (mit
Backup von `shell.json`) und installiert den Update-Hook. Gibt es noch die alte Installation
(`homeserver.traffic`, `~/.config/homeserver-traffic/`), wird sie **migriert**: Config-Ordner umziehen,
Pfade in der `config.ini` anpassen, neuen Leisten-Eintrag an der Stelle des alten einsetzen, alten entfernen.
Fehlt `shell.json` (frisches System), bricht der Installer nicht ab, sondern sagt, was zu tun ist.
Danach `omarchy restart shell` (die Shell behält QML im Speicher; ein Rescan genügt nicht).

### Schritt 1: Zugangsdaten

`~/.config/homeserver/config.ini` ausfüllen (Vorlage: `config.example`). Ein `%` im Passwort ist erlaubt.

### Schritt 2: SSH-Key und Server einrichten

```bash
# Schlüssel erzeugen (ohne Passphrase, die Leiste braucht ihn unbeaufsichtigt)
ssh-keygen -t ed25519 -N "" -C homeserver -f ~/.config/homeserver/id_ed25519

# Ordner server/ auf den Proxmox-Host bringen und dort als root ausführen:
tar cz -C ~/Work/homeserver server | ssh root@<proxmox-ip> 'rm -rf /tmp/hs && mkdir /tmp/hs && tar xz -C /tmp/hs'
ssh root@<proxmox-ip> "sh /tmp/hs/server/install-server.sh '$(cat ~/.config/homeserver/id_ed25519.pub)'"
```

Der Installer legt den Benutzer `traffic` an, installiert `/usr/local/lib/homeserver/agent.py`
(root-eigen), schreibt `/etc/homeserver/agent.conf` (nur wenn sie fehlt) und bindet den Key mit
`restrict,command="/usr/local/lib/homeserver/agent.py"` an den Agenten: kein Terminal, keine Weiterleitungen,
nur dieses Programm. Er macht am Ende einen Selbsttest als `traffic` und zeigt den Host-Key-Fingerprint.

**Welche Schnittstellen gesendet werden**, steht serverseitig in `/etc/homeserver/agent.conf`
(`interfaces = nic0 vmbr0`), nicht im Skript. Namen aus `ip -br link`.

**Host-Key pinnen** (ohne Blind-Vertrauen):

```bash
ssh-keyscan -t ed25519 <proxmox-ip> > /tmp/hk
ssh-keygen -lf /tmp/hk                      # mit dem Fingerprint vom Server vergleichen!
install -m 600 /tmp/hk ~/.config/homeserver/known_hosts
```

Der Host-Key ist auf die **in `host =` eingetragene Adresse** gepinnt. Wechselst du auf einen Namen,
musst du neu pinnen.

## Schnittstelle: `nic0` oder `vmbr0`

Gemessen auf dem Server: `nic0` ≈ 9–14 kB/s, `vmbr0` ≈ 1 kB/s.

- `nic0` = gesamter Verkehr auf dem Kabel (Host + alle VMs/Container, auch qBittorrent). Entspricht
  `netin`/`netout` der Proxmox-API. **Standard.**
- `vmbr0` = nur Verkehr, der den Host selbst betrifft (Gast-Verkehr läuft an der Bridge-Schnittstelle vorbei).

`main_iface` in `[ssh]` wählt die Hauptanzeige; das Popup zeigt eine weitere gemeldete Schnittstelle als Zusatzzeile.

## Warum SSH statt Proxmox-API?

Getestet: `/nodes/<node>/status` hat keine Netzwerkwerte, `/nodes/<node>/network` nur die Konfiguration,
`/nodes/<node>/rrddata` nur **60-s-Mittelwerte** (jüngster Punkt war 51 s alt) und keine Wahl der Schnittstelle.
Für „wie schnell jetzt" taugt das nicht. SSH liefert echte Zähler im Sekundentakt. Dafür ist ein
zusätzlicher (stark eingeschränkter) Zugang zum Host nötig. Ein Proxmox-API-Token wird **nicht** gebraucht.

## Sicherheit

- Der Key kann ausschließlich den Agenten ausführen (`restrict` + `command=`). Agent, Verzeichnis,
  `agent.conf` und `authorized_keys` gehören root; der Benutzer `traffic` hat kein Passwort und keine Rechte.
- Befehle an den Server nur über eine **feste Whitelist** im Agenten: exakte Namen, Argumente nur als
  harmlose Tokens, keine Shell-Auslegung, keine freien Argumente. Alles andere → `denied`.
- Alles, was root braucht (ab Phase 1), erledigt ein eigener Dienst mit festen Befehlen ohne Eingabe von
  außen. Es gibt kein sudo für den Benutzer `traffic`.
- SSH mit `StrictHostKeyChecking=yes` und eigener `known_hosts`; Zugangsdaten nur in `config.ini` (600),
  nie im Code, nie im Repo (`.gitignore`). Secrets werden nie geloggt oder ausgegeben.
- qBittorrent-Login per POST (URL-kodiert), kein Proxy, kurze Timeouts (2,5 s), Antworten größenbegrenzt.
  Bei `https://` wird das Zertifikat **immer** geprüft; `ca_file` vertraut gezielt einer eigenen CA.
- Alle angezeigten Texte sind `Text.PlainText`; Zahlen werden validiert (nur endliche Werte ≥ 0).
- Keine Ports nach außen; Zugriff nur über LAN/VPN.

## HTTPS für qBittorrent (optional)

Standardmäßig spricht die WebUI HTTP. Über das WireGuard-VPN ist der Verkehr verschlüsselt; im Heimnetz
geht das Passwort im Klartext über das LAN. Wer das nicht will: in qBittorrent die WebUI-HTTPS-Option
aktivieren, `url = https://…` eintragen und bei eigener CA `ca_file = ~/.config/homeserver/qbittorrent-ca.pem`
setzen. Die TLS-Prüfung wird nie abgeschaltet; ein fehlender oder kaputter `ca_file` ist ein Konfigurationsfehler.

## Bekannte Einschränkungen

- **Login-Sperre:** Nach einem abgelehnten qBittorrent-Login wartet der Streamer 60 s (dann 120, 240 … bis
  15 min), weil qBittorrent standardmäßig nach 5 Fehlversuchen die IP für eine Stunde sperrt. Nach einer
  Passwort-Korrektur hilft `omarchy restart shell`.
- **Mehrere Bildschirme:** Jede Leiste lädt ihr eigenes Widget, also auch einen eigenen Streamer und eine
  eigene SSH-Verbindung. Beim Laptop allein ist es genau eine.
- Verifiziert wurde der Weg über das WireGuard-VPN; derselbe Pfad im Heim-LAN hängt nur an der Adresse in `host =`.

## Update-Festigkeit

Das Plugin liegt in `~/.config/omarchy/plugins/` und wird von `omarchy update` nicht angefasst. Verloren gehen
kann nur der **Leisten-Eintrag** (z. B. nach `omarchy refresh shell` oder `omarchy bar defaults`).
Der `post-update`-Hook setzt ihn nach Updates zurück; nach einem manuellen Refresh genügt `./install.sh --bar-only`.

## Tests

```bash
python3 -I -m unittest discover -s tests    # Protokoll, Rate, Fehler, Config, Backoff, Agent, Integration
node tests/test_model.js                    # Einheiten, Verlauf, Datenformat, Kopfzeilen-Status
bash tests/test_install.sh                  # Installer in einer Sandbox (Migration, fehlende shell.json)
python3 -I plugin/homeserver.py --count 5   # Streamer einzeln ansehen
```

## Fehlersuche

| Symptom | Ursache / Abhilfe |
|---|---|
| `offline · Host-Key stimmt nicht` | `known_hosts` fehlt oder passt nicht zur Adresse → neu pinnen (siehe oben) |
| `offline · Login abgelehnt` (SSH) | Key nicht in `authorized_keys`, oder Benutzer gesperrt (`usermod -p '*' traffic`) |
| `offline · Server-Agent veraltet` | Server noch auf dem alten `traffic-counters.sh` → `install-server.sh` ausführen |
| `offline · unerwartete Agent-Antwort` | Protokollversion passt nicht → Agent und Client auf denselben Stand bringen |
| `offline · Schnittstelle auf dem Server nicht gefunden` | `main_iface` fehlt in `/etc/homeserver/agent.conf` (`interfaces`) |
| `offline · Login abgelehnt` (qBittorrent) | Benutzer/Passwort in `config.ini`; bei Dauerfehlern sperrt qBittorrent die IP zeitweise |
| `offline · keine Antwort` | VPN aus? `ip route get <host>` prüfen |
| `offline · Host nicht erreichbar` | keine Route zum Host (VPN/Netz) |
| `offline · Verbindung abgelehnt` | falscher Port (qBittorrent-WebUI), Dienst gestoppt |
| `offline · TLS-Prüfung fehlgeschlagen` | Zertifikat/Hostname passt nicht → `ca_file` und `url` prüfen |
| `config.ini fehlt/unvollständig`, `CA-Datei nicht gefunden` | `./install.sh` bzw. `config.example` vergleichen |
| Widget fehlt in der Leiste | `./install.sh --bar-only`; `omarchy plugin list` muss `homeserver` zeigen |
| Änderung an `Panel.qml` ist nicht sichtbar | Die Shell behält QML im Speicher: `omarchy restart shell` |
| Gar nichts sichtbar | Läuft ein Fenster im Vollbild? Dann blendet Hyprland die Leiste aus |

Streamer von Hand prüfen: `python3 -I plugin/homeserver.py --count 5`.
Agent auf dem Server von Hand prüfen: `su -s /bin/sh traffic -c 'python3 /usr/local/lib/homeserver/agent.py --once'`.
Shell-Log: `quickshell log -p /usr/share/omarchy/shell | grep -i homeserver`.
Danach `pgrep -a ssh`: pro Leiste darf es genau eine Verbindung geben.

## Lizenz

MIT, siehe [LICENSE](LICENSE).
