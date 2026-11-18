// patch-menu-init.cjs: add new panels menu seeds to init.sql
const fs = require('node:fs')
const p = 'F:/INCER/light-mes/backend/src/main/resources/db/init.sql'
let s = fs.readFileSync(p, 'utf8')
// 1. 销货单 after 销售订单 (id 164)
const from1 = "(103, 102, 'salesOrder',          '\u9500\u552e\u8ba2\u5355',           '/scm/sales/salesOrder',          'Tickets',        1),"
const to1 = from1 + "\n(164, 102, 'saleInv',             '\u9500\u8d27\u5355',             '/scm/sales/saleInv',             'Tickets',        2),"
if (!s.includes(from1)) { console.error('salesOrder seed not found'); process.exit(1) }
s = s.replace(from1, to1)
// 2. 领料申请单 after 其他出库单 (id 165)
const from2 = "(127, 121, 'otherOut',            '\u5176\u4ed6\u51fa\u5e93\u5355',         '/scm/inv/otherOut',               'Upload',         6),"
const to2 = from2 + "\n(165, 121, 'materialReq',          '\u9886\u6599\u7533\u8bf7\u5355',       '/scm/inv/materialReq',            'Upload',         7),"
if (!s.includes(from2)) { console.error('otherOut seed not found'); process.exit(1) }
s = s.replace(from2, to2)
// 3. 采购管理 + 配货管理 before 库存核算 (ids 160-163, 170-172)
const from3 = "(120, 100, 'invAcct',             '\u5e93\u5b58\u6838\u7b97',           NULL,                             'Box',            2),"
const to3 = "(160, 100, 'purchase',            '\u91c7\u8d2d\u7ba1\u7406',           NULL,                             'ShoppingCart',   3),\n" +
"(161, 160, 'purchaseDoc',         '\u5355\u636e',               NULL,                             NULL,             1),\n" +
"(162, 161, 'puOrder',             '\u91c7\u8d2d\u8ba2\u5355',           '/scm/purchase/puOrder',           'Tickets',        1),\n" +
"(163, 161, 'puIn',                '\u8fdb\u8d27\u5355',             '/scm/purchase/puIn',              'Tickets',        2),\n" +
"(170, 100, 'distribution',        '\u914d\u8d27\u7ba1\u7406',           NULL,                             'Box',            4),\n" +
"(171, 170, 'distDoc',             '\u5355\u636e',               NULL,                             NULL,             1),\n" +
"(172, 171, 'pickOrder',           '\u914d\u8d27\u5355',             '/scm/dist/pickOrder',             'Tickets',        1),\n" +
"(120, 100, 'invAcct',             '\u5e93\u5b58\u6838\u7b97',           NULL,                             'Box',            2),"
if (!s.includes(from3)) { console.error('invAcct seed not found'); process.exit(1) }
s = s.replace(from3, to3)
fs.writeFileSync(p, s)
console.log('init.sql menu seeds added')