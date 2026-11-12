import assert from 'node:assert/strict'
import test from 'node:test'

import { clampInspectionQty } from '../src/core/inspection-qty.js'

test('合格数量超过报检数量时压回报检数量', () => {
  const row = { 报检数量: 10, 合格数量: 11, 不合格数量: 0 }
  assert.equal(clampInspectionQty(row), true)
  assert.equal(row['合格数量'], 10)
})

test('不合格数量为负时归零', () => {
  const row = { 报检数量: 10, 合格数量: 10, 不合格数量: -1 }
  clampInspectionQty(row)
  assert.equal(row['不合格数量'], 0)
})

test('合格+不合格超过报检数量时压不合格数量', () => {
  const row = { 报检数量: 10, 合格数量: 8, 不合格数量: 5 }
  clampInspectionQty(row)
  assert.equal(row['合格数量'], 8)
  assert.equal(row['不合格数量'], 2)
})

test('合法数量不动', () => {
  const row = { 报检数量: 10, 合格数量: 7, 不合格数量: 3 }
  assert.equal(clampInspectionQty(row), false)
  assert.equal(row['合格数量'], 7)
  assert.equal(row['不合格数量'], 3)
})

test('检验单用检验数量作为上限', () => {
  const row = { 检验数量: 20, 合格数量: 25, 不合格数量: 0 }
  clampInspectionQty(row)
  assert.equal(row['合格数量'], 20)
})

test('没有报检/检验数量的行不处理', () => {
  const row = { 合格数量: 5, 不合格数量: -3 }
  assert.equal(clampInspectionQty(row), false)
  assert.equal(row['不合格数量'], -3)
})
