import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { CARD_H, CARD_W, GAP_X, canvasHeightOf, defaultHandles, layoutNodes } from '../src/views/scm/flow-layout.js'

/**
 * 采购模块的排版输入：镜像 BusinessOverview.vue 里 code: 'purchase' 的 nodes/pos。
 * 最后一条用例会读源文件核对节点清单，漏改这里会直接失败。
 */
const PURCHASE = {
  nodes: ['PU_REQ', 'PU_ORDER', 'ARRIVAL_IN', 'INSPECTION', 'PURCHASE_IN', 'PURCHASE_RETURN', 'PU_INVOICE', 'RECEIPT_PAYABLE']
    .map((code) => ({ code })),
  pos: {
    PU_REQ: [200, 40], PU_ORDER: [200, 160], ARRIVAL_IN: [200, 280], INSPECTION: [200, 400],
    PURCHASE_IN: [120, 520], PURCHASE_RETURN: [360, 520],
    PU_INVOICE: [200, 640], RECEIPT_PAYABLE: [200, 760],
  },
}

/** 按 y 分行，返回每行的卡片（按 x 排序）。 */
function rowsOf(layout) {
  const byY = new Map()
  for (const [code, p] of Object.entries(layout)) {
    if (!byY.has(p.y)) byY.set(p.y, [])
    byY.get(p.y).push({ code, x: p.x })
  }
  return [...byY.entries()].sort((a, b) => a[0] - b[0]).map(([y, items]) => items.sort((a, b) => a.x - b.x))
}

test('采购主链单列到底，只有「入库 | 退货」一行是两张卡', () => {
  const rows = rowsOf(layoutNodes(PURCHASE))
  assert.deepEqual(rows.map((r) => r.length), [1, 1, 1, 1, 2, 1, 1])
  assert.deepEqual(rows[4].map((c) => c.code), ['PURCHASE_IN', 'PURCHASE_RETURN'])
})

test('单卡行落在双卡行正中间：请购/订单/到货/检验/发票/付款 同一列', () => {
  const rows = rowsOf(layoutNodes(PURCHASE))
  const spine = rows.filter((r) => r.length === 1)
  assert.equal(new Set(spine.map((r) => r[0].x)).size, 1, '主链必须一条竖线，不能左右错开')
  const [left, right] = rows[4]
  assert.equal(left.x + CARD_W + GAP_X, right.x, '分支行两张卡之间只留一个标准间距')
})

test('同一行的卡片不重叠，间距恒为 GAP_X', () => {
  const rows = rowsOf(layoutNodes(PURCHASE))
  for (const row of rows) {
    for (let i = 1; i < row.length; i += 1) {
      assert.equal(row[i].x - row[i - 1].x, CARD_W + GAP_X, `第 ${i} 列与前一列间距不是标准列距`)
    }
  }
})

test('画布高度按最后一行算：7 行 = 6×行距 + 卡高 + 上下留白', () => {
  assert.equal(canvasHeightOf(layoutNodes(PURCHASE)), 6 * (CARD_H + 56) + CARD_H + 80)
})

test('画布高度不受原始 pos 的虚高 Y 影响（不留底部大片空白）', () => {
  const sparse = { nodes: PURCHASE.nodes, pos: { ...PURCHASE.pos, RECEIPT_PAYABLE: [200, 4000] } }
  assert.equal(canvasHeightOf(layoutNodes(sparse)), canvasHeightOf(layoutNodes(PURCHASE)))
})

test('空模块 / 缺 pos 不炸，节点退回左上角', () => {
  assert.deepEqual(layoutNodes(null), {})
  assert.deepEqual(layoutNodes({ nodes: [] }), {})
  assert.deepEqual(layoutNodes({ nodes: [{ code: 'A' }] }), { A: { x: 0, y: 0 } })
})

test('镜像的节点清单与源文件一致（防止改了流程图忘了同步用例）', () => {
  const here = path.dirname(fileURLToPath(import.meta.url))
  const source = fs.readFileSync(path.join(here, '../src/views/scm/BusinessOverview.vue'), 'utf8')
  const start = source.indexOf("code: 'purchase'")
  assert.ok(start > 0, '找不到采购模块配置')
  const block = source.slice(start, source.indexOf('docs:', start))
  const codes = [...block.matchAll(/\{ code: '([A-Z_]+)', label: '/g)].map((m) => m[1])
  assert.deepEqual(codes, PURCHASE.nodes.map((n) => n.code))
})

// ---------- 列模式（质量管理：三条独立链并列） ----------

/** 质检模块的排版输入：镜像 BusinessOverview.vue 里 code: 'qc' 的 layout/gutter/nodes/pos。 */
const QC = {
  layout: 'columns',
  gutter: 104,
  nodes: [
    'ARRIVAL_IN', 'INSPECTION', 'PURCHASE_IN', 'PURCHASE_RETURN',
    'MANU_ORDER', 'FIRST_INSPECT', 'PROCESS_REPORT',
    'FINISH_INSPECT', 'FINISH_IN',
  ].map((code) => ({ code })),
  pos: {
    ARRIVAL_IN: [0, 0], INSPECTION: [0, 1], PURCHASE_IN: [0, 2], PURCHASE_RETURN: [0, 3],
    MANU_ORDER: [1, 0], FIRST_INSPECT: [1, 1], PROCESS_REPORT: [1, 2],
    FINISH_INSPECT: [2, 1], FINISH_IN: [2, 2],
  },
}

test('列模式：x = 左侧留白 + 列号 × 列距，y = 行号 × 行距', () => {
  const layout = layoutNodes(QC)
  assert.deepEqual(layout.ARRIVAL_IN, { x: QC.gutter, y: 0 })
  assert.deepEqual(layout.FINISH_INSPECT, { x: QC.gutter + 2 * (CARD_W + GAP_X), y: CARD_H + 56 })
  assert.deepEqual(layout.PROCESS_REPORT, { x: QC.gutter + CARD_W + GAP_X, y: 2 * (CARD_H + 56) })
})

test('列模式：只有一张卡的行不被重新居中（行模式会居中，列就错位了）', () => {
  const layout = layoutNodes(QC)
  // 第 4 行只有采购退货单，必须仍在第 0 列，而不是被摆到中间列
  assert.equal(layout.PURCHASE_RETURN.x, QC.gutter)
  assert.equal(layout.PURCHASE_RETURN.y, 3 * (CARD_H + 56))
})

test('质检三条链各占一列，链内节点纵向对齐', () => {
  const layout = layoutNodes(QC)
  const columns = [[], [], []]
  for (const [code, p] of Object.entries(layout)) {
    columns[(p.x - QC.gutter) / (CARD_W + GAP_X)].push(code)
  }
  assert.deepEqual(columns[0], ['ARRIVAL_IN', 'INSPECTION', 'PURCHASE_IN', 'PURCHASE_RETURN'])
  assert.deepEqual(columns[1], ['MANU_ORDER', 'FIRST_INSPECT', 'PROCESS_REPORT'])
  assert.deepEqual(columns[2], ['FINISH_INSPECT', 'FINISH_IN'])
})

test('未使用的工序报检单 / 生产过程检验单 不进流程图', () => {
  const codes = Object.keys(layoutNodes(QC))
  assert.ok(!codes.includes('PROCESS_INSPECT_APPLY'), '工序报检单 0 张单，不该出现在流程图里')
  assert.ok(!codes.includes('PROCESS_INSPECTION'), '生产过程检验单 0 张单，不该出现在流程图里')
})

test('首检在报工之前：首件报检单的行号小于工序汇报单', () => {
  const layout = layoutNodes(QC)
  assert.ok(layout.FIRST_INSPECT.y < layout.PROCESS_REPORT.y, '首件报检单必须排在工序汇报单上方（先首检后报工）')
  assert.equal(layout.FIRST_INSPECT.x, layout.PROCESS_REPORT.x, '首检 → 报工是同一条竖线，不拐弯')
})

test('列模式画布高度：4 行 = 3×行距 + 卡高 + 上下留白', () => {
  assert.equal(canvasHeightOf(layoutNodes(QC)), 3 * (CARD_H + 56) + CARD_H + 80)
})

test('质检「相关单据」不再列未使用的两张单（流程图里也没有）', () => {
  const here = path.dirname(fileURLToPath(import.meta.url))
  const source = fs.readFileSync(path.join(here, '../src/views/scm/BusinessOverview.vue'), 'utf8')
  const start = source.indexOf("code: 'qc'")
  const block = source.slice(start, source.indexOf('archives:', start))
  assert.ok(!block.includes("'PROCESS_INSPECT_APPLY'"), '工序报检单不该再出现在质量管理的单据栏')
  assert.ok(!block.includes("'PROCESS_INSPECTION'"), '生产过程检验单不该再出现在质量管理的单据栏')
})

// ---------- 列模式（委外管理：接单 → 发料/结算 + 回厂首检） ----------

/** 委外模块的排版输入：镜像 BusinessOverview.vue 里 code: 'outsource' 的 layout/nodes/pos。 */
const OUTSOURCE = {
  layout: 'columns',
  nodes: ['SO_ORDER', 'OUTSOURCE_ORDER', 'OUTSOURCE_ISSUE', 'FIRST_INSPECT', 'OUTSOURCE_FEE', 'OUTSOURCE_IN']
    .map((code) => ({ code })),
  pos: {
    SO_ORDER: [1, 0], OUTSOURCE_ORDER: [1, 1],
    OUTSOURCE_ISSUE: [0, 2], FIRST_INSPECT: [1, 2], OUTSOURCE_FEE: [2, 2],
    OUTSOURCE_IN: [1, 3],
  },
}

test('委外：接单一条竖线，发料/结算分列两侧，首检夹在入库之前', () => {
  const layout = layoutNodes(OUTSOURCE)
  assert.equal(layout.SO_ORDER.x, layout.OUTSOURCE_ORDER.x, '销售订单 → 委外加工单 是同一条竖线')
  assert.equal(layout.OUTSOURCE_ORDER.x, layout.FIRST_INSPECT.x, '首检在同一列，不拐弯')
  assert.ok(layout.FIRST_INSPECT.y < layout.OUTSOURCE_IN.y, '先回厂首检、再委外入库')
  assert.ok(layout.OUTSOURCE_ISSUE.x < layout.OUTSOURCE_ORDER.x, '发料单在左')
  assert.ok(layout.OUTSOURCE_FEE.x > layout.OUTSOURCE_ORDER.x, '费用单在右')
  assert.equal(layout.OUTSOURCE_ISSUE.y, layout.FIRST_INSPECT.y, '发料/首检/结算同排（都是加工单的下游）')
})

test('委外镜像的节点清单与源文件一致', () => {
  const here = path.dirname(fileURLToPath(import.meta.url))
  const source = fs.readFileSync(path.join(here, '../src/views/scm/BusinessOverview.vue'), 'utf8')
  const start = source.indexOf("code: 'outsource'")
  assert.ok(start > 0, '找不到委外模块配置')
  const block = source.slice(start, source.indexOf('docs:', start))
  const codes = [...block.matchAll(/\{ code: '([A-Z_]+)', label: '/g)].map((m) => m[1])
  assert.deepEqual(codes, OUTSOURCE.nodes.map((n) => n.code))
})

test('连线锚点：目标在左必须左出右进，否则折线会绕卡片留残线', () => {
  const pos = { x: 104, y: 0 }
  const right = defaultHandles(pos, { x: 352, y: 0 })
  assert.deepEqual([right.sourceHandle, right.targetHandle], ['out', 'in'])
  const left = defaultHandles({ x: 352, y: 0 }, pos)
  assert.deepEqual([left.sourceHandle, left.targetHandle], ['out-l', 'in-r'],
    '目标在左边时右出左进会先往右伸再绕回来，就是那条残线')
  const down = defaultHandles(pos, { x: 104, y: 112 })
  assert.deepEqual([down.sourceHandle, down.targetHandle], ['out-b', 'in-t'])
  const up = defaultHandles({ x: 104, y: 112 }, pos)
  assert.deepEqual([up.sourceHandle, up.targetHandle], ['out-t', 'in-b'])
})

test('销售管理：直接销售出库一条闭环，库存/退货/费用三条旁支都接得上', () => {
  const here = path.dirname(fileURLToPath(import.meta.url))
  const source = fs.readFileSync(path.join(here, '../src/views/scm/BusinessOverview.vue'), 'utf8')
  const start = source.indexOf("code: 'sales'")
  assert.ok(start > 0, '找不到销售管理模块配置')
  const block = source.slice(start, source.indexOf('note:', start))
  const edges = [...block.matchAll(/\{ from: '([A-Z_]+)', to: '([A-Z_]+)', label: '([^']+)' \}/g)]
    .map((m) => ({ from: m[1], to: m[2], label: m[3] }))
  assert.equal(edges.length, 9, '主链 5 条 + 库存 2 条 + 费用 2 条')

  const chain = ['QUOTE_ORDER', 'SO_ORDER', 'SALE_OUT', 'SALE_INV', 'SALE_INVOICE', 'RECEIPT']
  for (let i = 0; i < chain.length - 1; i += 1) {
    assert.ok(
      edges.some((e) => e.from === chain[i] && e.to === chain[i + 1]),
      `主链少了 ${chain[i]} → ${chain[i + 1]}`,
    )
  }
  // 库存联动：出库扣减、退货回增，两端都是同一个库存结存
  assert.ok(edges.some((e) => e.from === 'SALE_OUT' && e.to === 'STOCK_STATUS' && e.label === '出库'))
  assert.ok(edges.some((e) => e.from === 'SALE_RETURN' && e.to === 'STOCK_STATUS' && e.label === '退货入库'))
  // 费用闭环：费用单 → 分摊单 → 发票
  assert.ok(edges.some((e) => e.from === 'EXPENSE' && e.to === 'SALE_COST_ALLOC'))
  assert.ok(edges.some((e) => e.from === 'SALE_COST_ALLOC' && e.to === 'SALE_INVOICE'))
  // 收款收口：发票之后必须有钱回来，否则不算闭环
  assert.ok(edges.some((e) => e.to === 'RECEIPT'), '销售闭环必须包含收款单')
})

test('移动仓管：入库三件与出库三件都接在库存结存上，旧图那几条乱连的边不再出现', () => {
  const here = path.dirname(fileURLToPath(import.meta.url))
  const source = fs.readFileSync(path.join(here, '../src/views/scm/BusinessOverview.vue'), 'utf8')
  const start = source.indexOf("code: 'pda'")
  assert.ok(start > 0, '找不到移动仓管模块配置')
  const block = source.slice(start, source.indexOf('note:', start))
  const edges = [...block.matchAll(/\{ from: '([A-Z_]+)', to: '([A-Z_]+)', label: '([^']+)' \}/g)]
    .map((m) => ({ from: m[1], to: m[2], label: m[3] }))
  assert.equal(edges.length, 8, '3 条入库 + 3 条出库 + 仓间移动 + 盘点')
  const hub = 'STOCK_STATUS'
  for (const edge of edges) {
    assert.ok(edge.from === hub || edge.to === hub, `每条边都要连到库存结存：${edge.from}→${edge.to}`)
  }
  const intoHub = edges.filter((e) => e.to === hub).map((e) => e.from)
  assert.deepEqual(intoHub.sort(), ['OTHER_IN', 'PURCHASE_IN', 'FINISH_IN', 'TRANSFER'].sort())
  const outOfHub = edges.filter((e) => e.from === hub).map((e) => e.to)
  assert.deepEqual(outOfHub.sort(), ['SALE_OUT', 'MATERIAL_OUT', 'OTHER_OUT', 'STOCK_CHECK'].sort())
})

test('质检镜像的节点清单与源文件一致', () => {
  const here = path.dirname(fileURLToPath(import.meta.url))
  const source = fs.readFileSync(path.join(here, '../src/views/scm/BusinessOverview.vue'), 'utf8')
  const start = source.indexOf("code: 'qc'")
  assert.ok(start > 0, '找不到质量管理模块配置')
  const block = source.slice(start, source.indexOf('docs:', start))
  const codes = [...block.matchAll(/\{ code: '([A-Z_]+)', label: '/g)].map((m) => m[1])
  assert.deepEqual(codes, QC.nodes.map((n) => n.code))
})
