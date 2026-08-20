// gen-stock-sql.cjs v2: generate stock-6 INSERT..ODKU SQL (panel_config) from engine.js INV configs
const fs = require('node:fs')
const eng = fs.readFileSync('F:/INCER/light-mes/frontend/src/business/engine.js', 'utf8')
const start = eng.indexOf('const INVENTORY_OPTIONS = {')
const end = eng.indexOf('const PROCESS_REPORT_GRID_COLUMNS')
if (start < 0 || end < 0) { console.error('segment anchors missing'); process.exit(1) }
const seg = eng.slice(start, end)
const deps = `const today = new Date().toISOString().slice(0, 10);
const WORKSHOP_OPTIONS = ['\u7194\u94f8\u8f66\u95f4', '\u8f67\u5236\u8f66\u95f4', '\u7cbe\u6574\u8f66\u95f4', '\u6d4b\u8bd5\u8f66\u95f4'];
`
const cfgs = eval(deps + seg + ';\n({ PURCHASE_IN: PURCHASE_IN_CONFIG, FINISH_IN: FINISH_IN_CONFIG, OTHER_IN: OTHER_IN_CONFIG, SALE_OUT: SALE_OUT_CONFIG, MATERIAL_OUT: MATERIAL_OUT_CONFIG, OTHER_OUT: OTHER_OUT_CONFIG })')
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
const NAMES = { PURCHASE_IN: '\u91c7\u8d2d\u5165\u5e93\u5355', FINISH_IN: '\u4ea7\u6210\u54c1\u5165\u5e93\u5355', OTHER_IN: '\u5176\u4ed6\u5165\u5e93\u5355', SALE_OUT: '\u9500\u552e\u51fa\u5e93\u5355', MATERIAL_OUT: '\u6750\u6599\u51fa\u5e93\u5355', OTHER_OUT: '\u5176\u4ed6\u51fa\u5e93\u5355' }
let sql = '-- \u5e93\u5b58\u6838\u7b97 6 \u5355\u636e\u9762\u677f\u540c\u6b65\uff08\u5bf9\u9f50 h4t \u673a\u68b0\u884c\u4e1a\u5b9e\u6d4b\uff0c2026-08-19\uff09\n'
for (const [code, cfg0] of Object.entries(cfgs)) {
  const cfg = inject(cfg0)
  sql += "INSERT INTO panel_config (panel_code, panel_name, category, config) VALUES ('" + code + "', '" + NAMES[code] + "', '\u5355\u636e', '" + esc(JSON.stringify(cfg)) + "') ON DUPLICATE KEY UPDATE config = VALUES(config);\n"
  console.log(code + ': ' + cfg.metadata.panelName + ' config ' + JSON.stringify(cfg).length + ' bytes')
}
fs.writeFileSync('F:/INCER/light-mes/tools/update-stock.sql', sql)
console.log('sql written, bytes:', Buffer.byteLength(sql))