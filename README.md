# homeserver-traffic

Leisten-Widget für **Omarchy** (Quickshell-Leiste `omarchy-shell`, nicht Waybar), das den
Netzwerkverkehr des Heimservers zeigt:

| Wert | Quelle |
|---|---|
| Proxmox-Host: Download / Upload | SSH-Dauerverbindung mit eingeschränktem Key, liest nur Zählerstände |
| qBittorrent: Download / Upload | WebUI-API `/api/v2/transfer/info` |

- **Leiste:** nur das Server-Symbol (ausgegraut, wenn der Host offline ist). Die Werte gibt es im Tooltip und im Popup.
- **Tooltip:** alle vier Werte.
- **Klick:** Popup (wie das WLAN-Panel) mit Live-Werten, 90-s-Verlauf, Erreichbarkeitsstatus
  beider Quellen und der zweiten Schnittstelle als Zusatzzeile. Schließen mit `Esc` oder Klick daneben.
- Einheiten skalieren automatisch (B/s, kB/s, MB/s, GB/s; SI, 1 kB = 1000 B).
- Fällt eine Quelle aus, bleibt die andere aktiv; der Grund steht im Popup.

## Aufbau

```
plugin/homeserver_traffic.py   Streamer: SSH + qBittorrent -> JSON-Zeile pro Sekunde (nur stdlib)
plugin/Panel.qml, SourceCard.qml, Sparkline.qml, Model.js, i18n/   Oberfläche (Quickshell/QML)
server/setup-proxmox.sh        einmalig auf dem Proxmox-Host: Benutzer + eingeschränkter Key
hooks/post-update.sh           stellt das Widget nach Omarchy-Updates wieder her
install.sh                     Installation (idempotent)
tests/                         Unit-Tests (Python + Node)
```

Der Streamer läuft **dauerhaft** (ein Prozess, eine SSH-Verbindung) statt bei jedem Intervall neu
zu starten. So bleiben Vorwert und Verlauf im Speicher und es gibt keinen Verbindungsaufbau pro
Messung. Die Leiste startet ihn und liest seine stdout-Zeilen. Beendet die Leiste ihn (auch beim
Hot-Reload), erkennt er das und räumt sein `ssh`-Kind auf (`SIGTERM`, `BrokenPipe`, `PDEATHSIG`).

## Installation

Voraussetzungen: Omarchy 4.x, `python3`, `ssh` (alles ohnehin vorhanden).

```bash
git clone <dieses-repo> ~/Work/homeserver-traffic && cd ~/Work/homeserver-traffic
./install.sh
```

Der Installer legt `~/.config/homeserver-traffic/config.ini` aus `config.example` an (chmod 600),
verlinkt das Plugin nach `~/.config/omarchy/plugins/`, trägt es per `omarchy bar put` in die Leiste ein
(mit Backup von `shell.json`) und installiert den Update-Hook.

### Schritt 1: Zugangsdaten

`~/.config/homeserver-traffic/config.ini` ausfüllen (Vorlage: `config.example`).
Ein `%` im Passwort ist erlaubt.

### Schritt 2: SSH-Key und Proxmox-Seite

```bash
# Schlüssel erzeugen (ohne Passphrase, die Leiste braucht ihn unbeaufsichtigt)
ssh-keygen -t ed25519 -N "" -C homeserver-traffic -f ~/.config/homeserver-traffic/id_ed25519

# Auf dem Proxmox-Host als root (Skript vorher dorthin kopieren):
sh setup-proxmox.sh "$(cat id_ed25519.pub)"
```

Das Skript legt den Benutzer `traffic` an. Dessen Key hat `restrict,command="/usr/local/bin/traffic-counters.sh"`:
kein Terminal, keine Weiterleitungen, nur dieses root-eigene Skript, das jede Sekunde die Zähler ausgibt.

**Host-Key pinnen** (ohne Blind-Vertrauen). Den Fingerprint zeigt `setup-proxmox.sh` am Ende an:

```bash
ssh-keyscan -t ed25519 <proxmox-ip> > /tmp/hk
ssh-keygen -lf /tmp/hk                      # mit dem Fingerprint vom Server vergleichen!
install -m 600 /tmp/hk ~/.config/homeserver-traffic/known_hosts
```

Der Host-Key ist auf die **in `host =` eingetragene Adresse** gepinnt. Wechselst du auf einen Namen,
musst du neu pinnen.

## Schnittstelle: `nic0` oder `vmbr0`

Gemessen auf dem Server: `nic0` ≈ 9–14 kB/s, `vmbr0` ≈ 1 kB/s.

- `nic0` = gesamter Verkehr auf dem Kabel (Host + alle VMs/Container, auch qBittorrent). Entspricht
  `netin`/`netout` der Proxmox-API. **Standard.**
- `vmbr0` = nur Verkehr, der den Host selbst betrifft (Gast-Verkehr läuft an der Bridge-Schnittstelle vorbei).

`main_iface` in `[ssh]` wählt die Hauptanzeige; das Popup zeigt die andere als Zusatzzeile.

## Warum SSH statt Proxmox-API?

Getestet: `/nodes/<node>/status` hat keine Netzwerkwerte, `/nodes/<node>/network` nur die Konfiguration,
`/nodes/<node>/rrddata` nur **60-s-Mittelwerte** (jüngster Punkt war 51 s alt) und keine Wahl der Schnittstelle.
Für „wie schnell jetzt" taugt das nicht. SSH liefert echte Zähler im Sekundentakt. Dafür ist ein
zusätzlicher (stark eingeschränkter) Zugang zum Host nötig. Ein Proxmox-API-Token wird **nicht** gebraucht.

## Sicherheit

- Der Key kann ausschließlich `traffic-counters.sh` ausführen (`restrict` + `command=`); `authorized_keys` und
  Skript gehören root, der Benutzer `traffic` hat kein Passwort und keine Rechte.
- SSH mit `StrictHostKeyChecking=yes` und eigener `known_hosts`; Zugangsdaten nur in `config.ini` (600),
  nie im Code, nie im Repo (`.gitignore`). Secrets werden nie geloggt oder ausgegeben.
- qBittorrent-Login per POST (URL-kodiert), kein Proxy, kurze Timeouts (2,5 s), Antworten größenbegrenzt.
- Alle angezeigten Texte sind `Text.PlainText`; Zahlen werden validiert (nur endliche Werte ≥ 0).
- Keine Ports nach außen; Zugriff nur über LAN/VPN.

## Bekannte Einschränkungen

- **qBittorrent läuft per HTTP.** Über das WireGuard-VPN ist der Verkehr verschlüsselt. Im Heimnetz geht das
  Passwort im Klartext über das LAN. Wer das nicht will, aktiviert in qBittorrent die WebUI-HTTPS-Option und
  trägt `https://…` in `config.ini` ein (die CA dann in `known_hosts`-Manier gezielt vertrauen, nicht
  die TLS-Prüfung abschalten).
- **Login-Sperre:** Nach einem abgelehnten qBittorrent-Login wartet der Streamer 60 s (dann 120, 240 … bis
  15 min), weil qBittorrent standardmäßig nach 5 Fehlversuchen die IP für eine Stunde sperrt.
  Passwort-Korrektur wirkt daher bis zu 15 min verzögert; schneller: Leiste neu laden
  (`omarchy-shell shell rescanPlugins` startet den Streamer neu).
- **Mehrere Bildschirme:** Jede Leiste lädt ihr eigenes Widget, also auch einen eigenen Streamer und eine
  eigene SSH-Verbindung. Beim Laptop allein ist es genau eine.
- **Ohne Proxmox-API:** Der Host-Key-Pin (`known_hosts`) ersetzt das CA-Pinning; die Proxmox-API wird nicht
  verwendet, ein API-Token ist daher nicht nötig.
- Verifiziert wurde der Weg über das WireGuard-VPN; derselbe Pfad im Heim-LAN hängt nur an der Adresse in `host =`.

## Update-Festigkeit

Das Plugin liegt in `~/.config/omarchy/plugins/` und wird von `omarchy update` nicht angefasst. Verloren gehen
kann nur der **Leisten-Eintrag** (z. B. nach `omarchy refresh shell` oder `omarchy bar defaults`).
Der `post-update`-Hook setzt ihn nach Updates zurück; nach einem manuellen Refresh genügt `./install.sh`.

## Tests

```bash
python3 -I -m unittest discover -s tests    # Rate, Zähler-Reset, Parser, Config (inkl. '%'-Passwort)
node tests/test_model.js                    # Einheiten-Skalierung, Verlauf, Zeilen-Validierung
python3 plugin/homeserver_traffic.py --count 5   # Streamer einzeln ansehen
```

## Fehlersuche

| Symptom | Ursache / Abhilfe |
|---|---|
| Popup: `offline · Host-Key stimmt nicht` | `known_hosts` fehlt oder passt nicht zur Adresse → neu pinnen (siehe oben) |
| `offline · Login abgelehnt` (SSH) | Key nicht in `authorized_keys`, oder Benutzer gesperrt (`usermod -p '*' traffic`) |
| `offline · Login abgelehnt` (qBittorrent) | Benutzer/Passwort in `config.ini`; bei Dauerfehlern sperrt qBittorrent die IP zeitweise |
| `offline · keine Antwort` | VPN aus? `ip route get <host>` prüfen |
| `offline · Verbindung abgelehnt` | falscher Port (qBittorrent-WebUI), Dienst gestoppt |
| `config.ini fehlt/unvollständig` | `./install.sh` bzw. `config.example` vergleichen |
| Widget fehlt in der Leiste | `./install.sh`; `omarchy plugin list` muss `homeserver.traffic` zeigen |
| Änderung an `Panel.qml` ist nicht sichtbar | Die Shell behält QML im Speicher: `omarchy restart shell` (ein Rescan genügt nicht) |
| Gar nichts sichtbar | Läuft ein Fenster im Vollbild? Dann blendet Hyprland die Leiste aus |

Streamer von Hand prüfen: `python3 plugin/homeserver_traffic.py --count 5`.
Shell-Log: `quickshell log -p /usr/share/omarchy/shell | grep -i homeserver`.
Danach `pgrep -a ssh`: pro Leiste darf es genau eine Verbindung geben.
