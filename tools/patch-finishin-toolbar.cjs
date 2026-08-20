// patch-finishin-toolbar.cjs: add 选单 group to FINISH_IN toolbarDiff
const fs = require('node:fs')
const p = 'F:/INCER/light-mes/frontend/src/business/engine.js'
let s = fs.readFileSync(p, 'utf8')
const from = "  toolbarDiff: [\n    { name: '\u9009\u5355', actions: ['\u9009\u5355', '\u9009\u4ea7\u6210\u54c1\u5165\u5e93\u5355\uff08\u81ea\u5236\u52a0\u5de5\uff09', '\u9009\u751f\u4ea7\u52a0\u5de5\u5355'] },"
if (s.includes(from)) { console.log('already has select group'); process.exit(0) }
// FINISH_IN toolbarDiff currently: find its toolbarDiff block
const fiStart = s.indexOf('const FINISH_IN_CONFIG')
const fiEnd = s.indexOf('\n})\n', fiStart)
const seg = s.slice(fiStart, fiEnd)
const tFrom = 'toolbarDiff: ['
const tIdx = seg.indexOf(tFrom)
if (tIdx < 0) { console.error('FINISH_IN toolbarDiff not found'); process.exit(1) }
const tEnd = seg.indexOf('],', tIdx) + 2
const newTb = "toolbarDiff: [\n    { name: '\u9009\u5355', actions: ['\u9009\u5355', '\u9009\u4ea7\u6210\u54c1\u5165\u5e93\u5355\uff08\u81ea\u5236\u52a0\u5de5\uff09', '\u9009\u751f\u4ea7\u52a0\u5de5\u5355'] },\n    { name: '\u751f\u5355', actions: ['\u751f\u6210\u4ea7\u6210\u54c1\u5165\u5e93\u5355\uff08\u81ea\u5236\u9000\u5e93\uff09', '\u751f\u6210\u8865\u6295\u751f\u4ea7\u52a0\u5de5\u5355', '\u751f\u6210\u8fd4\u5de5\u751f\u4ea7\u52a0\u5de5\u5355'] },\n    { name: '\u8f6c\u6362', actions: ['\u8f6c\u6210\u9500\u552e\u51fa\u5e93\u5355'] },\n    { name: '\u5de5\u5177', actions: ['\u73b0\u5b58\u91cf\u67e5\u8be2', '\u53d8\u66f4\u5386\u53f2', '\u8054\u67e5', '\u751f\u4ea7\u52a0\u5de5\u60c5\u51b5', '\u9000\u5e93\u60c5\u51b5', '\u751f\u5355\u6d41\u7a0b\u8054\u67e5'] },\n    { name: '\u8bbe\u7f6e', actions: ['\u5355\u636e\u8bbe\u7f6e', '\u79fb\u52a8\u63a7\u4ef6\u4f4d\u7f6e', '\u8c03\u6574\u63a7\u4ef6\u5bbd\u5ea6', '\u5de5\u5177\u680f\u8bbe\u7f6e', '\u667a\u80fd\u9009\u5355\u8bbe\u7f6e'] },\n  ]"
s = s.slice(0, fiStart) + seg.slice(0, tIdx) + newTb + seg.slice(tEnd) + s.slice(fiEnd)
fs.writeFileSync(p, s)
console.log('FINISH_IN toolbarDiff select group added')