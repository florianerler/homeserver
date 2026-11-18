import QtQuick
import qs.Commons
import qs.Commons as Commons
import "Model.js" as Model

// Speicher: Fuellstand je Storage (Warnung ab 85 %, kritisch ab 95 %).
Column {
  id: card
  property var info: ({})
  property var tr
  property string statusText: ""
  spacing: Style.space(8)

  readonly property bool online: info.ok === true
  function num(v, d) { return Commons.I18n.formatNumber(v, d) }

  CardHeader {
    width: parent.width
    title: card.tr.t("storage")
    statusText: card.statusText
    online: card.online
  }

  Text {
    visible: card.online && card.info.items.length === 0
    textFormat: Text.PlainText
    text: card.tr.t("storage.none")
    color: Qt.darker(Commons.Color.foreground, 1.4)
    font.family: Style.font.family
    font.pixelSize: Style.font.caption
  }

  Repeater {
    model: card.online ? card.info.items : []
    delegate: Column {
      width: card.width
      spacing: Style.space(4)
      opacity: modelData.active ? 1 : 0.5
      InfoRow {
        width: parent.width
        label: modelData.name + " · " + modelData.type
        value: !modelData.active ? card.tr.t("storage.inactive")
             : Model.sizeText(modelData.usedKb, card.num) + " / " + Model.sizeText(modelData.totalKb, card.num) + "  (" + card.num(modelData.pct, 0) + " %)"
        valueColor: modelData.active && modelData.pct >= 85 ? Qt.lighter(Commons.Color.urgent, 1.3) : Commons.Color.foreground
      }
      MeterBar { width: parent.width; visible: modelData.active; fraction: modelData.pct / 100 }
    }
  }
}
