import QtQuick
import qs.Commons
import qs.Commons as Commons

// SMART: Gesundheitsstatus je Platte (nur Anzeige; "FEHLER" loest den Warnpunkt in der Leiste aus).
Column {
  id: card
  property var info: ({})
  property var tr
  property string statusText: ""
  spacing: Style.space(8)

  readonly property bool online: info.ok === true

  function statusColor(st) {
    return st === "passed" ? Commons.Color.accent : st === "failed" ? Commons.Color.urgent : Qt.darker(Commons.Color.foreground, 1.8)
  }

  CardHeader {
    width: parent.width
    title: card.tr.t("smart")
    statusText: card.statusText
    online: card.online
  }

  Text {
    visible: card.online && card.info.items.length === 0
    textFormat: Text.PlainText
    text: card.tr.t("smart.none")
    color: Qt.darker(Commons.Color.foreground, 1.4)
    font.family: Style.font.family
    font.pixelSize: Style.font.caption
  }

  Repeater {
    model: card.online ? card.info.items : []
    delegate: Item {
      width: card.width
      height: Math.max(devText.implicitHeight, Style.space(20))
      Rectangle {
        id: sdot
        width: Style.space(8)
        height: width
        radius: width / 2
        color: card.statusColor(modelData.status)
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
      }
      Text {
        id: devText
        textFormat: Text.PlainText
        text: modelData.dev
        color: Commons.Color.foreground
        font.family: Style.font.family
        font.pixelSize: Style.font.body
        anchors.left: sdot.right
        anchors.leftMargin: Style.space(8)
        anchors.verticalCenter: parent.verticalCenter
      }
      Text {
        textFormat: Text.PlainText
        text: card.tr.t("smart." + modelData.status) + (modelData.tempC !== null ? " · " + modelData.tempC + " °C" : "")
        color: modelData.status === "failed" ? Commons.Color.urgent : Qt.darker(Commons.Color.foreground, 1.4)
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
      }
    }
  }
}
