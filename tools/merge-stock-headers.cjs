// merge-stock-headers.cjs: merge agent designs into engine.js (header + toolbarDiff for 6 stock panels)
const fs = require('node:fs')
const p = 'F:/INCER/light-mes/frontend/src/business/engine.js'
let s = fs.readFileSync(p, 'utf8')

const PANEL_DIR = 'F:/INCER/light-mes/tools/panels/'
const COMMON_HEAD = ['\u5355\u636e\u65e5\u671f', '\u5355\u636e\u7f16\u53f7', '\u4e1a\u52a1\u7c7b\u578b']

// FINISH_IN header (agent did not produce file; built from real ST1002 new-form grab)
const MANUAL = {
  FINISH_IN: [
    { dataName: '\u5165\u5e93\u7c7b\u522b', dataType: '\u4e0b\u62c9\u6846', isRequired: false, defaultValue: '', options: ['\u81ea\u5236\u52a0\u5de5\u5165\u5e93', '\u9000\u5e93\u5165\u5e93'] },
    { dataName: '\u751f\u4ea7\u8f66\u95f4', dataType: '\u4e0b\u62c9\u6846', isRequired: false, defaultValue: '', options: ['\u7194\u94f8\u8f66\u95f4', '\u8f67\u5236\u8f66\u95f4', '\u7cbe\u6574\u8f66\u95f4', '\u6d4b\u8bd5\u8f66\u95f4'] },
    { dataName: '\u52a0\u5de5\u5355\u53f7', dataType: '\u6587\u672c', isRequired: false, defaultValue: '' },
    { dataName: '\u7ecf\u624b\u4eba', dataType: '\u53c2\u7167', isRequired: false, defaultValue: '', refPanel: 'EMP', refField: '\u5458\u5de5\u540d\u79f0', displayField: '\u5458\u5de5\u540d\u79f0', filter: { '\u505c\u7528': false } },
    { dataName: '\u9879\u76ee', dataType: '\u53c2\u7167', isRequired: false, defaultValue: '', refPanel: 'PROJ', refField: '\u9879\u76ee\u540d\u79f0', displayField: '\u9879\u76ee\u540d\u79f0', filter: { '\u505c\u7528': false } },
    { dataName: '\u4ed3\u5e93', dataType: '\u53c2\u7167', isRequired: false, defaultValue: '', refPanel: 'WH', refField: '\u4ed3\u5e93\u540d\u79f0', displayField: '\u4ed3\u5e93\u540d\u79f0', filter: { '\u505c\u7528': false } },
    { dataName: '\u9500\u552e\u8ba2\u5355\u53f7', dataType: '\u6587\u672c', isRequired: false, defaultValue: '' },
    { dataName: '\u5339\u914d\u6765\u6e90\u5355\u53f7', dataType: '\u6587\u672c', isRequired: false, defaultValue: '' },
    { dataName: '\u51ed\u8bc1\u5b57\u53f7', dataType: '\u6587\u672c', isRequired: false, defaultValue: '' },
  ],
}

const PANELS = [
  { code: 'PURCHASE_IN', biz: '\u91c7\u8d2d\u5165\u5e93', file: true },
  { code: 'FINISH_IN', biz: '\u4ea7\u6210\u54c1\u5165\u5e93', file: false },
  { code: 'OTHER_IN', biz: '\u5176\u4ed6\u5165\u5e93', file: true },
  { code: 'SALE_OUT', biz: '\u9500\u552e\u51fa\u5e93', file: true },
  { code: 'MATERIAL_OUT', biz: '\u6750\u6599\u51fa\u5e93', file: true },
  { code: 'OTHER_OUT', biz: '\u5176\u4ed6\u51fa\u5e93', file: true },
]

const COMMON_GROUPS = ['\u65b0\u589e', '\u4fdd\u5b58', '\u5220\u9664', '\u5ba1\u6838', '\u53d8\u66f4', '\u8bbe\u7f6e', '\u6253\u5370', '\u66f4\u591a']

for (const panel of PANELS) {
  // ---- header ----
  let fields
  if (panel.file) {
    fields = JSON.parse(fs.readFileSync(PANEL_DIR + panel.code + '/' + panel.code + '.header.json', 'utf8'))
  } else {
    fields = MANUAL[panel.code]
  }
  fields = fields.filter((f) => !COMMON_HEAD.includes(f.dataName))
  const headerText = JSON.stringify(fields, null, 2).replace(/\n/g, '\n    ')

  // locate panel segment
  const segStart = s.indexOf('const ' + panel.code + '_CONFIG = invPanel({')
  if (segStart < 0) { console.error('segment not found: ' + panel.code); process.exit(1) }
  const segEnd = s.indexOf('\n})\n', segStart)
  let seg = s.slice(segStart, segEnd)

  // replace header array: header: invHeader([ ... ], 'biz')
  const hFrom = seg.indexOf('header: invHeader([')
  if (hFrom < 0) { console.error('header not found: ' + panel.code); process.exit(1) }
  const hEnd = seg.indexOf('], \'' + panel.biz + '\')', hFrom)
  if (hEnd < 0) { console.error('header end not found: ' + panel.code); process.exit(1) }
  seg = seg.slice(0, hFrom) + 'header: invHeader(' + headerText + ', \'' + panel.biz + '\')' + seg.slice(hEnd + ('], \'' + panel.biz + '\')').length)

  // ---- toolbarDiff ----
  let tb = []
  try {
    tb = JSON.parse(fs.readFileSync(PANEL_DIR + panel.code + '/' + panel.code + '.toolbar.json', 'utf8'))
  } catch { tb = [] }
  let diff = tb.filter((g) => !COMMON_GROUPS.includes(g.name)).map((g) => ({ name: g.name, actions: g.actions }))
  if (!diff.some((g) => g.name === '\u67e5\u627e')) diff.push({ name: '\u67e5\u627e', actions: ['\u67e5\u627e', '\u5237\u65b0'] })
  if (!diff.some((g) => g.name === '\u5bfc\u5165')) diff.push({ name: '\u5bfc\u5165', actions: ['\u4e0b\u8f7d\u5bfc\u5165\u6a21\u677f', '\u5bfc\u5165'] })
  const tbText = JSON.stringify(diff, null, 2).replace(/\n/g, '\n    ')
  const tFrom = seg.indexOf('toolbarDiff: [')
  if (tFrom < 0) { console.error('toolbarDiff not found: ' + panel.code); process.exit(1) }
  // find matching closing bracket (balanced)
  let depth = 0, tEnd = tFrom
  for (let i = tFrom; i < seg.length; i++) {
    if (seg[i] === '[') depth++
    else if (seg[i] === ']') { depth--; if (depth === 0) { tEnd = i + 1; break } }
  }
  seg = seg.slice(0, tFrom) + 'toolbarDiff: ' + tbText + seg.slice(tEnd)

  s = s.slice(0, segStart) + seg + s.slice(segEnd)
  console.log('merged: ' + panel.code + ' (header ' + fields.length + ' fields, toolbarDiff ' + diff.length + ' groups)')
}

fs.writeFileSync(p, s)
console.log('DONE')
