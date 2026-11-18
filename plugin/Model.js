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

// Eine Quelle (Karte) aus dem Snapshot pruefen und bereinigen.
function parseSource(s) {
  if (!s || typeof s !== "object") s = {}
  var out = { ok: s.ok === true, state: String(s.state || "offline").slice(0, 16),
              error: String(s.error || "").slice(0, 32), rx: clean(s.rx), tx: clean(s.tx), ifaces: {} }
  if (s.ifaces && typeof s.ifaces === "object") {
    var n = 0
    for (var k in s.ifaces) {
      if (++n > 8) break
      var f = s.ifaces[k]
      if (f && typeof f === "object") out.ifaces[String(k).slice(0, 16)] = { rx: clean(f.rx), tx: clean(f.tx) }
    }
  }
  if (typeof s.iface === "string") out.iface = s.iface.slice(0, 16)
  return out
}

// Eine JSON-Zeile (Snapshot v2) des Streamers pruefen. Gibt null bei Muell zurueck.
// -> { net: <Quelle>, qbt: <Quelle>, ... } fuer jede Karte in "cards" (max. 16).
function parseSample(line) {
  var obj
  try { obj = JSON.parse(line) } catch (e) { return null }
  if (!obj || typeof obj !== "object" || obj.v !== 2 || !obj.cards || typeof obj.cards !== "object") return null
  if (!obj.cards.net || !obj.cards.qbt) return null
  var out = {}, n = 0
  for (var name in obj.cards) {
    if (++n > 16) break
    if (/^[a-z]{1,16}$/.test(name)) out[name] = parseSource(obj.cards[name])
  }
  return out
}

// Gesamtstatus fuer die Kopfzeile des Popups.
// kind: allOk | connecting | allOffline | someOffline (dann ist offline die Anzahl).
function summarize(cards) {
  var online = 0, connecting = 0, offline = 0
  for (var k in cards) {
    var c = cards[k]
    if (c.ok) online++
    else if (c.state === "connecting") connecting++
    else offline++
  }
  var total = online + connecting + offline
  var kind
  if (offline === 0 && connecting === 0) kind = "allOk"
  else if (offline === 0) kind = "connecting"
  else if (online === 0 && connecting === 0) kind = "allOffline"
  else kind = "someOffline"
  return { kind: kind, online: online, connecting: connecting, offline: offline, total: total }
}
