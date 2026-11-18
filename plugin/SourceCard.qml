import QtQuick
import qs.Commons
import qs.Commons as Commons
import "Model.js" as Model

// Eine Quelle im Popup: Statuspunkt, Titel, Status, zwei Raten, Verlauf, Hinweiszeile.
Column {
  id: card

  property string title: ""
  property string statusText: ""
  property bool online: false
  property real rx: 0
  property real tx: 0
  property var rxHistory: []
  property var txHistory: []
  property int window: 90
  property string downLabel: ""
  property string upLabel: ""
  property string peakText: ""
  property string extraText: ""
  property color foreground: Commons.Color.foreground
  property color accent: Commons.Color.accent
  property color urgent: Commons.Color.urgent
  property string fontFamily: Style.font.family

  readonly property color dim: Qt.darker(foreground, 1.4)

  function fmt(v) { return Model.format(v, Commons.I18n.formatNumber) }

  spacing: Style.space(8)

  Item {
    width: parent.width
    height: titleText.implicitHeight

    Rectangle {
      id: dot
      width: Style.space(8)
      height: width
      radius: width / 2
      color: card.online ? card.accent : card.urgent
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
    }
    Text {
      id: titleText
      textFormat: Text.PlainText
      text: card.title
      color: card.foreground
      font.family: card.fontFamily
      font.pixelSize: Style.font.body
      font.bold: true
      anchors.left: dot.right
      anchors.leftMargin: Style.space(8)
    }
    Text {
      textFormat: Text.PlainText
      text: card.statusText
      color: card.online ? card.dim : card.urgent
      font.family: card.fontFamily
      font.pixelSize: Style.font.caption
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
    }
  }

  Row {
    width: parent.width
    opacity: card.online ? 1 : 0.4
    Repeater {
      model: [
        { label: card.downLabel, arrow: "↓", value: card.rx },
        { label: card.upLabel, arrow: "↑", value: card.tx }
      ]
      delegate: Column {
        width: card.width / 2
        spacing: Style.space(2)
        Text {
          textFormat: Text.PlainText
          text: modelData.label
          color: card.dim
          font.family: card.fontFamily
          font.pixelSize: Style.font.caption
        }
        Text {
          textFormat: Text.PlainText
          text: card.online ? modelData.arrow + " " + card.fmt(modelData.value) : "–"
          color: index === 0 ? card.accent : card.foreground
          font.family: card.fontFamily
          font.pixelSize: Style.font.title
          font.bold: true
        }
      }
    }
  }

  Sparkline {
    width: parent.width
    height: Style.space(44)
    opacity: card.online ? 1 : 0.35
    rx: card.rxHistory
    tx: card.txHistory
    window: card.window
    peak: Model.peak(card.rxHistory, card.txHistory)
    rxColor: card.accent
    txColor: Qt.rgba(card.foreground.r, card.foreground.g, card.foreground.b, 0.6)
    baseColor: Qt.rgba(card.foreground.r, card.foreground.g, card.foreground.b, 0.2)
  }

  Text {
    visible: text !== ""
    textFormat: Text.PlainText
    text: card.peakText
    color: card.dim
    font.family: card.fontFamily
    font.pixelSize: Style.font.caption
  }

  Text {
    visible: text !== ""
    textFormat: Text.PlainText
    text: card.extraText
    color: card.dim
    font.family: card.fontFamily
    font.pixelSize: Style.font.caption
  }
}
