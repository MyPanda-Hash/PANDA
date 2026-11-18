import assert from 'node:assert/strict'
import test from 'node:test'

import { boardSummary, groupByProduct, pendingStepCount, saleOrderQty, saleProductSummary } from '../src/business/plan-groups.js'

/** 三产品加工单的真实看板形状：字段与 /api/px/planBoard 一致 */
function board() {
  return [
    { 产品编码: 'CP005', 产品名称: '轴套 C', 规格型号: 'Z-05', 工序顺序: 1, 工序编码: 'PX001', 工序名称: '车削', 计划数量: 10, 上道合格量: 10, 已排产量: 0, 可排产量: 10 },
    { 产品编码: 'CP005', 产品名称: '轴套 C', 规格型号: 'Z-05', 工序顺序: 2, 工序编码: 'PX002', 工序名称: '钻孔', 计划数量: 10, 上道合格量: 0, 已排产量: 0, 可排产量: 0 },
    { 产品编码: 'CP002', 产品名称: '法兰 B', 规格型号: 'F-02', 工序顺序: 1, 工序编码: 'PX003', 工序名称: '下料', 计划数量: 30, 上道合格量: 30, 已排产量: 0, 可排产量: 30 },
    { 产品编码: 'CP003', 产品名称: '齿圈 A', 规格型号: 'G-03', 工序顺序: 1, 工序编码: 'PX002', 工序名称: '钻孔', 计划数量: 200, 上道合格量: 200, 已排产量: 200, 可排产量: 0 },
  ]
}

test('多产品加工单按产品分区，每区只放自己的工序', () => {
  const groups = groupByProduct(board())
  assert.deepEqual(groups.map((g) => g.productCode), ['CP005', 'CP002', 'CP003'])
  assert.equal(groups[0].rows.length, 2)
  assert.deepEqual(groups[0].rows.map((r) => r['工序名称']), ['车削', '钻孔'])
  assert.equal(groups[1].rows.length, 1)
  assert.equal(groups[2].rows.length, 1)
})

test('区内工序按工序顺序排，不受接口返回次序影响', () => {
  const rows = [
    { 产品编码: 'CP001', 工序顺序: 3, 工序名称: '抛光', 可排产量: 0 },
    { 产品编码: 'CP001', 工序顺序: 1, 工序名称: '下料', 可排产量: 5 },
    { 产品编码: 'CP001', 工序顺序: 2, 工序名称: '车削', 可排产量: 0 },
  ]
  const [g] = groupByProduct(rows)
  assert.deepEqual(g.rows.map((r) => r['工序名称']), ['下料', '车削', '抛光'])
})

test('块头汇总计划、已排、可排', () => {
  const groups = groupByProduct([
    { 产品编码: 'CP001', 工序顺序: 1, 计划数量: 100, 已排产量: 40, 可排产量: 60 },
    { 产品编码: 'CP001', 工序顺序: 2, 计划数量: 100, 已排产量: 0, 可排产量: 0 },
  ])
  assert.equal(groups[0].planQty, 100)
  assert.equal(groups[0].plannedQty, 40)
  assert.equal(groups[0].availableQty, 60)
})

test('产品状态：全排满 / 部分排产 / 未排产', () => {
  const [full] = groupByProduct([{ 产品编码: 'A', 计划数量: 5, 已排产量: 5, 可排产量: 0 }])
  assert.equal(full.stateLabel, '已排满')
  assert.equal(full.tagType, 'info')
  const [part] = groupByProduct([{ 产品编码: 'A', 计划数量: 5, 已排产量: 2, 可排产量: 3 }])
  assert.equal(part.stateLabel, '部分排产')
  assert.equal(part.tagType, 'warning')
  const [none] = groupByProduct([{ 产品编码: 'A', 计划数量: 5, 已排产量: 0, 可排产量: 5 }])
  assert.equal(none.stateLabel, '未排产')
  assert.equal(none.tagType, 'success')
})

test('缺产品编码的老数据按产品名称兜底，不同产品不并成一块', () => {
  const groups = groupByProduct([
    { 产品名称: '轴套 C', 工序顺序: 1, 计划数量: 10, 可排产量: 10 },
    { 产品名称: '法兰 B', 工序顺序: 1, 计划数量: 20, 可排产量: 20 },
    { 产品名称: '轴套 C', 工序顺序: 2, 计划数量: 10, 可排产量: 0 },
  ])
  assert.equal(groups.length, 2)
  assert.equal(groups[0].rows.length, 2)
  assert.equal(groups[1].rows.length, 1)
})

test('连产品名都没有的行归到「—」，不丢行', () => {
  const groups = groupByProduct([{ 工序顺序: 1, 计划数量: 1, 可排产量: 1 }])
  assert.equal(groups.length, 1)
  assert.equal(groups[0].key, '—')
  assert.equal(groups[0].rows.length, 1)
})

test('一键排产计数只算此刻可排产量 > 0 的工序', () => {
  assert.equal(pendingStepCount(board()), 2)
  assert.equal(pendingStepCount([]), 0)
  assert.equal(pendingStepCount(null), 0)
})

test('摘要给页面和弹窗共用：产品数 / 工序数 / 待排数', () => {
  assert.equal(
    boardSummary(board()),
    '3 个产品 · 4 道工序 · 待排 2 道 · 按工序看可排产量并分批排产',
  )
  assert.equal(boardSummary([]), '0 个产品 · 0 道工序 · 待排 0 道 · 按工序看可排产量并分批排产')
})

test('空数据不炸', () => {
  assert.deepEqual(groupByProduct([]), [])
  assert.deepEqual(groupByProduct(null), [])
})

test('销售订单产品列只显示 名称 ×数量 单位，多行用顿号分开', () => {
  const items = [
    { 存货名称: '轴套 C', 存货编码: 'CP005', 数量: 10, 销售单位: '件' },
    { 存货名称: '铝板 6061', 存货编码: 'CP002', 数量: 30.5, 销售单位: '件' },
  ]
  assert.equal(saleProductSummary(items), '轴套 C ×10件、铝板 6061 ×30.5件')
})

test('销售订单没有明细时产品列显示破折号，数量为 0', () => {
  assert.equal(saleProductSummary([]), '—')
  assert.equal(saleProductSummary(null), '—')
  assert.equal(saleOrderQty(null), 0)
})

test('销售订单数量按明细行累加', () => {
  assert.equal(saleOrderQty([{ 数量: 10 }, { 数量: 30 }, { 数量: 200 }]), 240)
})
