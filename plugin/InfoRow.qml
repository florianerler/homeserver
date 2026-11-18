import QtQuick
import qs.Commons
import qs.Commons as Commons

// Eine Zeile "Bezeichnung ........ Wert".
Item {
  id: row
  property string label: ""
  property string value: ""
  property color valueColor: Commons.Color.foreground
  height: Math.max(l.implicitHeight, v.implicitHeight)

  Text {
    id: l
    textFormat: Text.PlainText
    text: row.label
    color: Qt.darker(Commons.Color.foreground, 1.4)
    font.family: Style.font.family
    font.pixelSize: Style.font.caption
    anchors.left: parent.left
    anchors.verticalCenter: parent.verticalCenter
  }
  Text {
    id: v
    textFormat: Text.PlainText
    text: row.value
    color: row.valueColor
    font.family: Style.font.family
    font.pixelSize: Style.font.body
    elide: Text.ElideRight
    horizontalAlignment: Text.AlignRight
    anchors.left: l.right
    anchors.leftMargin: Style.space(12)
    anchors.right: parent.right
    anchors.verticalCenter: parent.verticalCenter
  }
}
