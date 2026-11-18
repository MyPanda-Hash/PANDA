import assert from 'node:assert/strict'
import test from 'node:test'

import { groupByProcess, isBackflush, pickedIndexes, toggled } from '../src/core/select-lines.js'

const lines = [
  { 材料编码: 'CL001', 材料名称: '45#圆钢', 计划数量: 4.53, 领料工序: '下料' },
  { 材料编码: 'CL004', 材料名称: '切削液', 计划数量: 0.1, 领料工序: '车削' },
  { 材料编码: 'CL005', 材料名称: '包装木箱', 计划数量: 0.1, 领料工序: '下料' },
]

test('按领料工序分组，组内保留原始行号', () => {
  const groups = groupByProcess(lines)
  assert.deepEqual(groups.map((g) => g.label), ['下料', '车削'])
  assert.deepEqual(groups[0].lines.map((l) => l.idx), [0, 2])
  assert.equal(groups[0].count, 2)
})

test('组小计按计划数量求和（两位小数）', () => {
  const groups = groupByProcess(lines)
  assert.equal(groups[0].total, 4.63)
  assert.equal(groups[1].total, 0.1)
})

test('没有工序的行归到「未指定工序」，不能静默丢掉', () => {
  const groups = groupByProcess([{ 材料编码: 'X', 计划数量: 1 }])
  assert.equal(groups.length, 1)
  assert.equal(groups[0].label, '未指定工序')
})

test('勾选结果按原始顺序返回行号，取消勾选的行被排除', () => {
  assert.deepEqual(pickedIndexes([true, false, true]), [0, 2])
  assert.deepEqual(pickedIndexes(undefined, 2), [0, 1], '没有勾选状态时默认全选')
})

test('整组勾选/取消只影响本组', () => {
  const state = [true, true, true]
  const off = toggled(state, [0, 2], false)
  assert.deepEqual(off, [false, true, false])
  const on = toggled(off, [0, 2], true)
  assert.deepEqual(on, [true, true, true])
})

test('已领满的行不可再勾（可领量 = 计划 − 累计领用）', () => {
  const groups = groupByProcess([
    { 材料编码: 'A', 计划数量: 2, 累计领用数量: 2, 领料工序: '下料' },
    { 材料编码: 'B', 计划数量: 2, 累计领用数量: 0.5, 领料工序: '下料' },
  ])
  assert.equal(groups[0].lines[0].available, 0)
  assert.equal(groups[0].lines[1].available, 1.5)
})

test('倒冲料不可手工领：可领量按 0 算并打标记', () => {
  const groups = groupByProcess([
    { 材料编码: 'CL004', 计划数量: 0.1, 领料工序: '车削', 材料倒冲方式: '按定额倒冲' },
    { 材料编码: 'CL001', 计划数量: 4, 领料工序: '下料', 材料倒冲方式: '不倒冲' },
  ])
  const flush = groups.find((g) => g.label === '车削').lines[0]
  assert.equal(flush.backflush, true)
  assert.equal(flush.available, 0)
  const normal = groups.find((g) => g.label === '下料').lines[0]
  assert.equal(normal.backflush, false, '「不倒冲」不能被误判成倒冲')
  assert.equal(normal.available, 4)
})

test('isBackflush 排掉「不倒冲」', () => {
  assert.equal(isBackflush({ 材料倒冲方式: '按定额倒冲' }), true)
  assert.equal(isBackflush({ 材料倒冲方式: '不倒冲' }), false)
  assert.equal(isBackflush({}), false)
})
