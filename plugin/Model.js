.pragma library

// Reine Hilfsfunktionen ohne UI (getestet in tests/test_model.js).

var UNITS = ["B/s", "kB/s", "MB/s", "GB/s"]   // SI: 1 kB = 1000 B

function clean(v) {
  v = Number(v)
  return isFinite(v) && v > 0 ? v : 0
}

function decimalsFor(value, unitIndex) {
  if (unitIndex === 0) return 0
  return value >= 100 ? 0 : value >= 10 ? 1 : 2
}

// Bytes/s -> { value, decimals, unit }, automatisch auf B/s, kB/s, MB/s, GB/s skaliert.
function scale(bytesPerSec) {
  var v = clean(bytesPerSec)
  var i = 0
  while (v >= 1000 && i < UNITS.length - 1) { v /= 1000; i++ }
  var d = decimalsFor(v, i)
  // Rundung darf nicht "1000.0 kB/s" erzeugen: dann gehoert der Wert in die naechste Einheit.
  if (i < UNITS.length - 1 && Number(v.toFixed(d)) >= 1000) { v /= 1000; i++; d = decimalsFor(v, i) }
  return { value: v, decimals: d, unit: UNITS[i] }
}

// fmtNumber(value, decimals) kommt aus der UI (Dezimalkomma je nach Sprache).
function format(bytesPerSec, fmtNumber) {
  var s = scale(bytesPerSec)
  return fmtNumber(s.value, s.decimals) + " " + s.unit
}

// Neues Array mit angehaengtem Wert, auf die letzten `max` Eintraege begrenzt.
// Es wird bewusst ein neues Array geliefert, damit QML-Bindings neu auswerten.
function push(arr, v, max) {
  var out = arr.concat([clean(v)])
  return out.length > max ? out.slice(out.length - max) : out
}

function peak(a, b) {
  var m = 0
  for (var i = 0; i < a.length; i++) if (a[i] > m) m = a[i]
  for (var j = 0; j < b.length; j++) if (b[j] > m) m = b[j]
  return m
}

// Eine JSON-Zeile des Streamers pruefen und bereinigen. Gibt null bei Muell zurueck.
function parseSample(line) {
  var obj
  try { obj = JSON.parse(line) } catch (e) { return null }
  if (!obj || typeof obj !== "object" || !obj.host || !obj.qbt) return null
  function source(s) {
    var out = { ok: s.ok === true, state: String(s.state || "offline"),
                error: String(s.error || ""), rx: clean(s.rx), tx: clean(s.tx), ifaces: {} }
    if (s.ifaces && typeof s.ifaces === "object") {
      for (var k in s.ifaces) {
        var f = s.ifaces[k]
        if (f && typeof f === "object") out.ifaces[String(k)] = { rx: clean(f.rx), tx: clean(f.tx) }
      }
    }
    if (typeof s.iface === "string") out.iface = s.iface.slice(0, 16)
    return out
  }
  return { host: source(obj.host), qbt: source(obj.qbt) }
}
