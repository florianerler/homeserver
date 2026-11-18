import QtQuick
import qs.Commons
import qs.Commons as Commons

// Internet: Latenz und Paketverlust zu einer festen IP, Kurve der letzten Messungen.
Column {
  id: card
  property var info: ({})
  property var tr
  property string statusText: ""
  spacing: Style.space(8)

  readonly property bool online: info.ok === true
  readonly property bool warn: online && info.warn === true
  readonly property color warnColor: Qt.lighter(Commons.Color.urgent, 1.3)
  function num(v, d) { return Commons.I18n.formatNumber(v, d) }
  readonly property real maxMs: {
    var m = 50
    if (online) for (var i = 0; i < info.history.length; i++) if (info.history[i] !== null && info.history[i] > m) m = info.history[i]
    return m
  }

  CardHeader {
    width: parent.width
    title: card.tr.t("ping")
    statusText: card.statusText
    online: card.online
  }

  Row {
    width: parent.width
    opacity: card.online ? 1 : 0.4
    Column {
      width: parent.width / 2
      spacing: Style.space(2)
      Text { textFormat: Text.PlainText; text: card.tr.t("ping.latency"); color: Qt.darker(Commons.Color.foreground, 1.4); font.family: Style.font.family; font.pixelSize: Style.font.caption }
      Text {
        textFormat: Text.PlainText
        text: !card.online ? "–" : (card.info.avgMs === null ? card.tr.t("ping.lost") : card.num(card.info.avgMs, 0) + " ms")
        color: card.warn ? card.warnColor : Commons.Color.accent
        font.family: Style.font.family; font.pixelSize: Style.font.title; font.bold: true
      }
    }
    Column {
      width: parent.width / 2
      spacing: Style.space(2)
      Text { textFormat: Text.PlainText; text: card.tr.t("ping.loss"); color: Qt.darker(Commons.Color.foreground, 1.4); font.family: Style.font.family; font.pixelSize: Style.font.caption }
      Text {
        textFormat: Text.PlainText
        text: card.online ? card.num(card.info.lossPct, 1) + " %" : "–"
        color: card.online && card.info.lossPct > 2 ? card.warnColor : Commons.Color.foreground
        font.family: Style.font.family; font.pixelSize: Style.font.title; font.bold: true
      }
    }
  }

  // Kurve: ein Balken je Messung; fehlende Messung (alles verloren) = roter Stummel.
  Item {
    width: parent.width
    height: Style.space(36)
    opacity: card.online ? 1 : 0.35
    Row {
      anchors.right: parent.right
      anchors.bottom: parent.bottom
      height: parent.height
      spacing: 1
      Repeater {
        model: card.online ? card.info.history : []
        delegate: Item {
          width: Math.max(2, (card.width - 59) / 60)
          height: parent.height
          Rectangle {
            width: parent.width
            anchors.bottom: parent.bottom
            height: modelData === null ? parent.height : Math.max(2, parent.height * Math.min(1, modelData / card.maxMs))
            color: modelData === null ? Commons.Color.urgent : (modelData > 100 ? card.warnColor : Commons.Color.accent)
            opacity: modelData === null ? 0.9 : 0.75
          }
        }
      }
    }
  }

  Text {
    visible: card.online
    textFormat: Text.PlainText
    text: card.tr.t("ping.target") + " " + card.info.target
    color: Qt.darker(Commons.Color.foreground, 1.4)
    font.family: Style.font.family
    font.pixelSize: Style.font.caption
  }
}
