import QtQuick
import Quickshell
import Quickshell.Io
import qs.Ui
import qs.Commons
import qs.Commons as Commons
import "Model.js" as Model

// Homeserver: Server-Icon in der Leiste, Tooltip und ein Popup mit Karten. Die Daten
// liefert homeserver.py als JSON-Snapshots (einer pro Sekunde); die Leiste startet es
// einmal und liest mit.
Panel {
  id: root
  moduleName: "homeserver"
  ipcTarget: "homeserver"

  readonly property int historyLength: 90

  // Dieser Ordner (Symlink-Pfad), darin liegt auch der Streamer.
  readonly property string pluginDir: decodeURIComponent(String(Qt.resolvedUrl(".")).replace(/^file:\/\//, "")).replace(/\/$/, "")

  Commons.Translations { id: strings; directory: Qt.resolvedUrl("i18n") }

  property var net: ({ ok: false, state: "connecting", error: "connecting", rx: 0, tx: 0, ifaces: {} })
  property var qbt: ({ ok: false, state: "connecting", error: "connecting", rx: 0, tx: 0, ifaces: {} })
  property var system: ({ ok: false, state: "connecting", error: "connecting" })
  property var guests: ({ ok: false, state: "connecting", error: "connecting" })
  property var netRxHistory: []
  property var netTxHistory: []
  property var qbtRxHistory: []
  property var qbtTxHistory: []

  function fmt(v) { return Model.format(v, Commons.I18n.formatNumber) }

  function ingest(line) {
    var sample = Model.parseSample(line)
    if (!sample) return
    net = sample.net
    qbt = sample.qbt
    if (sample.system) system = sample.system
    if (sample.guests) guests = sample.guests
    // Offline-Zeitraeume als 0 im Verlauf, damit die Zeitachse weiterlaeuft.
    netRxHistory = Model.push(netRxHistory, net.ok ? net.rx : 0, historyLength)
    netTxHistory = Model.push(netTxHistory, net.ok ? net.tx : 0, historyLength)
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

  // Gesamtstatus fuer die Kopfzeile (alle Karten).
  readonly property var summary: Model.summarize({ net: net, qbt: qbt, system: system, guests: guests })
  readonly property string summaryText: {
    var s = summary
    if (s.kind === "allOk") return strings.t("hero.allOk")
    if (s.kind === "connecting") return strings.t("hero.connecting")
    if (s.kind === "allOffline") return strings.t("hero.allOffline")
    return strings.tn("hero.someOffline", s.offline).arg(Commons.I18n.formatNumber(s.offline, 0)).arg(Commons.I18n.formatNumber(s.total, 0))
  }

  readonly property string iface: net.iface || "nic0"
  readonly property string tooltip: [
    strings.t("host") + " (" + iface + ")",
    "  " + rateLine(net),
    strings.t("qbt"),
    "  " + rateLine(qbt)
  ].join("\n")

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  // ---- Streamer ------------------------------------------------------------
  Process {
    id: streamer
    command: ["python3", "-I", root.pluginDir + "/homeserver.py"]
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
    tooltipText: root.tooltip
    dimmed: !root.net.ok
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
    contentHeight: panel.fittedContentHeight(panel.loadedContentHeight, Style.space(1000))

    content: PanelKeyCatcher {
      implicitHeight: Math.min(panelColumn.implicitHeight, Style.space(1000))
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Flickable {
        id: scroller
        anchors.fill: parent
        clip: true
        contentWidth: width
        contentHeight: panelColumn.implicitHeight
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick

      Column {
        id: panelColumn
        width: scroller.width
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
              text: root.summaryText
              color: Qt.darker(Commons.Color.foreground, 1.4)
              font.family: Style.font.family
              font.pixelSize: Style.font.caption
            }
          }
        }

        SourceCard {
          width: parent.width
          title: strings.t("host") + " · " + root.iface
          statusText: root.statusOf(root.net)
          online: root.net.ok
          rx: root.net.rx
          tx: root.net.tx
          rxHistory: root.netRxHistory
          txHistory: root.netTxHistory
          window: root.historyLength
          downLabel: strings.t("download")
          upLabel: strings.t("upload")
          peakText: strings.t("note.peak").arg(root.historyLength).arg(root.fmt(Model.peak(root.netRxHistory, root.netTxHistory)))
          extraText: {
            // Erste andere vom Server gemeldete Schnittstelle (konfigurierbar, nicht fest verdrahtet).
            var other = "", f = null
            for (var k in root.net.ifaces) {
              if (k !== root.iface) { other = k; f = root.net.ifaces[k]; break }
            }
            return root.net.ok && f
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

        PanelSeparator { width: parent.width }

        SystemCard {
          width: parent.width
          info: root.system
          tr: strings
          statusText: root.statusOf(root.system)
        }

        PanelSeparator { width: parent.width }

        GuestsCard {
          width: parent.width
          info: root.guests
          tr: strings
          statusText: root.statusOf(root.guests)
        }
      }
      }
    }
  }
}
