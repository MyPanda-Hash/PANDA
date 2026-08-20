// patch-menu-init3.cjs: add menu seeds (CRLF-safe)
const fs = require('node:fs')
const p = 'F:/INCER/light-mes/backend/src/main/resources/db/init.sql'
let s = fs.readFileSync(p, 'utf8')
const r1 = /\(103, 102, 'salesOrder'[\s\S]*?\),\r?\n/
const to1 = "(103, 102, 'salesOrder',          '\u9500\u552e\u8ba2\u5355',           '/scm/sales/salesOrder',          'Tickets',        1),\n(164, 102, 'saleInv',             '\u9500\u8d27\u5355',             '/scm/sales/saleInv',             'Tickets',        2),\n"
if (!r1.test(s)) { console.error('salesOrder miss'); process.exit(1) }
s = s.replace(r1, to1)
const r2 = /\(127, 121, 'otherOut'[\s\S]*?\),\r?\n/
const to2 = "(127, 121, 'otherOut',            '\u5176\u4ed6\u51fa\u5e93\u5355',         '/scm/inv/otherOut',              'Upload',         6),\n(165, 121, 'materialReq',          '\u9886\u6599\u7533\u8bf7\u5355',       '/scm/inv/materialReq',            'Upload',         7),\n"
if (!r2.test(s)) { console.error('otherOut miss'); process.exit(1) }
s = s.replace(r2, to2)
const r3 = /\(120, 100, 'invAcct'[\s\S]*?\),\r?\n/
const to3 = "(160, 100, 'purchase',            '\u91c7\u8d2d\u7ba1\u7406',           NULL,                             'ShoppingCart',   3),\n(161, 160, 'purchaseDoc',         '\u5355\u636e',               NULL,                             NULL,             1),\n(162, 161, 'puOrder',             '\u91c7\u8d2d\u8ba2\u5355',           '/scm/purchase/puOrder',           'Tickets',        1),\n(163, 161, 'puIn',                '\u8fdb\u8d27\u5355',             '/scm/purchase/puIn',              'Tickets',        2),\n(170, 100, 'distribution',        '\u914d\u8d27\u7ba1\u7406',           NULL,                             'Box',            4),\n(171, 170, 'distDoc',             '\u5355\u636e',               NULL,                             NULL,             1),\n(172, 171, 'pickOrder',           '\u914d\u8d27\u5355',             '/scm/dist/pickOrder',             'Tickets',        1),\n(120, 100, 'invAcct',             '\u5e93\u5b58\u6838\u7b97',           NULL,                             'Box',            2),\n"
if (!r3.test(s)) { console.error('invAcct miss'); process.exit(1) }
s = s.replace(r3, to3)
fs.writeFileSync(p, s)
console.log('init.sql menu seeds added')