import QtQuick
import qs.Commons
import qs.Commons as Commons
import "Model.js" as Model

// System: Last, RAM, CPU-Temperatur, Laufzeit des Servers.
Column {
  id: card
  property var info: ({})
  property var tr
  property string statusText: ""
  spacing: Style.space(8)

  function num(v, d) { return Commons.I18n.formatNumber(v, d) }
  readonly property bool online: info.ok === true
  readonly property real memFraction: online ? Model.memUsedFraction(info.memTotalKb, info.memAvailKb) : 0
  readonly property real loadFraction: online ? Math.min(1, info.load[0] / info.ncpu) : 0

  CardHeader {
    width: parent.width
    title: card.tr.t("system")
    statusText: card.statusText
    online: card.online
  }

  Column {
    width: parent.width
    spacing: Style.space(6)
    opacity: card.online ? 1 : 0.4

    InfoRow {
      width: parent.width
      label: card.tr.t("sys.load")
      value: card.online
        ? card.num(card.info.load[0], 2) + " · " + card.num(card.info.load[1], 2) + " · " + card.num(card.info.load[2], 2) + "  (" + card.info.ncpu + " CPU)"
        : "–"
    }
    MeterBar { width: parent.width; fraction: card.loadFraction; warnAt: 0.9; criticalAt: 1.5 }

    InfoRow {
      width: parent.width
      label: card.tr.t("sys.ram")
      value: card.online
        ? card.num((card.info.memTotalKb - card.info.memAvailKb) / 1048576, 1) + " / " + card.num(card.info.memTotalKb / 1048576, 1) + " GB  (" + card.num(card.memFraction * 100, 0) + " %)"
        : "–"
    }
    MeterBar { width: parent.width; fraction: card.memFraction }

    InfoRow {
      width: parent.width
      label: card.tr.t("sys.temp")
      value: !card.online ? "–" : (card.info.tempC === null ? card.tr.t("sys.noTemp") : card.num(card.info.tempC, 0) + " °C")
      valueColor: card.online && card.info.tempC !== null && card.info.tempC >= 85 ? Commons.Color.urgent : Commons.Color.foreground
    }
    InfoRow {
      width: parent.width
      label: card.tr.t("sys.uptime")
      value: card.online ? Model.uptimeText(card.info.uptime) : "–"
    }
  }
}
