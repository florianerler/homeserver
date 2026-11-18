import QtQuick
import qs.Commons
import qs.Commons as Commons

// Waagerechter Fuellbalken, fraction 0..1. Faerbt sich ab warnAt / criticalAt.
Rectangle {
  id: bar
  property real fraction: 0
  property real warnAt: 0.85
  property real criticalAt: 0.95
  height: Style.space(6)
  radius: height / 2
  color: Qt.rgba(Commons.Color.foreground.r, Commons.Color.foreground.g, Commons.Color.foreground.b, 0.15)

  Rectangle {
    width: parent.width * Math.max(0, Math.min(1, bar.fraction))
    height: parent.height
    radius: parent.radius
    color: bar.fraction >= bar.criticalAt ? Commons.Color.urgent
         : bar.fraction >= bar.warnAt ? Qt.lighter(Commons.Color.urgent, 1.3)
         : Commons.Color.accent
  }
}
