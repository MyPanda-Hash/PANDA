// gen-stock-seed.cjs: sync 6 stock panels form_data seeds from engine.js INV_SEED
const fs = require('node:fs')
const eng = fs.readFileSync('F:/INCER/light-mes/frontend/src/business/engine.js', 'utf8')
// extract I() + INV_SEED block
const iStart = eng.indexOf('const I = (o) =>')
const seedEnd = eng.indexOf('\n\n', eng.indexOf('const INV_SEED = {'))
if (iStart < 0 || seedEnd < 0) { console.error('anchors missing'); process.exit(1) }
const seg = eng.slice(iStart, seedEnd)
const deps = `const today = new Date().toISOString().slice(0, 10);
const INVENTORY_OPTIONS = {
  warehouses: ['\u539f\u6599\u4ed3', '\u8f85\u6599\u4ed3', '\u6210\u54c1\u4ed3', '\u534a\u6210\u54c1\u4ed3', '\u4e0d\u826f\u54c1\u4ed3'],
  inventory: ['\u94dd\u68d2 \u03a680', '\u94dd\u677f 6061', '\u94dd\u578b\u6750-\u6563\u70ed\u7247', '\u51cf\u901f\u7bb1\u4f53 A', '\u8f74\u5957 C', '6061\u94dd\u952d', '\u5207\u524a\u6db2', '\u5305\u88c5\u6728\u7bb1'],
  inventoryCode: ['CP001', 'CP002', 'CP003', 'CP004', 'CP005', 'CL001', 'CL002', 'CL004', 'CL005'],
  units: ['\u4ef6', 'kg', '\u5957', '\u5347'],
  suppliers: ['\u534e\u4e1c\u94dd\u4e1a', '\u4e2d\u5929\u7cbe\u5de5', '\u897f\u90e8\u6750\u6599', '\u5357\u65b9\u91cd\u5de5', '\u5317\u65b9\u673a\u68b0'],
  customers: ['\u534e\u4e1c\u94dd\u4e1a', '\u4e2d\u5929\u7cbe\u5de5', '\u897f\u90e8\u6750\u6599', '\u5357\u65b9\u91cd\u5de5', '\u5317\u65b9\u673a\u68b0'],
  depts: ['\u9500\u552e\u4e00\u90e8', '\u9500\u552e\u4e8c\u90e8', '\u56fd\u9645\u90e8'],
  bizTypes: { IN: ['\u91c7\u8d2d\u5165\u5e93', '\u4ea7\u6210\u54c1\u5165\u5e93', '\u5176\u4ed6\u5165\u5e93'], OUT: ['\u9500\u552e\u51fa\u5e93', '\u6750\u6599\u51fa\u5e93', '\u5176\u4ed6\u51fa\u5e93'] },
  workshops: ['\u7194\u94f8\u8f66\u95f4', '\u8f67\u5236\u8f66\u95f4', '\u7cbe\u6574\u8f66\u95f4', '\u6d4b\u8bd5\u8f66\u95f4'],
  persons: ['\u5f20\u4f1f', '\u674e\u5a1c', '\u738b\u82b3', '\u9648\u5f3a'],
  taxRate: [0, 3, 6, 9, 13],
}
const WORKSHOP_OPTIONS = ['\u7194\u94f8\u8f66\u95f4', '\u8f67\u5236\u8f66\u95f4', '\u7cbe\u6574\u8f66\u95f4', '\u6d4b\u8bd5\u8f66\u95f4'];
`
const clean = (t) => t.replace(/\/\/[^\n]*/g, '').replace(/\/\*[\s\S]*?\*\//g, '')
const seed = eval(deps + clean(seg) + ';\nINV_SEED')
const esc = (s) => String(s).replace(/'/g, "''")
let sql = '-- 6 \u5e93\u5b58\u5355\u636e\u79cd\u5b50\u540c\u6b65\uff082026-08-20\uff09\n'
for (const [code, rows] of Object.entries(seed)) {
  for (const row of rows) {
    const { detail, ...head } = row
    const no = row['\u7f16\u53f7']
    const status = row['\u5355\u636e\u72b6\u6001'] || '\u8349\u7a3f'
    const audited = row['\u5ba1\u6838\u4eba'] ? "'admin', NOW()" : 'NULL, NULL'
    sql += "DELETE FROM form_data WHERE panel_code='" + code + "' AND form_no='" + no + "';\n"
    sql += "INSERT INTO form_data (panel_code, form_no, data, detail_data, status, create_by, create_time, audit_by, audit_time) VALUES ('" + code + "', '" + no + "', '" + esc(JSON.stringify(head)) + "', '" + esc(JSON.stringify(detail)) + "', '" + status + "', 'admin', NOW(), " + audited + ");\n"
  }
  console.log(code + ': ' + rows.length + ' rows')
}
fs.writeFileSync('F:/INCER/light-mes/tools/update-stock-seed.sql', sql)
console.log('sql bytes:', Buffer.byteLength(sql))