// patch-select-actions.cjs: trim select group actions to implemented sources
const fs = require('node:fs')
const p = 'F:/INCER/light-mes/frontend/src/business/engine.js'
let s = fs.readFileSync(p, 'utf8')
const jobs = [
  // PURCHASE_IN: keep 选采购订单 only
  ["{ name: '\u9009\u5355', actions: ['\u9009\u91c7\u8d2d\u8ba2\u5355', '\u9009\u8fdb\u8d27\u5355', '\u9009\u5230\u8d27\u5355', '\u9009\u68c0\u9a8c\u5355', '\u8bbe\u7f6e\u9ed8\u8ba4\u529f\u80fd'] }", "{ name: '\u9009\u5355', actions: ['\u9009\u5355', '\u9009\u91c7\u8d2d\u8ba2\u5355'] }"],
  // FINISH_IN (just added)
  ["{ name: '\u9009\u5355', actions: ['\u9009\u5355', '\u9009\u4ea7\u6210\u54c1\u5165\u5e93\u5355\uff08\u81ea\u5236\u52a0\u5de5\uff09', '\u9009\u751f\u4ea7\u52a0\u5de5\u5355'] }", "{ name: '\u9009\u5355', actions: ['\u9009\u5355', '\u9009\u751f\u4ea7\u52a0\u5de5\u5355'] }"],
  // SALE_OUT
  ["{ name: '\u9009\u5355', actions: ['\u9009\u5355', '\u9009\u9500\u552e\u8ba2\u5355'] }", "{ name: '\u9009\u5355', actions: ['\u9009\u5355', '\u9009\u9500\u552e\u8ba2\u5355'] }"],
]
let n = 0
for (const [from, to] of jobs) {
  if (s.includes(from)) { s = s.replace(from, to); n++ }
}
fs.writeFileSync(p, s)
console.log('select actions trimmed: ' + n)