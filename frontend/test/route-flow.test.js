import assert from 'node:assert/strict'
import test from 'node:test'

import { routeFlowChips, routeFlowSummary } from '../src/core/route-flow.js'

test('工序按加工顺序排列，缺失顺序的按原次序跟在后面', () => {
  const rows = [
    { 加工顺序: 3, 工序编码: 'PX003', 工序名称: '检验' },
    { 加工顺序: 1, 工序编码: 'PX001', 工序名称: '下料' },
    { 工序名称: '包装' },
    { 加工顺序: 2, 工序编码: 'PX002', 工序名称: '车削' },
  ]
  assert.deepEqual(routeFlowChips(rows).map((c) => c.name), ['下料', '车削', '检验', '包装'])
})

test('工序序号按排列位置重编，不跟着原始加工顺序跳号', () => {
  const chips = routeFlowChips([
    { 加工顺序: 10, 工序名称: '下料' },
    { 加工顺序: 20, 工序名称: '车削' },
  ])
  assert.deepEqual(chips.map((c) => c.seq), [1, 2])
})

test('车间缺省时不显示空括号', () => {
  const chips = routeFlowChips([{ 工序名称: '下料', 生产车间: '' }])
  assert.equal(chips[0].workshop, '')
})

test('摘要给下拉用：3 道工序 · 下料→车削→检验', () => {
  const rows = [
    { 加工顺序: 1, 工序名称: '下料' },
    { 加工顺序: 2, 工序名称: '车削' },
    { 加工顺序: 3, 工序名称: '检验' },
  ]
  assert.equal(routeFlowSummary(rows), '3 道工序 · 下料→车削→检验')
})

test('空路线不炸，摘要为空串', () => {
  assert.deepEqual(routeFlowChips([]), [])
  assert.deepEqual(routeFlowChips(null), [])
  assert.equal(routeFlowSummary([]), '')
})
