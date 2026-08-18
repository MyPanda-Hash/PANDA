export const menuTree = [
  {
    code: 'dashboard',
    title: '我的桌面',
    path: '/dashboard',
    icon: 'HomeFilled',
  },
  {
    code: 'scm',
    title: '智能供应链',
    icon: 'Connection',
    children: [
      {
        code: 'sales',
        title: '销售管理',
        icon: 'ShoppingCart',
        children: [
          { code: 'cat', title: '单据', children: [{ code: 'salesOrder', title: '销售订单', path: '/panelx/list/SO_ORDER', icon: 'Tickets', panelCode: 'SO_ORDER', operationName: '新增流程' }] },
          { code: 'detail', title: '明细表', children: [{ code: 'salesOrderDetail', title: '销售订单明细表', path: '/panelx/list/SALES_ORDER_DETAIL', panelCode: 'SALES_ORDER_DETAIL', icon: 'List' }] },
          { code: 'stats', title: '统计表', children: [{ code: 'salesOrderStats', title: '销售订单统计表', path: '/panelx/list/SALES_ORDER_STATS', panelCode: 'SALES_ORDER_STATS', icon: 'Histogram' }] },
          { code: 'exec', title: '执行表', children: [{ code: 'salesOrderExec', title: '销售订单执行表', path: '/panelx/list/SALES_ORDER_EXEC', panelCode: 'SALES_ORDER_EXEC', icon: 'Operation' }] },
          { code: 'more', title: '更多', children: [{ code: 'salesOrderProgress', title: '销售订单生产进度表', path: '/panelx/list/SALES_ORDER_PROGRESS', panelCode: 'SALES_ORDER_PROGRESS', icon: 'TrendCharts' }] },
        ],
      },
      {
        code: 'invAcct',
        title: '库存核算',
        icon: 'Box',
        children: [
          {
            code: 'doc', title: '单据', children: [
              { code: 'purchaseIn', title: '采购入库单', path: '/panelx/list/PURCHASE_IN', icon: 'Download', panelCode: 'PURCHASE_IN', operationName: '新增流程' },
              { code: 'finishIn', title: '产成品入库单', path: '/panelx/list/FINISH_IN', icon: 'Download', panelCode: 'FINISH_IN', operationName: '新增流程' },
              { code: 'otherIn', title: '其他入库单', path: '/panelx/list/OTHER_IN', icon: 'Download', panelCode: 'OTHER_IN', operationName: '新增流程' },
              { code: 'saleOut', title: '销售出库单', path: '/panelx/list/SALE_OUT', icon: 'Upload', panelCode: 'SALE_OUT', operationName: '新增流程' },
              { code: 'materialOut', title: '材料出库单', path: '/panelx/list/MATERIAL_OUT', icon: 'Upload', panelCode: 'MATERIAL_OUT', operationName: '新增流程' },
              { code: 'otherOut', title: '其他出库单', path: '/panelx/list/OTHER_OUT', icon: 'Upload', panelCode: 'OTHER_OUT', operationName: '新增流程' },
            ],
          },
          {
            code: 'detail', title: '明细表', children: [
              { code: 'purchaseInDetail', title: '采购入库明细表', path: '/panelx/list/PURCHASE_IN_DETAIL', panelCode: 'PURCHASE_IN_DETAIL', icon: 'List' },
              { code: 'finishInDetail', title: '产成品入库明细表', path: '/panelx/list/FINISH_IN_DETAIL', panelCode: 'FINISH_IN_DETAIL', icon: 'List' },
              { code: 'otherInDetail', title: '其他入库明细表', path: '/panelx/list/OTHER_IN_DETAIL', panelCode: 'OTHER_IN_DETAIL', icon: 'List' },
              { code: 'saleOutDetail', title: '销售出库明细表', path: '/panelx/list/SALE_OUT_DETAIL', panelCode: 'SALE_OUT_DETAIL', icon: 'List' },
              { code: 'materialOutDetail', title: '材料出库明细表', path: '/panelx/list/MATERIAL_OUT_DETAIL', panelCode: 'MATERIAL_OUT_DETAIL', icon: 'List' },
              { code: 'otherOutDetail', title: '其他出库明细表', path: '/panelx/list/OTHER_OUT_DETAIL', panelCode: 'OTHER_OUT_DETAIL', icon: 'List' },
            ],
          },
          {
            code: 'stats', title: '统计表', children: [
              { code: 'purchaseInStats', title: '采购入库统计表', path: '/panelx/list/PURCHASE_IN_STATS', panelCode: 'PURCHASE_IN_STATS', icon: 'Histogram' },
              { code: 'finishInStats', title: '产成品入库统计表', path: '/panelx/list/FINISH_IN_STATS', panelCode: 'FINISH_IN_STATS', icon: 'Histogram' },
              { code: 'otherInStats', title: '其他入库统计表', path: '/panelx/list/OTHER_IN_STATS', panelCode: 'OTHER_IN_STATS', icon: 'Histogram' },
              { code: 'saleOutStats', title: '销售出库统计表', path: '/panelx/list/SALE_OUT_STATS', panelCode: 'SALE_OUT_STATS', icon: 'Histogram' },
              { code: 'materialOutStats', title: '材料出库统计表', path: '/panelx/list/MATERIAL_OUT_STATS', panelCode: 'MATERIAL_OUT_STATS', icon: 'Histogram' },
              { code: 'otherOutStats', title: '其他出库统计表', path: '/panelx/list/OTHER_OUT_STATS', panelCode: 'OTHER_OUT_STATS', icon: 'Histogram' },
            ],
          },
          {
            code: 'cost', title: '成本核算', children: [
              { code: 'costMaintain', title: '成本手工维护', path: '/panelx/list/COST_MAINTAIN', panelCode: 'COST_MAINTAIN', icon: 'Money' },
            ],
          },
          {
            code: 'ledger', title: '库存账表', children: [
              { code: 'stockStatus', title: '库存状况表', path: '/panelx/list/STOCK_STATUS', panelCode: 'STOCK_STATUS', icon: 'DataAnalysis' },
              { code: 'stockSummary', title: '收发存汇总表', path: '/panelx/list/STOCK_SUMMARY', panelCode: 'STOCK_SUMMARY', icon: 'DataAnalysis' },
              { code: 'stockLedger', title: '库存台账', path: '/panelx/list/STOCK_LEDGER', panelCode: 'STOCK_LEDGER', icon: 'Notebook' },
            ],
          },
        ],
      },
    ],
  },
  {
    code: 'mfg',
    title: '新生产',
    icon: 'Odometer',
    children: [
      {
        code: 'prod',
        title: '生产管理',
        icon: 'Cpu',
        children: [
          {
            code: 'doc', title: '单据', children: [
              { code: 'manufactureOrder', title: '生产加工单', path: '/panelx/list/MANU_ORDER', icon: 'Document', panelCode: 'MANU_ORDER', operationName: '新增流程' },
              { code: 'manufactureOrderProc', title: '生产加工单工序统计表', path: '/panelx/list/MANU_PROC_STATS', panelCode: 'MANU_PROC_STATS', icon: 'Histogram' },
            ],
          },
          {
            code: 'report', title: '报表', children: [
              { code: 'manufactureDetail', title: '生产加工单明细表', path: '/panelx/list/MANU_ORDER_DETAIL', panelCode: 'MANU_ORDER_DETAIL', icon: 'List' },
              { code: 'manufactureStats', title: '生产加工单统计表', path: '/panelx/list/MANU_ORDER_STATS', panelCode: 'MANU_ORDER_STATS', icon: 'Histogram' },
              { code: 'manufactureBoard', title: '生产看板', path: '/prod/manufacture/board', icon: 'Monitor' },
            ],
          },
        ],
      },
      {
        code: 'smartShop',
        title: '智慧车间',
        icon: 'MagicStick',
        children: [
          {
            code: 'doc', title: '单据', children: [
              { code: 'procReport', title: '工序汇报单', path: '/panelx/list/PROCESS_REPORT', icon: 'EditPen', panelCode: 'PROCESS_REPORT', operationName: '新增流程' },
              { code: 'reworkReport', title: '返修工序汇报单', path: '/panelx/list/REWORK_REPORT', panelCode: 'REWORK_REPORT', icon: 'EditPen' },
              { code: 'reworkDesk', title: '返修工作台', path: '/prod/shop/reworkDesk', icon: 'Tools' },
            ],
          },
          {
            code: 'report', title: '报表', children: [
              { code: 'procDetail', title: '工序明细表', path: '/panelx/list/PROC_DETAIL', panelCode: 'PROC_DETAIL', icon: 'List' },
              { code: 'procStats', title: '工序统计表', path: '/panelx/list/PROC_STATS', panelCode: 'PROC_STATS', icon: 'Histogram' },
              { code: 'salaryDetail', title: '工资明细表', path: '/panelx/list/SALARY_DETAIL', panelCode: 'SALARY_DETAIL', icon: 'List' },
              { code: 'salaryStats', title: '工资统计表', path: '/panelx/list/SALARY_STATS', panelCode: 'SALARY_STATS', icon: 'Histogram' },
            ],
          },
        ],
      },
    ],
  },
  {
    code: 'foundation',
    title: '基础设置',
    icon: 'Setting',
    children: [
      {
        code: 'base',
        title: '基础设置',
        icon: 'Collection',
        children: [
          {
            code: 'info', title: '基本信息', children: [
              { code: 'dept', title: '部门', path: '/panelx/list/DEPT', icon: 'OfficeBuilding', panelCode: 'DEPT' },
              { code: 'employee', title: '员工', path: '/panelx/list/EMP', icon: 'User', panelCode: 'EMP' },
              { code: 'partner', title: '往来单位', path: '/panelx/list/PARTNER', icon: 'OfficeBuilding', panelCode: 'PARTNER' },
              { code: 'uom', title: '计量单位', path: '/panelx/list/UOM', icon: 'ScaleToOriginal', panelCode: 'UOM' },
              { code: 'inventory', title: '存货', path: '/panelx/list/INV', icon: 'Grid', panelCode: 'INV' },
              { code: 'equip', title: '设备', path: '/panelx/list/EQUIP', icon: 'Cpu', panelCode: 'EQUIP' },
              { code: 'team', title: '班组', path: '/panelx/list/TEAM', icon: 'UserFilled', panelCode: 'TEAM' },
              { code: 'wc', title: '工作中心', path: '/panelx/list/WC', icon: 'Odometer', panelCode: 'WC' },
              { code: 'process', title: '工序', path: '/panelx/list/OP', icon: 'SetUp', panelCode: 'OP' },
              { code: 'opConv', title: '工序辅单位换算率设置', path: '/panelx/list/OP_CONV', icon: 'RefreshRight', panelCode: 'OP_CONV' },
              { code: 'routing', title: '工艺路线', path: '/panelx/list/ROUTE', icon: 'Guide', panelCode: 'ROUTE' },
              { code: 'bom', title: '物料清单', path: '/panelx/list/BOM', icon: 'Files', panelCode: 'BOM' },
              { code: 'bomFwd', title: '物料清单正向查询', path: '/panelx/list/BOM_FWD', icon: 'TopRight', panelCode: 'BOM_FWD' },
              { code: 'bomRev', title: '物料清单反向查询', path: '/panelx/list/BOM_REV', icon: 'BottomLeft', panelCode: 'BOM_REV' },
              { code: 'warehouse', title: '仓库', path: '/panelx/list/WH', icon: 'House', panelCode: 'WH' },
              { code: 'region', title: '地区', path: '/panelx/list/REGION', icon: 'Location', panelCode: 'REGION' },
              { code: 'proj', title: '项目', path: '/panelx/list/PROJ', icon: 'Flag', panelCode: 'PROJ' },
              { code: 'reject', title: '不合格原因', path: '/panelx/list/REJECT', icon: 'CircleClose', panelCode: 'REJECT' },
            ],
          },
          {
            code: 'price', title: '价格信息', children: [
              { code: 'invPrice', title: '存货价格本', path: '/panelx/list/INV_PRICE', icon: 'PriceTag', panelCode: 'INV_PRICE' },
            ],
          },
        ],
      },
      {
        code: 'sys',
        title: '系统管理',
        icon: 'Tools',
        children: [
          {
            code: 'basic', title: '基本设置', children: [
              { code: 'options', title: '选项设置', path: '/panelx/list/SYS_OPT', icon: 'Setting', panelCode: 'SYS_OPT' },
              { code: 'boardAuth', title: '看板授权', path: '/panelx/list/SYS_BOARD_AUTH', icon: 'Key', panelCode: 'SYS_BOARD_AUTH' },
            ],
          },
          {
            code: 'doc', title: '单据档案设置', children: [
              { code: 'docDesign', title: '单据设计', path: '/panelx/list/SYS_BILL_DESIGN', icon: 'Brush', panelCode: 'SYS_BILL_DESIGN' },
              { code: 'mobileDoc', title: '移动单据设置', path: '/panelx/list/SYS_MOBILE', icon: 'Iphone', panelCode: 'SYS_MOBILE' },
              { code: 'mobileTpl', title: '移动模板设置', path: '/panelx/list/SYS_MOBILE_TPL', icon: 'Iphone', panelCode: 'SYS_MOBILE_TPL' },
              { code: 'coding', title: '单据编码设置', path: '/panelx/list/SYS_CODE', icon: 'Sort', panelCode: 'SYS_CODE' },
              { code: 'print', title: '打印管理中心', path: '/panelx/list/SYS_PRINT', icon: 'Printer', panelCode: 'SYS_PRINT' },
              { code: 'printDefault', title: '打印出厂值设置', path: '/panelx/list/SYS_PRINT_DEFAULT', icon: 'Printer', panelCode: 'SYS_PRINT_DEFAULT' },
            ],
          },
          {
            code: 'msg', title: '消息中心及预警设置', children: [
              { code: 'alert', title: '预警设置', path: '/panelx/list/SYS_ALARM', icon: 'Bell', panelCode: 'SYS_ALARM' },
            ],
          },
          {
            code: 'acct', title: '账套管理及工具', children: [
              { code: 'task', title: '任务管理', path: '/panelx/list/SYS_TASK', icon: 'Calendar', panelCode: 'SYS_TASK' },
              { code: 'screen', title: '大屏设备管理', path: '/panelx/list/SYS_SCREEN', icon: 'Monitor', panelCode: 'SYS_SCREEN' },
              { code: 'screenDl', title: '大屏客户端下载中心', path: '/panelx/list/SYS_SCREEN_DL', icon: 'Download', panelCode: 'SYS_SCREEN_DL' },
            ],
          },
        ],
      },
      {
        code: 'init',
        title: '初始化',
        icon: 'CirclePlus',
        children: [
          {
            code: 'balance', title: '期初余额', children: [
              { code: 'stockBalance', title: '库存期初余额', path: '/panelx/list/INIT_BALANCE', icon: 'Coin', panelCode: 'INIT_BALANCE' },
            ],
          },
          {
            code: 'doc', title: '期初单据', children: [
              { code: 'initTempIn', title: '期初暂估入库单', path: '/panelx/list/INIT_AP', icon: 'Download', panelCode: 'INIT_AP' },
              { code: 'initSaleOut', title: '期初销售出库单', path: '/panelx/list/INIT_AR', icon: 'Upload', panelCode: 'INIT_AR' },
            ],
          },
        ],
      },
    ],
  },
  {
    code: 'top',
    title: 'TOP应用',
    icon: 'Platform',
    children: [
      {
        code: 'solution',
        title: '方案中心',
        icon: 'Compass',
        children: [
          { code: 'solutionCenter', title: '方案中心', path: '/top/solution', icon: 'Compass' },
        ],
      },
    ],
  },
]

function walk(node, fn) {
  fn(node)
  if (node.children) node.children.forEach((c) => walk(c, fn))
}

export function flatMenus(tree) {
  const out = []
  walk({ children: tree || menuTree }, (n) => {
    if (n.path) out.push(n)
  })
  return out
}

export function findMenuByPath(path) {
  let hit = null
  walk({ children: menuTree }, (n) => {
    if (n.path === path) hit = n
  })
  return hit
}

export function filterTree(nodes, keyword) {
  const k = keyword.trim()
  if (!k) return nodes
  return nodes
    .map((n) => {
      if (n.children) {
        const children = filterTree(n.children, k)
        return children.length ? { ...n, children } : null
      }
      return n.title.includes(k) ? n : null
    })
    .filter(Boolean)
}

// 角色权限过滤：仅保留 visiblePanels 内的面板叶子；分组节点在子项全不可见时隐藏；admin 返回全量
export function filterMenuTree(tree, visiblePanels, isAdmin) {
  if (isAdmin) return tree
  const vis = Array.isArray(visiblePanels) ? visiblePanels : []
  const filterNode = (nodes) => {
    const out = []
    for (const n of nodes) {
      if (n.panelCode) {
        if (vis.includes(n.panelCode)) out.push({ ...n })
        continue
      }
      if (n.children && n.children.length) {
        const c = filterNode(n.children)
        if (c.length) out.push({ ...n, children: c })
        continue
      }
      if (n.path) out.push({ ...n }) // 非面板固定路由（生产看板等）默认可见
    }
    return out
  }
  return filterNode(tree)
}
