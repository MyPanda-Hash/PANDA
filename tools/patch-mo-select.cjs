// patch-mo-select.cjs: trim MATERIAL_OUT select group
const fs = require('node:fs')
const p = 'F:/INCER/light-mes/frontend/src/business/engine.js'
let s = fs.readFileSync(p, 'utf8')
const from = "{ name: '\u9009\u5355', actions: ['\u9009\u5355', '\u9009\u6750\u6599\u51fa\u5e93\u5355\uff08\u76f4\u63a5\u9886\u6599\uff09', '\u9009\u6750\u6599\u51fa\u5e93\u5355\uff08\u81ea\u5236\u9886\u6599\uff09', '\u9009\u751f\u4ea7\u52a0\u5de5\u5355', '\u9009\u751f\u4ea7\u52a0\u5de5\u5355(\u65b0\u589e\u6750\u6599)'] }"
const to = "{ name: '\u9009\u5355', actions: ['\u9009\u5355', '\u9009\u751f\u4ea7\u52a0\u5de5\u5355'] }"
if (!s.includes(from)) { console.error('MATERIAL_OUT select group not found'); process.exit(1) }
s = s.replace(from, to)
fs.writeFileSync(p, s)
console.log('MATERIAL_OUT select trimmed')