# homeserver

Leisten-Widget für **Omarchy** (Quickshell-Leiste `omarchy-shell`, nicht Waybar), das den
Heimserver im Blick behält. Das Popup besteht aus Karten, eine pro Bereich; jede Karte fällt
einzeln aus, ohne die anderen zu stören.

| Karte | Quelle | Stand |
|---|---|---|
| Proxmox-Host: Download / Upload | SSH-Agent, liest Zählerstände | ✅ |
| qBittorrent: Download / Upload | WebUI-API `/api/v2/transfer/info` | ✅ |
| System: Last, RAM, CPU-Temperatur, Laufzeit | Agent liest `/proc`, `/sys` | ✅ |
| Gäste: VMs und Container mit Status | Collector (`pvesh`) → Datei → Agent | ✅ |
| Internet: Ping-Latenz und Paketverlust zu 1.1.1.1 | Agent startet unprivilegiertes `ping` | ✅ |
| Speicher: Füllstand je Storage (Warnung ab 85 %, kritisch ab 95 %) | Collector (`pvesm status`) | ✅ |
| SMART: Gesundheit je Platte (Warnpunkt nur bei „FEHLER“; Standby wird nicht geweckt) | Collector (`smartctl`) | ✅ |
| Speedtest auf dem Server: alle 3 h und per Klick, Verlauf 30 Tage | eigener Dienst als `hs-speedtest` | ✅ |
| Aktionen: qBittorrent Alt-Speed und Pause/Fortsetzen, Wake-on-LAN, WireGuard-Status | | geplant (Phase 5, siehe unten) |

- **Leiste:** nur das Server-Symbol (ausgegraut, wenn der Host offline ist). Ein kleiner roter Punkt daneben warnt,
  wenn etwas auffällt: Ping-Verlust über 2 % oder Latenz über 100 ms, ein aktiver Speicher ab 85 % Füllstand oder
  eine Platte mit SMART „FEHLER“. Karten, die offline sind, lösen keine Warnung aus (sie zeigen ihren Grund im Popup).
- **Tooltip:** die aktuellen Werte von Proxmox-Host und qBittorrent.
- **Klick:** Popup (wie das WLAN-Panel) mit einer Karte je Bereich: Status je Quelle samt Grund, Live-Werte,
  Verläufe, bei Speedtest ein Knopf „Jetzt messen“. Das Popup ist höher als kleine Bildschirme und deshalb
  scrollbar (Mausrad); es beginnt beim Öffnen immer oben. Schließen mit `Esc` oder Klick daneben.
- Einheiten skalieren automatisch (B/s, kB/s, MB/s, GB/s; SI, 1 kB = 1000 B).

## Aufbau

```
plugin/homeserver.py        Einstieg des Streamers (python3 -I), startet das Paket hs/
plugin/hs/                  Streamer: Protokoll, Config, SSH-Agent-Quelle, qBittorrent-Quelle (nur stdlib)
plugin/*.qml, Model.js, i18n/   Oberfläche (Quickshell/QML): eine Karte je Datei, Texte de/en
server/agent.py             Server-Agent (läuft als Benutzer traffic, per command= im SSH-Key)
server/collector.py         root-eigener Dienst mit festen Befehlen, schreibt nach /run/homeserver/
server/homeserver-collector.service   systemd-Unit (gehärtet) dazu
server/speedtest-run.py     Speedtest-Runner (läuft als hs-speedtest), Verlauf und Zustand
server/homeserver-speedtest.{service,timer,path}   Dienst, Zeitplan (3 h) und Klick-Trigger
server/homeserver-tmpfiles.conf    Trigger-Verzeichnis und Speedtest-Datenverzeichnis
server/agent.conf.example   Server-Konfiguration (welche Schnittstellen gesendet werden)
server/install-server.sh    einmalig auf dem Proxmox-Host: Benutzer, Agent, Key-Bindung
server/diagnose.sh          nur lesend: Fakten über den Server sammeln
server/audit.sh             nur lesend (als root): Neustart-Festigkeit, Rechte, Key-Bindung, Sandboxing, offene Ports
server/make-paste-block.sh  erzeugt einen Einfüge-Block für die Server-Einrichtung per Web-Shell
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
SYS <uptime> <ncpu> <l1> <l5> <l15> <mem_total_kB> <mem_avail_kB> <temp_C|->     alle 5 s
BEGIN GUESTS <alter_s>                   alle 30 s, atomarer Block
G <vmid> <qemu|lxc> <running|stopped|other> <name>
END GUESTS                               (oder: MISSING GUESTS, wenn der Collector nichts liefert)
BEGIN STORAGE <alter_s> / S <name> <typ> <active|inactive> <total_kB> <used_kB> / END STORAGE     alle 30 s
BEGIN SMART <alter_s> / D <gerät> <passed|failed|standby|unknown> <temp_C|-> / END SMART         alle 30 s (Collector prüft alle 10 min)
SPEEDSTATE <running|idle|notinstalled|unavailable> <cooldown_s> <1|0|->     alle 5 s
BEGIN SPEED 0 / R <alter_s> <down_kbit> <up_kbit> <ping_ms> / END SPEED     bei neuem Ergebnis, sonst alle 5 min
CMDRESULT <name> <ok|busy|cooldown|notinstalled|unavailable|failed|denied>   Antwort auf einen Befehl
PING <uptime> <ziel-ip> <gesendet> <empfangen> <avg_ms|->     alle 5 s (3 Pakete); PINGERR unavailable|failed
```

Langsame Daten werden seltener gesendet als die Netzwerkzähler. Jede Karte hat ihre eigene
Frische-Grenze und fällt einzeln aus: Fehlt der Collector, wird nur „Gäste" offline („Collector auf dem
Server liefert nicht"), Netzwerk und System laufen weiter. Bricht die Verbindung ab, gehen alle Karten sofort offline.

Der Client rechnet Raten selbst aus der **Server-Uptime**-Differenz (nicht aus der lokalen Uhr, damit
Netzwerk-Jitter nichts verfälscht). Bei Zähler-Reset, rückwärts laufender Zeit oder großer Lücke wird
neu aufgesetzt. Ein alter Agent (`traffic-counters.sh`) wird erkannt und als „Server-Agent veraltet"
gemeldet. Befehle vom Client zum Server laufen über **stdin derselben Verbindung** und müssen auf einer
festen Whitelist stehen (aktuell nur `speedtest`, ohne Argumente). Schließt der Client, meldet der Kernel EOF auf stdin, der Agent
beendet sich und damit die Sitzung, auch wenn der Client hart abgeschossen wird.

## Installation

Voraussetzungen: Omarchy 4.x, `python3`, `ssh` (alles ohnehin vorhanden).

```bash
git clone https://github.com/florianerler/homeserver ~/Work/homeserver && cd ~/Work/homeserver
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

**Ohne SSH-Zugang als root** (z. B. nur Proxmox-Web-Shell): `server/make-paste-block.sh` erzeugt einen einzigen
Block zum Einfügen, der die Dateien per Heredoc anlegt und den Installer startet:

```bash
server/make-paste-block.sh ~/.config/homeserver/id_ed25519.pub | wl-copy    # dann in der Web-Shell einfügen
```

Der Installer ist **wiederholbar** (jede neue Version wird mit demselben Befehl eingespielt). Er legt die Benutzer
`traffic` (Agent) und `hs-speedtest` (Speedtest) an, installiert Agent, Collector und Runner nach
`/usr/local/lib/homeserver/` (root-eigen), richtet die systemd-Units und das Trigger-Verzeichnis ein,
schreibt `/etc/homeserver/agent.conf` (nur wenn sie fehlt) und bindet den Key mit
`restrict,command="/usr/local/lib/homeserver/agent.py"` an den Agenten: kein Terminal, keine Weiterleitungen,
nur dieses Programm. Am Ende zeigt er den Zustand von Collector (Gäste, Speicher, SMART), Speedtest-Programm und Ping,
macht einen Selbsttest als `traffic` und gibt den Host-Key-Fingerprint aus.

**Voraussetzungen auf dem Server** (der Installer installiert **keine** Pakete): Proxmox mit `pvesh`/`pvesm`,
`python3`, `ping`; für die SMART-Karte `smartctl` (Paket `smartmontools`); für den Speedtest `speedtest-cli`
oder die Ookla-CLI. Fehlt etwas, fällt nur die jeweilige Karte aus.

**Ping-Ziel:** `ping_target = 1.1.1.1` in `/etc/homeserver/agent.conf`. Erlaubt ist nur eine IP-Adresse (kein Hostname, kein DNS);
leer schaltet den Ping ab. Der Agent ruft ein festes `ping -n -c 3 -i 0.3 -W 1 <ip>` ohne Shell und ohne root auf.

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

## Speedtest

Der Test läuft **auf dem Server** (misst dessen Internetanschluss, nicht den Laptop) als eigener Dienst
`homeserver-speedtest.service` unter dem Systembenutzer `hs-speedtest` (ohne Login, ohne Shell, ohne sudo),
gehärtet mit `ProtectSystem=strict`, `NoNewPrivileges`, `PrivateTmp`, Speicher- und Task-Limit.

- **Zeitplan:** `homeserver-speedtest.timer` alle 3 Stunden (mit kleiner Zufallsverzögerung).
- **Klick auf „Jetzt messen“:** Der Streamer sendet den Befehl `speedtest` über die bestehende SSH-Verbindung.
  Der Agent prüft ihn gegen seine Whitelist und legt nur eine Trigger-Datei in `/run/homeserver-trigger/` an
  (Verzeichnis gehört `traffic`, Modus 0700). Eine `.path`-Unit startet daraufhin den Dienst. Es gibt keinen
  Zugriff auf Befehle oder Argumente von außen.
- **Schutz vor Dauerfeuer:** `flock` (nur ein Test gleichzeitig) und ein Mindestabstand von 10 Minuten (nach einem Fehler 1 Minute), den
  Runner **und** Agent prüfen; der Knopf zeigt „läuft…“ bzw. „wieder möglich in …“.
- **Verlauf:** `/var/lib/homeserver-speedtest/history.jsonl`, 30 Tage, höchstens 300 Messungen. Misslungene Läufe
  landen nicht im Verlauf, nur im Zustand („letzter Lauf fehlgeschlagen“).
- **Programm:** wird vom Installer **nicht** installiert. Unterstützt werden das Debian-Paket `speedtest-cli`
  (`apt install speedtest-cli`, aus der Distribution, keine Fremd-Quelle) und die Ookla-CLI `speedtest`. Der Runner
  erkennt die Art über `<programm> --version`, denn `/usr/bin/speedtest` ist beim Debian-Paket ein eigenes Skript von
  `speedtest-cli` und nicht die Ookla-CLI. Fehlt beides,
  sagt die Karte „Programm auf dem Server nicht installiert“. Beachte: Ein Test verbraucht je nach Anschluss einige
  hundert MB Datenvolumen; bei 8 Läufen am Tag also schnell mehrere GB im Monat.

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
- Alles, was root braucht, erledigt der **Collector** (`homeserver-collector.service`): feste Befehle ohne
  Shell und mit Zeitlimit (`pvesh get /cluster/resources --type vm`, `pvesm status`, `smartctl --scan` und `smartctl -j -H -A -n standby -d <typ> <gerät>`;
  Geräte und `-d`-Typen stammen aus einer Whitelist, `smartctl` darf Platten im Standby nicht wecken), keine Eingabe von außen,
  Ausgabe wird validiert und bereinigt (Gastnamen nur `A-Za-z0-9._-`), Ergebnis als Datei in `/run/homeserver/`
  (root schreibt, alle lesen). Der Dienst ist per systemd gehärtet (`NoNewPrivileges`, `ProtectSystem`,
  `ProtectHome`, Speicher-/Task-Limit). Es gibt kein sudo für den Benutzer `traffic`.
- SSH mit `StrictHostKeyChecking=yes` und eigener `known_hosts`; Zugangsdaten nur in `config.ini` (600),
  nie im Code, nie im Repo (`.gitignore`). Secrets werden nie geloggt oder ausgegeben.
- qBittorrent-Login per POST (URL-kodiert), kein Proxy, kurze Timeouts (2,5 s), Antworten größenbegrenzt.
  Bei `https://` wird das Zertifikat **immer** geprüft; `ca_file` vertraut gezielt einer eigenen CA.
- Der Speedtest-Dienst läuft als eigener Systembenutzer ohne Login und Shell, `ProtectSystem=strict`; der einzige
  Root-Schritt ist das Entfernen der Trigger-Datei (`ExecStartPre=+`). Das Trigger-Verzeichnis gehört `traffic` (0700).
  Die Oberfläche kann dem Streamer nur das Wort `speedtest` schicken; der Streamer filtert, der Agent prüft erneut.
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

- **Datenvolumen:** Ein Speedtest verbraucht je nach Anschluss einige hundert MB; bei 8 Läufen am Tag also mehrere GB im Monat.
- **SMART und Standby:** Eine Platte im Standby wird nicht geweckt und zeigt „Standby (nicht geweckt)“, bis sie von selbst aufwacht.
- **Kleine Bildschirme:** Das Popup ist höher als der Bildschirm und wird gescrollt.

## Geplant (Phase 5: Aktionen)

- **qBittorrent:** Alt-Speed-Limits ein/aus und „alle pausieren / fortsetzen“ (API-Version wird erkannt:
  5.x `stop/start`, 4.x `pause/resume`).
- **Wake-on-LAN:** Gaming-PC wecken. Die MAC-Adresse steht **nur serverseitig** in `agent.conf` mit einem Alias;
  der Client sendet nur `wol <alias>` (kein freies Argument, keine MAC vom Client).
- **WireGuard-Status:** Route zum Host prüfen und ob die Schnittstelle vom Typ `wireguard` ist.

## Update-Festigkeit

Das Plugin liegt in `~/.config/omarchy/plugins/` und wird von `omarchy update` nicht angefasst. Verloren gehen
kann nur der **Leisten-Eintrag** (z. B. nach `omarchy refresh shell` oder `omarchy bar defaults`).
Der `post-update`-Hook setzt ihn nach Updates zurück; nach einem manuellen Refresh genügt `./install.sh --bar-only`.

## Tests

```bash
python3 -W error::ResourceWarning -I -m unittest discover -s tests   # 140+ Tests: Protokoll, Rate, Fehler, Config, Backoff,
                                            # Agent, Collector (feindliche Eingaben), Speedtest-Runner, Units, Integration
node tests/test_model.js                    # Einheiten, Verlauf, Datenformat, Warnungen, Kopfzeilen-Status
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
| Gäste: `Collector auf dem Server liefert nicht` | `systemctl status homeserver-collector`, `journalctl -u homeserver-collector -n 20` auf dem Server |
| Speicher/SMART: `Collector auf dem Server liefert nicht` | `journalctl -u homeserver-collector`; `smartctl` installiert? (`apt install smartmontools`, nur auf Wunsch) |
| Speedtest: `Programm auf dem Server nicht installiert` | `apt install speedtest-cli` (nur wenn gewünscht), danach `systemctl start homeserver-speedtest` |
| Speedtest: Knopf bleibt grau | läuft gerade oder Mindestabstand (10 min, nach Fehler 1 min); `journalctl -u homeserver-speedtest -n 20` |
| Speedtest: Journal zeigt `Fehler: …` | Typ und letzte Fehlerzeile des Programms; zum Gegenprüfen: `runuser -u hs-speedtest -- speedtest-cli --simple` |
| Internet: `ping auf dem Server nicht nutzbar` | als `traffic` testen: `su -s /bin/sh traffic -c 'ping -c1 1.1.1.1'` (Rechte/`ping_group_range`) |
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
