import QtQuick

// Verlaufslinie fuer Download (rx) und Upload (tx). Die Werte laufen von rechts
// herein; die Skala passt sich an die Spitze im Fenster an (mindestens 1 kB/s,
// damit Rauschen nicht wie Last aussieht).
Canvas {
  id: root

  property var rx: []
  property var tx: []
  property int window: 90
  property color rxColor: "#cccccc"
  property color txColor: "#888888"
  property color baseColor: "#444444"
  property real peak: 1000

  onRxChanged: requestPaint()
  onTxChanged: requestPaint()
  onWidthChanged: requestPaint()
  onHeightChanged: requestPaint()
  onRxColorChanged: requestPaint()
  onTxColorChanged: requestPaint()

  onPaint: {
    var ctx = getContext("2d")
    ctx.reset()
    var w = width, h = height
    if (w <= 0 || h <= 0) return
    var top = 2, bottom = h - 2
    var scaleMax = Math.max(1000, peak) * 1.1
    var step = w / Math.max(1, window - 1)

    ctx.strokeStyle = baseColor
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(0, bottom + 0.5)
    ctx.lineTo(w, bottom + 0.5)
    ctx.stroke()

    function line(arr, color) {
      if (!arr || arr.length < 2) return
      ctx.strokeStyle = color
      ctx.lineWidth = 1.5
      ctx.lineJoin = "round"
      ctx.beginPath()
      for (var i = 0; i < arr.length; i++) {
        var x = w - (arr.length - 1 - i) * step
        var y = bottom - (Math.min(arr[i], scaleMax) / scaleMax) * (bottom - top)
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
      }
      ctx.stroke()
    }
    line(tx, txColor)
    line(rx, rxColor)
  }
}
