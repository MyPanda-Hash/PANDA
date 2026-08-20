// patch-select-actions2.cjs: trim select group per panel segment
const fs = require('node:fs')
const p = 'F:/INCER/light-mes/frontend/src/business/engine.js'
let s = fs.readFileSync(p, 'utf8')
const targets = [
  ['PURCHASE_IN', '\u9009\u91c7\u8d2d\u8ba2\u5355'],
  ['SALE_OUT', '\u9009\u9500\u552e\u8ba2\u5355'],
  ['OTHER_IN', '\u9009\u5176\u4ed6\u51fa\u5e93\u5355'],
  ['MATERIAL_OUT', '\u9009\u751f\u4ea7\u52a0\u5de5\u5355'],
  ['OTHER_OUT', '\u9009\u914d\u8d27\u5355'],
]
let n = 0
for (const [code, main] of targets) {
  const start = s.indexOf('const ' + code + '_CONFIG')
  const end = s.indexOf('\n})\n', start)
  if (start < 0) { console.error(code + ' not found'); continue }
  const seg = s.slice(start, end)
  const tFrom = seg.indexOf("toolbarDiff: [")
  if (tFrom < 0) continue
  // find the select group line within toolbarDiff
  const selFrom = seg.indexOf("{ name: '\u9009\u5355'")
  if (selFrom < 0) { console.log(code + ': no select group'); continue }
  const selEnd = seg.indexOf('}', selFrom) + 1
  const newGroup = "{ name: '\u9009\u5355', actions: ['\u9009\u5355', '" + main + "'] }"
  const newSeg = seg.slice(0, selFrom) + newGroup + seg.slice(selEnd)
  s = s.slice(0, start) + newSeg + s.slice(end)
  n++
  console.log(code + ' trimmed')
}
fs.writeFileSync(p, s)
console.log('total: ' + n)