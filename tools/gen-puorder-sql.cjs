// gen-puorder-sql.cjs: generate PU_ORDER panel_config + form_data SQL
const fs = require('node:fs')
const eng = fs.readFileSync('F:/INCER/light-mes/frontend/src/business/engine.js', 'utf8')
// extract PU_ORDER_CONFIG + PU_ORDER_ROWS
const cfgStart = eng.indexOf('const PU_ORDER_CONFIG = {')
const cfgEnd = eng.indexOf('\nlet PU_ORDER_ROWS', cfgStart)
const rowsEnd = eng.indexOf('\nconst PROCESS_REPORT_GRID_COLUMNS', cfgEnd)
if (cfgStart < 0 || cfgEnd < 0) { console.error('anchors missing'); process.exit(1) }
const cfgText = eng.slice(cfgStart + 'const PU_ORDER_CONFIG = {'.length - 1, cfgEnd) // '{...}'
const rowsText = eng.slice(eng.indexOf('let PU_ORDER_ROWS = [', cfgStart) + 'let PU_ORDER_ROWS = ['.length, rowsEnd)
const today = new Date().toISOString().slice(0, 10)
const cfg = eval('(' + cfgText + ')')
const rows = eval('[' + rowsText)
// approval injection
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
let sql = "-- \u91c7\u8d2d\u8ba2\u5355\u9762\u677f\uff08PU_ORDER\uff0c\u5bf9\u9f50 h4t PU02\uff0c2026-08-20\uff09\n"
sql += "INSERT INTO panel_config (panel_code, panel_name, category, config) VALUES ('PU_ORDER', '\u91c7\u8d2d\u8ba2\u5355', '\u5355\u636e', '" + esc(JSON.stringify(cfg)) + "') ON DUPLICATE KEY UPDATE config = VALUES(config);\n"
for (const row of rows) {
  const { detail, ...head } = row
  const no = row['\u7f16\u53f7']
  const status = row['\u5355\u636e\u72b6\u6001'] === '\u8349\u7a3f' ? '\u8349\u7a3f' : '\u5df2\u5ba1\u6838'
  const audited = row['\u5ba1\u6838\u4eba'] ? "'admin', NOW()" : 'NULL, NULL'
  sql += "DELETE FROM form_data WHERE panel_code='PU_ORDER' AND form_no='" + no + "';\n"
  sql += "INSERT INTO form_data (panel_code, form_no, data, detail_data, status, create_by, create_time, audit_by, audit_time) VALUES ('PU_ORDER', '" + no + "', '" + esc(JSON.stringify(head)) + "', '" + esc(JSON.stringify(detail)) + "', '" + status + "', 'admin', NOW(), " + audited + ");\n"
}
fs.writeFileSync('F:/INCER/light-mes/tools/update-puorder.sql', sql)
console.log('PU_ORDER sql generated, bytes:', Buffer.byteLength(sql))