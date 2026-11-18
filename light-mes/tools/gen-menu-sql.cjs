// gen-menu-sql.cjs: generate sys_menu inserts for new panels
const fs = require('node:fs')
const rows = [
  [164, 102, 'saleInv', '\u9500\u8d27\u5355', '/scm/sales/saleInv', 'Tickets', 2],
  [165, 121, 'materialReq', '\u9886\u6599\u7533\u8bf7\u5355', '/scm/inv/materialReq', 'Upload', 7],
  [160, 100, 'purchase', '\u91c7\u8d2d\u7ba1\u7406', null, 'ShoppingCart', 3],
  [161, 160, 'purchaseDoc', '\u5355\u636e', null, null, 1],
  [162, 161, 'puOrder', '\u91c7\u8d2d\u8ba2\u5355', '/scm/purchase/puOrder', 'Tickets', 1],
  [163, 161, 'puIn', '\u8fdb\u8d27\u5355', '/scm/purchase/puIn', 'Tickets', 2],
  [170, 100, 'distribution', '\u914d\u8d27\u7ba1\u7406', null, 'Box', 4],
  [171, 170, 'distDoc', '\u5355\u636e', null, null, 1],
  [172, 171, 'pickOrder', '\u914d\u8d27\u5355', '/scm/dist/pickOrder', 'Tickets', 1],
]
let sql = '-- \u65b0\u9762\u677f\u83dc\u5355\u540c\u6b65\uff082026-08-20\uff09\n'
for (const [id, pid, code, title, path, icon, sort] of rows) {
  const pv = path ? "'" + path + "'" : 'NULL'
  const iv = icon ? "'" + icon + "'" : 'NULL'
  sql += "INSERT INTO sys_menu (id, parent_id, code, title, path, icon, sort) VALUES (" + id + ", " + pid + ", '" + code + "', '" + title + "', " + pv + ", " + iv + ", " + sort + ") ON DUPLICATE KEY UPDATE title = VALUES(title), path = VALUES(path);\n"
}
fs.writeFileSync('F:/INCER/light-mes/tools/update-menu.sql', sql)
console.log('menu sql generated')