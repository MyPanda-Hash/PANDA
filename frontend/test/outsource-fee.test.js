import test from 'node:test'
import assert from 'node:assert/strict'

/**
 * 委外加工费用单（OUTSOURCE_FEE）的计算口径：
 * 1) 表头「费用合计」= 明细「费用金额」之和，随明细变化自动重算；
 * 2) 明细「费用金额」= 数量 × 委外单价（配置里的 calc 规则要真的指向已有的列）。
 */
import { feeRowTotals, feeHeaderTotal } from '../src/core/views/doc-calc.js'

function row(qty, price, amount) {
  return { 数量: qty, 委外单价: price, 费用金额: amount }
}

test('费用金额按 数量 × 委外单价 计算，保留两位', () => {
  const out = feeRowTotals([row(15, 21, 0)])
  assert.equal(out[0]['费用金额'], 315)
})

test('单价带小数时金额跟着算对（不再被取整）', () => {
  const out = feeRowTotals([row(15, 20.555, 0)])
  assert.equal(out[0]['费用金额'], 308.33, '15 × 20.555 = 308.325 → 308.33')
})

test('多行时逐行算，互不影响', () => {
  const out = feeRowTotals([row(10, 6.5, 0), row(2, 3.25, 0)])
  assert.equal(out[0]['费用金额'], 65)
  assert.equal(out[1]['费用金额'], 6.5)
})

test('费用合计 = 各行费用金额之和', () => {
  assert.equal(feeHeaderTotal([row(10, 6.5, 65), row(2, 3.25, 6.5)]), 71.5)
  assert.equal(feeHeaderTotal([]), 0)
})

test('有历史费用金额但没单价时，费用合计仍按金额求和（不覆盖已填报的单据）', () => {
  assert.equal(feeHeaderTotal([{ 数量: 10, 委外单价: 0, 费用金额: 65 }]), 65)
})

test('数量为 0 或空值时金额为 0，不产生 NaN', () => {
  const out = feeRowTotals([row(0, 5, 9), row(undefined, 5, 9)])
  assert.equal(out[0]['费用金额'], 0)
  assert.equal(out[1]['费用金额'], 0)
})

// ---- 表头汇总：applyCalc 支持 headerCalc（表头字段 = 明细列的汇总） ----

import { applyHeaderCalc } from '../src/core/views/doc-calc.js'

test('表头 费用合计 跟随明细自动汇总', () => {
  const form = { 费用合计: 0 }
  applyHeaderCalc(form, [
    { target: '费用合计', source: '费用金额', op: 'sum', round: 2 },
  ], [{ 费用金额: 65 }, { 费用金额: 6.5 }])
  assert.equal(form['费用合计'], 71.5)
})

test('明细为空时表头合计归零，不保留旧值', () => {
  const form = { 费用合计: 99 }
  applyHeaderCalc(form, [{ target: '费用合计', source: '费用金额', op: 'sum' }], [])
  assert.equal(form['费用合计'], 0)
})

test('没有 headerCalc 时不动表头字段', () => {
  const form = { 费用合计: 99 }
  applyHeaderCalc(form, undefined, [{ 费用金额: 65 }])
  assert.equal(form['费用合计'], 99)
})

test('op 省略时默认求和', () => {
  const form = { 费用合计: 0 }
  applyHeaderCalc(form, [{ target: '费用合计', source: '费用金额' }], [{ 费用金额: 1 }, { 费用金额: 2 }])
  assert.equal(form['费用合计'], 3)
})
