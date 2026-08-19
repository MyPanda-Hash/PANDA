// gen-db-update.cjs: generate update-route.sql from engine.js ROUTE config + rows
const fs = require('node:fs')
const eng = fs.readFileSync('F:/INCER/light-mes/frontend/src/business/engine.js', 'utf8')
const cfgStart = eng.indexOf('const ROUTE_CONFIG = ')
const cfgEnd = eng.indexOf('\nlet ROUTE_ROWS')
const rowsEnd = eng.indexOf('\n// BOM ', cfgEnd)
const cfgText = eng.slice(cfgStart + 'const ROUTE_CONFIG = '.length, cfgEnd)
const rowsText = eng.slice(eng.indexOf('let ROUTE_ROWS = [', cfgEnd) + 'let ROUTE_ROWS = ['.length, rowsEnd)
const WORKSHOP_OPTIONS = ['\u7194\u94f8\u8f66\u95f4', '\u8f67\u5236\u8f66\u95f4', '\u7cbe\u6574\u8f66\u95f4', '\u6d4b\u8bd5\u8f66\u95f4']
const cfg = eval('(' + cfgText + ')')
const rows = eval('[' + rowsText)
// approval injection (mirror applyApprovalConfig)
const APPROVAL_ACTIONS = ['\u63d0\u4ea4\u5ba1\u6279', '\u5ba1\u6279\u901a\u8fc7', '\u9a73\u56de\u5ba1\u6279']
const md = cfg.metadata
if (md.panelState) {
  const opts = md.panelState.defaultOptions || []
  if (!opts.includes('\u5ba1\u6279\u4e2d')) { const ai = opts.indexOf('\u5df2\u5ba1\u6838'); opts.splice(ai >= 0 ? ai + 1 : opts.length, 0, '\u5ba1\u6279\u4e2d') }
}
const groups = md.buttonGroups || []
if (!groups.some((g) => g.name === '\u5ba1\u6279')) {
  const gi = groups.findIndex((g) => g.name === '\u5ba1\u6838')
  const group = { name: '\u5ba1\u6279', actions: [...APPROVAL_ACTIONS] }
  if (gi >= 0) groups.splice(gi + 1, 0, group); else groups.push(group)
}
for (const a of APPROVAL_ACTIONS) if (!(md.panelButtons || []).some((b) => b.buttonName === a)) md.panelButtons.push({ buttonName: a })
const esc = (s) => String(s).replace(/'/g, "''")
const cfgJson = JSON.stringify(cfg)
let sql = '-- ROUTE \u9762\u677f\u540c\u6b65\uff1a\u5bf9\u9f50 T+ AA1055 \u5b9e\u6d4b\uff082026-08-19\uff09\n'
sql += "UPDATE panel_config SET config='" + esc(cfgJson) + "' WHERE panel_code='ROUTE';\n"
for (const row of rows) {
  const { detail, ...head } = row
  const no = row['\u7f16\u53f7']
  const status = row['\u5355\u636e\u72b6\u6001'] === '\u8349\u7a3f' ? '\u8349\u7a3f' : '\u5df2\u5ba1\u6838'
  const audited = row['\u5ba1\u6838\u4eba'] ? "'admin', NOW()" : 'NULL, NULL'
  sql += "DELETE FROM form_data WHERE panel_code='ROUTE' AND form_no='" + no + "';\n"
  sql += "INSERT INTO form_data (panel_code, form_no, data, detail_data, status, create_by, create_time, audit_by, audit_time) VALUES ('ROUTE', '" + no + "', '" + esc(JSON.stringify(head)) + "', '" + esc(JSON.stringify(detail)) + "', '" + status + "', 'admin', NOW(), " + audited + ");\n"
}
fs.writeFileSync('F:/INCER/light-mes/tools/update-route.sql', sql)
console.log('sql generated, bytes:', Buffer.byteLength(sql))