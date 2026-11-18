// patch-menus.cjs: add purchase/distribution menus + 销货单 + 领料申请单
const fs = require('node:fs')
const p = 'F:/INCER/light-mes/frontend/src/business/menus.js'
let s = fs.readFileSync(p, 'utf8')
// 1. 销售管理 单据 group: add 销货单 after 销售订单
const soFrom = "{ code: 'salesOrder', title: '\u9500\u552e\u8ba2\u5355', path: '/panelx/list/SO_ORDER', icon: 'Tickets', panelCode: 'SO_ORDER', operationName: '\u65b0\u589e\u6d41\u7a0b' }"
const soTo = "{ code: 'salesOrder', title: '\u9500\u552e\u8ba2\u5355', path: '/panelx/list/SO_ORDER', icon: 'Tickets', panelCode: 'SO_ORDER', operationName: '\u65b0\u589e\u6d41\u7a0b' },\n          { code: 'saleInv', title: '\u9500\u8d27\u5355', path: '/panelx/list/SALE_INV', icon: 'Tickets', panelCode: 'SALE_INV', operationName: '\u65b0\u589e\u6d41\u7a0b' }"
if (!s.includes(soFrom)) { console.error('SO menu not found'); process.exit(1) }
s = s.replace(soFrom, soTo)
// 2. 采购管理 + 配货管理 groups after 销售管理 block (insert before 库存核算 group)
const invFrom = "      {\n        code: 'invAcct',"
const invTo = "      {\n        code: 'purchase',\n        title: '\u91c7\u8d2d\u7ba1\u7406',\n        icon: 'ShoppingCart',\n        children: [\n          {\n            code: 'doc', title: '\u5355\u636e', children: [\n              { code: 'puOrder', title: '\u91c7\u8d2d\u8ba2\u5355', path: '/panelx/list/PU_ORDER', icon: 'Tickets', panelCode: 'PU_ORDER', operationName: '\u65b0\u589e\u6d41\u7a0b' },\n              { code: 'puIn', title: '\u8fdb\u8d27\u5355', path: '/panelx/list/PU_IN', icon: 'Tickets', panelCode: 'PU_IN', operationName: '\u65b0\u589e\u6d41\u7a0b' },\n            ],\n          },\n        ],\n      },\n      {\n        code: 'distribution',\n        title: '\u914d\u8d27\u7ba1\u7406',\n        icon: 'Box',\n        children: [\n          {\n            code: 'doc', title: '\u5355\u636e', children: [\n              { code: 'pickOrder', title: '\u914d\u8d27\u5355', path: '/panelx/list/PICK_ORDER', icon: 'Tickets', panelCode: 'PICK_ORDER', operationName: '\u65b0\u589e\u6d41\u7a0b' },\n            ],\n          },\n        ],\n      },\n      {\n        code: 'invAcct',"
if (!s.includes(invFrom)) { console.error('invAcct anchor not found'); process.exit(1) }
s = s.replace(invFrom, invTo)
// 3. 库存核算 单据 group: add 领料申请单
const mrFrom = "{ code: 'otherOut', title: '\u5176\u4ed6\u51fa\u5e93\u5355', path: '/panelx/list/OTHER_OUT', icon: 'Upload', panelCode: 'OTHER_OUT', operationName: '\u65b0\u589e\u6d41\u7a0b' }"
const mrTo = "{ code: 'otherOut', title: '\u5176\u4ed6\u51fa\u5e93\u5355', path: '/panelx/list/OTHER_OUT', icon: 'Upload', panelCode: 'OTHER_OUT', operationName: '\u65b0\u589e\u6d41\u7a0b' },\n              { code: 'materialReq', title: '\u9886\u6599\u7533\u8bf7\u5355', path: '/panelx/list/MATERIAL_REQ', icon: 'Upload', panelCode: 'MATERIAL_REQ', operationName: '\u65b0\u589e\u6d41\u7a0b' }"
if (!s.includes(mrFrom)) { console.error('OTHER_OUT menu not found'); process.exit(1) }
s = s.replace(mrFrom, mrTo)
fs.writeFileSync(p, s)
console.log('menus.js updated')