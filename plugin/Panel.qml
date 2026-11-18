import QtQuick
import Quickshell
import Quickshell.Io
import qs.Ui
import qs.Commons
import qs.Commons as Commons
import "Model.js" as Model

// Heimserver-Traffic: Leistenanzeige (Variante A: Host-Raten), Tooltip mit allen
// vier Werten und ein Popup mit Verlauf. Die Daten liefert homeserver_traffic.py
// als JSON-Zeilen (eine pro Sekunde); die Leiste startet es einmal und liest mit.
Panel {
  id: root
  moduleName: "homeserver.traffic"
  ipcTarget: "homeserver.traffic"

  readonly property int historyLength: 90

  // Dieser Ordner (Symlink-Pfad), darin liegt auch der Streamer.
  readonly property string pluginDir: decodeURIComponent(String(Qt.resolvedUrl(".")).replace(/^file:\/\//, "")).replace(/\/$/, "")

  Commons.Translations { id: strings; directory: Qt.resolvedUrl("i18n") }

  property var host: ({ ok: false, state: "connecting", error: "connecting", rx: 0, tx: 0, ifaces: {} })
  property var qbt: ({ ok: false, state: "connecting", error: "connecting", rx: 0, tx: 0, ifaces: {} })
  property var hostRxHistory: []
  property var hostTxHistory: []
  property var qbtRxHistory: []
  property var qbtTxHistory: []

  function fmt(v) { return Model.format(v, Commons.I18n.formatNumber) }

  function ingest(line) {
    var sample = Model.parseSample(line)
    if (!sample) return
    host = sample.host
    qbt = sample.qbt
    // Offline-Zeitraeume als 0 im Verlauf, damit die Zeitachse weiterlaeuft.
    hostRxHistory = Model.push(hostRxHistory, host.ok ? host.rx : 0, historyLength)
    hostTxHistory = Model.push(hostTxHistory, host.ok ? host.tx : 0, historyLength)
    qbtRxHistory = Model.push(qbtRxHistory, qbt.ok ? qbt.rx : 0, historyLength)
    qbtTxHistory = Model.push(qbtTxHistory, qbt.ok ? qbt.tx : 0, historyLength)
  }

  // Statustext einer Quelle: online / verbindet / offline mit kurzem Grund.
  function statusOf(src) {
    if (src.ok) return strings.t("state.online")
    if (src.state === "connecting") return strings.t("state.connecting")
    var reason = strings.t("err." + src.error, strings.t("err.generic"))
    return strings.t("state.offline") + " · " + reason
  }

  function rateLine(src) {
    return src.ok ? "↓ " + fmt(src.rx) + "   ↑ " + fmt(src.tx) : "–"
  }

  readonly property string iface: host.iface || "nic0"
  readonly property string tooltip: [
    strings.t("host") + " (" + iface + ")",
    "  " + rateLine(host),
    strings.t("qbt"),
    "  " + rateLine(qbt)
  ].join("\n")

  readonly property string barLabel: host.ok
    ? "↓ " + fmt(host.rx) + "  ↑ " + fmt(host.tx)
    : (host.state === "connecting" ? "…" : "–")

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  // ---- Streamer ------------------------------------------------------------
  Process {
    id: streamer
    command: ["python3", "-I", root.pluginDir + "/homeserver_traffic.py"]
    running: true
    stdout: SplitParser { onRead: function(line) { root.ingest(line) } }
    // Endet er (Absturz), wird er nach kurzer Pause neu gestartet.
    onExited: restartTimer.restart()
  }
  Timer {
    id: restartTimer
    interval: 3000
    onTriggered: streamer.running = true
  }

  // ---- Leiste --------------------------------------------------------------
  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "󰒍"
    labelText: root.barLabel
    tooltipText: root.tooltip
    dimmed: !root.host.ok
    onPressed: function(b) { root.toggle() }
  }

  // ---- Popup ---------------------------------------------------------------
  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: panel.loadedContent
    contentWidth: panel.fittedContentWidth(Style.space(360))
    contentHeight: panel.fittedContentHeight(panel.loadedContentHeight, Style.space(560))

    content: PanelKeyCatcher {
      implicitHeight: panelColumn.implicitHeight
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Column {
        id: panelColumn
        width: parent.width
        spacing: Style.space(14)

        // Kopf: Icon, Titel, Gesamtstatus
        Item {
          width: parent.width
          implicitHeight: Math.max(heroIcon.implicitHeight, heroLabels.implicitHeight)
          height: implicitHeight

          Text {
            id: heroIcon
            textFormat: Text.PlainText
            text: "󰒍"
            color: root.bar ? root.bar.foreground : Commons.Color.foreground
            font.family: root.bar ? root.bar.fontFamily : Style.font.family
            font.pixelSize: Style.font.display
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
          }
          Column {
            id: heroLabels
            anchors.left: heroIcon.right
            anchors.leftMargin: Style.space(14)
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(2)
            Text {
              textFormat: Text.PlainText
              text: strings.t("title")
              color: Commons.Color.foreground
              font.family: Style.font.family
              font.pixelSize: Style.font.title
              font.bold: true
            }
            Text {
              textFormat: Text.PlainText
              text: root.host.ok && root.qbt.ok ? strings.t("hero.bothOnline") : strings.t("hero.someOffline")
              color: Qt.darker(Commons.Color.foreground, 1.4)
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
          }
        }

        SourceCard {
          width: parent.width
          title: strings.t("host") + " · " + root.iface
          statusText: root.statusOf(root.host)
          online: root.host.ok
          rx: root.host.rx
          tx: root.host.tx
          rxHistory: root.hostRxHistory
          txHistory: root.hostTxHistory
          window: root.historyLength
          downLabel: strings.t("download")
          upLabel: strings.t("upload")
          peakText: strings.t("note.peak").arg(root.historyLength).arg(root.fmt(Model.peak(root.hostRxHistory, root.hostTxHistory)))
          extraText: {
            var other = root.iface === "nic0" ? "vmbr0" : "nic0"
            var f = root.host.ifaces ? root.host.ifaces[other] : null
            return root.host.ok && f
              ? strings.t("note.hostOnly").arg(other).arg(root.fmt(f.rx)).arg(root.fmt(f.tx))
              : ""
          }
        }

        PanelSeparator { width: parent.width }

        SourceCard {
          width: parent.width
          title: strings.t("qbt")
          statusText: root.statusOf(root.qbt)
          online: root.qbt.ok
          rx: root.qbt.rx
          tx: root.qbt.tx
          rxHistory: root.qbtRxHistory
          txHistory: root.qbtTxHistory
          window: root.historyLength
          downLabel: strings.t("download")
          upLabel: strings.t("upload")
          peakText: strings.t("note.peak").arg(root.historyLength).arg(root.fmt(Model.peak(root.qbtRxHistory, root.qbtTxHistory)))
        }
      }
    }
  }
}
