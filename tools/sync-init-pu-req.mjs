// 同步 init.sql：sys_menu 加请购单 + panel_config 加 PU_REQ
import fs from 'node:fs'
const file = 'F:/INCER/light-mes/backend/src/main/resources/db/init.sql'
let sql = fs.readFileSync(file, 'utf8')
// 1. sys_menu：在 INSERT 语句最后一个 VALUES 行前加请购单
const menuInsert = "INSERT INTO sys_menu (id, parent_id, code, title, path, icon, sort) VALUES"
const mi = sql.indexOf(menuInsert)
if (mi < 0) throw new Error('sys_menu INSERT not found')
const mEnd = sql.indexOf(';', mi)
const stmt = sql.slice(mi, mEnd)
// 最后一行是 (...); 或 (...)
const lastLineIdx = stmt.lastIndexOf('\n')
const lastLine = stmt.slice(lastLineIdx + 1).trimEnd()
if (!lastLine.endsWith(')')) throw new Error('unexpected last line: ' + lastLine.slice(0, 60))
const newStmt = stmt.slice(0, lastLineIdx + 1) +
  ",\n(412, 161, 'puReq', '请购单', '/scm/purchase/puReq', 'Tickets', 0)\n);"
sql = sql.slice(0, mi) + newStmt + sql.slice(mEnd)
// 2. 追加 PU_REQ panel_config INSERT
const cfgSql = fs.readFileSync('F:/INCER/light-mes/tools/pu_req-panel.sql', 'utf8')
sql = sql.replace(/\n$/, '') + '\n\n-- 请购单面板（PU_REQ，2026-08-24 对齐 T+ 机械行业实测）\n' + cfgSql
fs.writeFileSync(file, sql, 'utf8')
console.log('init.sql synced: sys_menu puReq + PU_REQ panel config')