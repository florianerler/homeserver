// Test fuer plugin/Model.js:  node tests/test_model.js
const fs = require("fs"), path = require("path"), assert = require("assert")
const src = fs.readFileSync(path.join(__dirname, "..", "plugin", "Model.js"), "utf8").replace(/^\.pragma library$/m, "")
const M = new Function(src + "; return { scale, format, push, peak, parseSample }")()
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

const ok = M.parseSample('{"host":{"ok":true,"state":"online","iface":"nic0","rx":10,"tx":20,"ifaces":{"nic0":{"rx":10,"tx":20},"vmbr0":{"rx":1,"tx":2}}},"qbt":{"ok":false,"state":"offline","error":"refused"}}')
assert.strictEqual(ok.host.rx, 10); assert.strictEqual(ok.host.ifaces.vmbr0.tx, 2)
assert.strictEqual(ok.qbt.ok, false); assert.strictEqual(ok.qbt.error, "refused")
assert.strictEqual(M.parseSample("kein json"), null)
assert.strictEqual(M.parseSample('{"host":{}}'), null)
assert.strictEqual(M.parseSample('{"host":{"rx":"x","tx":-3},"qbt":{}}').host.rx, 0)
console.log("Model.js: alle Tests ok")
