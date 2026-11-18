import QtQuick
import qs.Commons
import qs.Commons as Commons
import "Model.js" as Model

// System: Last, RAM, CPU-Temperatur, Laufzeit des Servers.
Column {
  id: card
  property var data: ({})
  property var tr
  property string statusText: ""
  spacing: Style.space(8)

  function num(v, d) { return Commons.I18n.formatNumber(v, d) }
  readonly property bool online: data.ok === true
  readonly property real memFraction: online ? Model.memUsedFraction(data.memTotalKb, data.memAvailKb) : 0
  readonly property real loadFraction: online ? Math.min(1, data.load[0] / data.ncpu) : 0

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
        ? card.num(card.data.load[0], 2) + " · " + card.num(card.data.load[1], 2) + " · " + card.num(card.data.load[2], 2) + "  (" + card.data.ncpu + " CPU)"
        : "–"
    }
    MeterBar { width: parent.width; fraction: card.loadFraction; warnAt: 0.9; criticalAt: 1.5 }

    InfoRow {
      width: parent.width
      label: card.tr.t("sys.ram")
      value: card.online
        ? card.num((card.data.memTotalKb - card.data.memAvailKb) / 1048576, 1) + " / " + card.num(card.data.memTotalKb / 1048576, 1) + " GB  (" + card.num(card.memFraction * 100, 0) + " %)"
        : "–"
    }
    MeterBar { width: parent.width; fraction: card.memFraction }

    InfoRow {
      width: parent.width
      label: card.tr.t("sys.temp")
      value: !card.online ? "–" : (card.data.tempC === null ? card.tr.t("sys.noTemp") : card.num(card.data.tempC, 0) + " °C")
      valueColor: card.online && card.data.tempC !== null && card.data.tempC >= 85 ? Commons.Color.urgent : Commons.Color.foreground
    }
    InfoRow {
      width: parent.width
      label: card.tr.t("sys.uptime")
      value: card.online ? Model.uptimeText(card.data.uptime) : "–"
    }
  }
}
