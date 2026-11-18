import QtQuick
import qs.Commons
import qs.Commons as Commons

// Gaeste: VMs und Container mit Status (nur Anzeige).
Column {
  id: card
  property var info: ({})
  property var tr
  property string statusText: ""
  spacing: Style.space(8)

  readonly property bool online: info.ok === true

  function statusColor(st) {
    return st === "running" ? Commons.Color.accent
         : st === "stopped" ? Qt.darker(Commons.Color.foreground, 1.8)
         : Commons.Color.urgent
  }

  CardHeader {
    width: parent.width
    title: card.tr.t("guests")
    statusText: card.statusText
    online: card.online
  }

  Text {
    visible: card.online
    width: parent.width
    textFormat: Text.PlainText
    text: card.info.total > 0
      ? card.tr.t("guests.summary").arg(card.info.running).arg(card.info.total)
      : card.tr.t("guests.none")
    color: Qt.darker(Commons.Color.foreground, 1.4)
    font.family: Style.font.family
    font.pixelSize: Style.font.caption
  }

  Repeater {
    model: card.online ? card.info.items : []
    delegate: Item {
      width: card.width
      height: Math.max(nameText.implicitHeight, Style.space(20))

      Rectangle {
        id: gdot
        width: Style.space(8)
        height: width
        radius: width / 2
        color: card.statusColor(modelData.status)
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
      }
      Text {
        id: nameText
        textFormat: Text.PlainText
        text: modelData.name
        color: Commons.Color.foreground
        font.family: Style.font.family
        font.pixelSize: Style.font.body
        elide: Text.ElideRight
        anchors.left: gdot.right
        anchors.leftMargin: Style.space(8)
        anchors.right: meta.left
        anchors.rightMargin: Style.space(8)
        anchors.verticalCenter: parent.verticalCenter
      }
      Text {
        id: meta
        textFormat: Text.PlainText
        text: modelData.vmid + " · " + (modelData.type === "qemu" ? "VM" : "LXC") + " · " + card.tr.t("guest." + modelData.status)
        color: Qt.darker(Commons.Color.foreground, 1.4)
        font.family: Style.font.family
        font.pixelSize: Style.font.caption
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
      }
    }
  }
}
