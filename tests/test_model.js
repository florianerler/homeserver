// Test fuer plugin/Model.js:  node tests/test_model.js
const fs = require("fs"), path = require("path"), assert = require("assert")
const src = fs.readFileSync(path.join(__dirname, "..", "plugin", "Model.js"), "utf8").replace(/^\.pragma library$/m, "")
const M = new Function(src + "; return { scale, format, push, peak, parseSample, summarize }")()
const fmt = (v, d) => v.toFixed(d)

assert.strictEqual(M.format(0, fmt), "0 B/s")
assert.strictEqual(M.format(999, fmt), "999 B/s")
assert.strictEqual(M.format(1000, fmt), "1.00 kB/s")
assert.strictEqual(M.format(12400, fmt), "12.4 kB/s")
assert.strictEqual(M.format(123456, fmt), "123 kB/s")
assert.strictEqual(M.format(999960, fmt), "1.00 MB/s")      // Rundung 1000.0 kB -> 1.00 MB
assert.strictEqual(M.format(12.4e6, fmt), "12.4 MB/s")
assert.strictEqual(M.format(2.5e9, fmt), "2.50 GB/s")
assert.strictEqual(M.format(1e15, fmt), "1000000 GB/s")      // groesste Einheit bleibt GB/s
assert.strictEqual(M.format(-5, fmt), "0 B/s")
assert.strictEqual(M.format(NaN, fmt), "0 B/s")
assert.strictEqual(M.format("abc", fmt), "0 B/s")

assert.deepStrictEqual(M.push([1, 2, 3], 4, 3), [2, 3, 4])
assert.deepStrictEqual(M.push([], -1, 3), [0])
assert.strictEqual(M.peak([1, 5], [3]), 5)

const ok = M.parseSample('{"v":2,"cards":{"net":{"ok":true,"state":"online","iface":"nic0","rx":10,"tx":20,"ifaces":{"nic0":{"rx":10,"tx":20},"vmbr0":{"rx":1,"tx":2}}},"qbt":{"ok":false,"state":"offline","error":"refused"}}}')
assert.strictEqual(ok.net.rx, 10); assert.strictEqual(ok.net.ifaces.vmbr0.tx, 2)
assert.strictEqual(ok.qbt.ok, false); assert.strictEqual(ok.qbt.error, "refused")
assert.strictEqual(M.parseSample("kein json"), null)
assert.strictEqual(M.parseSample('{"v":1,"cards":{}}'), null)                 // falsche Version
assert.strictEqual(M.parseSample('{"v":2,"cards":{"net":{}}}'), null)         // qbt fehlt
assert.strictEqual(M.parseSample('{"v":2,"cards":{"net":{"rx":"x","tx":-3},"qbt":{}}}').net.rx, 0)
assert.strictEqual(M.parseSample('{"v":2,"cards":{"net":{},"qbt":{},"BAD NAME":{},"x":{}}}')["BAD NAME"], undefined)
const many = {}; for (let i = 0; i < 40; i++) many["c" + "abcdefghijklmnopqrstuvwxyz"[i % 26] + "z".repeat(i % 3)] = {}
many.net = {}; many.qbt = {}
assert.ok(Object.keys(M.parseSample(JSON.stringify({ v: 2, cards: many }))).length <= 16)   // Karten begrenzt
assert.strictEqual(M.parseSample('{"v":2,"cards":{"net":{"error":"' + "e".repeat(100) + '"},"qbt":{}}}').net.error.length, 32)

// Kopfzeile: alle Zustaende
const on = { ok: true, state: "online" }, con = { ok: false, state: "connecting" }, off = { ok: false, state: "offline" }
assert.deepStrictEqual(M.summarize({ a: on, b: on }), { kind: "allOk", online: 2, connecting: 0, offline: 0, total: 2 })
assert.strictEqual(M.summarize({ a: con, b: con }).kind, "connecting")
assert.strictEqual(M.summarize({ a: on, b: con }).kind, "connecting")
assert.strictEqual(M.summarize({ a: off, b: off }).kind, "allOffline")
assert.strictEqual(M.summarize({ a: con, b: off }).kind, "someOffline")      // verbindet + offline, keiner online
assert.strictEqual(M.summarize({ a: on, b: off }).kind, "someOffline")
assert.strictEqual(M.summarize({ a: on, b: off }).offline, 1)
assert.strictEqual(M.summarize({ a: on, b: off, c: off }).total, 3)
console.log("Model.js: alle Tests ok")
