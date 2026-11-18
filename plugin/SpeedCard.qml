import QtQuick
import qs.Commons
import qs.Commons as Commons
import "Model.js" as Model

// Speedtest auf dem Server: letztes Ergebnis, Verlauf ueber Tage, Knopf "Jetzt messen".
Column {
  id: card
  property var info: ({})
  property var tr
  property string statusText: ""
  signal runRequested()
  spacing: Style.space(8)

  readonly property bool online: info.ok === true
  readonly property var last: online && info.history.length > 0 ? info.history[info.history.length - 1] : null
  readonly property var stats: online ? Model.speedStats(info.history) : Model.speedStats([])
  readonly property bool canRun: online && !info.running && info.cooldown === 0
  function num(v, d) { return Commons.I18n.formatNumber(v, d) }
  function mbit(v) { return Model.mbitText(v, num) }
  readonly property real maxDown: {
    var m = 1000
    if (online) for (var i = 0; i < info.history.length; i++) if (info.history[i].down > m) m = info.history[i].down
    return m
  }
  readonly property var bars: online ? info.history.slice(-48) : []

  CardHeader {
    width: parent.width
    title: card.tr.t("speed")
    statusText: card.statusText
    online: card.online
  }

  Row {
    width: parent.width
    opacity: card.online ? 1 : 0.4
    Repeater {
      model: [{ arrow: "↓", up: false }, { arrow: "↑", up: true }]
      delegate: Column {
        width: card.width / 2
        spacing: Style.space(2)
        Text {
          textFormat: Text.PlainText
          text: card.last ? (card.tr.t("speed.last").arg(Model.ageText(card.last.age))) : (index === 0 ? card.tr.t("speed.none") : "")
          color: Qt.darker(Commons.Color.foreground, 1.4)
          font.family: Style.font.family
          font.pixelSize: Style.font.caption
        }
        Text {
          textFormat: Text.PlainText
          text: card.last ? modelData.arrow + " " + card.mbit(modelData.up ? card.last.up : card.last.down) : "–"
          color: modelData.up ? Commons.Color.foreground : Commons.Color.accent
          font.family: Style.font.family
          font.pixelSize: Style.font.title
          font.bold: true
        }
      }
    }
  }

  // Verlauf: ein Balken je Messung (Download), die letzten 48 (ca. 6 Tage).
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
        model: card.bars
        delegate: Item {
          width: Math.max(2, (card.width - 47) / 48)
          height: parent.height
          Rectangle {
            width: parent.width
            anchors.bottom: parent.bottom
            height: Math.max(2, parent.height * Math.min(1, modelData.down / card.maxDown))
            color: Commons.Color.accent
            opacity: 0.75
          }
        }
      }
    }
  }

  Text {
    visible: card.online && card.stats.n > 0
    width: parent.width
    wrapMode: Text.WordWrap
    textFormat: Text.PlainText
    text: card.tr.t("speed.avg").arg(card.stats.n).arg(card.mbit(card.stats.down)).arg(card.mbit(card.stats.up)).arg(card.num(card.stats.ping, 0))
    color: Qt.darker(Commons.Color.foreground, 1.4)
    font.family: Style.font.family
    font.pixelSize: Style.font.caption
  }

  Text {
    visible: card.online && card.info.lastOk === false
    textFormat: Text.PlainText
    text: card.tr.t("speed.failed")
    color: Qt.lighter(Commons.Color.urgent, 1.3)
    font.family: Style.font.family
    font.pixelSize: Style.font.caption
  }

  // Knopf: nur aktiv, wenn nichts laeuft und der Mindestabstand (10 min) vorbei ist.
  Rectangle {
    id: runButton
    width: parent.width
    height: Style.space(30)
    radius: Style.space(6)
    color: card.canRun ? (mouse.containsMouse ? Qt.lighter(Commons.Color.accent, 1.15) : Commons.Color.accent)
                       : Qt.rgba(Commons.Color.foreground.r, Commons.Color.foreground.g, Commons.Color.foreground.b, 0.12)
    visible: card.online
    Text {
      anchors.centerIn: parent
      textFormat: Text.PlainText
      text: card.info.running ? card.tr.t("speed.running")
          : card.info.cooldown > 0 ? card.tr.t("speed.wait").arg(Model.ageText(card.info.cooldown))
          : card.tr.t("speed.run")
      color: card.canRun ? Commons.Color.background : Qt.darker(Commons.Color.foreground, 1.4)
      font.family: Style.font.family
      font.pixelSize: Style.font.body
      font.bold: true
    }
    MouseArea {
      id: mouse
      anchors.fill: parent
      hoverEnabled: true
      enabled: card.canRun
      cursorShape: card.canRun ? Qt.PointingHandCursor : Qt.ArrowCursor
      onClicked: card.runRequested()
    }
  }

  Text {
    visible: card.online && card.info.lastCmd !== null && card.info.lastCmd !== "ok"
    textFormat: Text.PlainText
    text: card.info.lastCmd ? card.tr.t("speed.cmd." + card.info.lastCmd) : ""
    color: Qt.darker(Commons.Color.foreground, 1.4)
    font.family: Style.font.family
    font.pixelSize: Style.font.caption
  }
}
