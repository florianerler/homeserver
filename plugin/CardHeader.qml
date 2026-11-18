import QtQuick
import qs.Commons
import qs.Commons as Commons

// Kopfzeile jeder Karte: Statuspunkt, Titel, Status (online / offline + Grund).
Item {
  id: head
  property string title: ""
  property string statusText: ""
  property bool online: false
  property color foreground: Commons.Color.foreground
  property color accent: Commons.Color.accent
  property color urgent: Commons.Color.urgent
  property string fontFamily: Style.font.family

  height: titleText.implicitHeight

  Rectangle {
    id: dot
    width: Style.space(8)
    height: width
    radius: width / 2
    color: head.online ? head.accent : head.urgent
    anchors.left: parent.left
    anchors.verticalCenter: parent.verticalCenter
  }
  Text {
    id: titleText
    textFormat: Text.PlainText
    text: head.title
    color: head.foreground
    font.family: head.fontFamily
    font.pixelSize: Style.font.body
    font.bold: true
    anchors.left: dot.right
    anchors.leftMargin: Style.space(8)
  }
  Text {
    textFormat: Text.PlainText
    text: head.statusText
    color: head.online ? Qt.darker(head.foreground, 1.4) : head.urgent
    font.family: head.fontFamily
    font.pixelSize: Style.font.caption
    anchors.right: parent.right
    anchors.verticalCenter: parent.verticalCenter
  }
}
