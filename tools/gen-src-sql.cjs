// gen-src-sql.cjs v3: generate 4 source panels config+rows SQL from engine.js
const fs = require('node:fs')
const eng = fs.readFileSync('F:/INCER/light-mes/frontend/src/business/engine.js', 'utf8')
const today = new Date().toISOString().slice(0, 10)
const clean = (t) => t.replace(/\/\/[^\n]*/g, '').replace(/\/\*[\s\S]*?\*\//g, '')
const APPROVAL_ACTIONS = ['\u63d0\u4ea4\u5ba1\u6279', '\u5ba1\u6279\u901a\u8fc7', '\u9a73\u56de\u5ba1\u6279']
const inject = (cfg) => {
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
  return cfg
}
const esc = (s) => String(s).replace(/'/g, "''")
const PANELS = [
  ['PU_IN', '\u8fdb\u8d27\u5355'],
  ['SALE_INV', '\u9500\u8d27\u5355'],
  ['PICK_ORDER', '\u914d\u8d27\u5355'],
  ['MATERIAL_REQ', '\u9886\u6599\u7533\u8bf7\u5355'],
]
let sql = '-- 4 \u9009\u5355\u6e90\u9762\u677f\u540c\u6b65\uff082026-08-20\uff09\n'
for (const [code, name] of PANELS) {
  const cfgStart = eng.indexOf('const ' + code + '_CONFIG = {')
  const cfgEnd = eng.indexOf('\nlet ' + code + '_ROWS', cfgStart)
  if (cfgStart < 0 || cfgEnd < 0) { console.error(code + ' anchors missing'); process.exit(1) }
  const cfgText = eng.slice(cfgStart + 'const '.length, cfgEnd)
  const rowsStart = eng.indexOf('let ' + code + '_ROWS = [', cfgEnd)
  let rowsEnd = rowsStart, depth = 0; for (let i = rowsStart; i < eng.length; i++) { if (eng[i] === '[') depth++; else if (eng[i] === ']') { depth--; if (depth === 0) { rowsEnd = i + 1; break } } }
  const rowsText = eng.slice(rowsStart + 'let '.length, rowsEnd)
  const cfg = eval('(' + clean(cfgText) + ')')
  const rows = eval(clean(rowsText))
  const c2 = inject(cfg)
  sql += "INSERT INTO panel_config (panel_code, panel_name, category, config) VALUES ('" + code + "', '" + name + "', '\u5355\u636e', '" + esc(JSON.stringify(c2)) + "') ON DUPLICATE KEY UPDATE config = VALUES(config);\n"
  for (const row of rows) {
    const { detail, ...head } = row
    const no = row['\u7f16\u53f7']
    const status = row['\u5355\u636e\u72b6\u6001'] === '\u8349\u7a3f' ? '\u8349\u7a3f' : '\u5df2\u5ba1\u6838'
    const audited = row['\u5ba1\u6838\u4eba'] ? "'admin', NOW()" : 'NULL, NULL'
    sql += "DELETE FROM form_data WHERE panel_code='" + code + "' AND form_no='" + no + "';\n"
    sql += "INSERT INTO form_data (panel_code, form_no, data, detail_data, status, create_by, create_time, audit_by, audit_time) VALUES ('" + code + "', '" + no + "', '" + esc(JSON.stringify(head)) + "', '" + esc(JSON.stringify(detail)) + "', '" + status + "', 'admin', NOW(), " + audited + ");\n"
  }
  console.log(code + ': ' + cfg.metadata.panelName + ' ok, rows=' + rows.length)
}
fs.writeFileSync('F:/INCER/light-mes/tools/update-src.sql', sql)
console.log('sql bytes:', Buffer.byteLength(sql))