import assert from 'node:assert/strict'
import test from 'node:test'

import {
  hasUnqualifiedTab,
  isUnqualifiedRow,
  syncUnqualifiedRow,
} from '../src/core/inspection-unqualified.js'

test('有不合格数量时自动创建处理行并带出存货/原因/数量', () => {
  const detail = {
    items: [{
      存货编码: 'CL005', 存货名称: '包装木箱', 规格型号: '1200×800', 仓库: '原料仓',
      不合格数量: 2, 检验结果判定: '全批报废', 不合格原因: '尺寸不良',
    }],
  }

  assert.equal(syncUnqualifiedRow(detail, 1), true)
  assert.equal(detail.unqualified.length, 1)
  const row = detail.unqualified[0]
  assert.equal(row['关联明细行号'], 1)
  assert.equal(row['存货编码'], 'CL005')
  assert.equal(row['存货名称'], '包装木箱')
  assert.equal(row['不合格原因'], '尺寸不良')
  assert.equal(row['处理数量'], 2)
  assert.equal(row['处理方式'], '退回')
})

test('判定为不合格但数量未填时也建立关联行', () => {
  const detail = { items: [{ 存货名称: '包装木箱', 不合格数量: 0, 判定: '不合格' }] }

  assert.equal(isUnqualifiedRow(detail.items[0]), true)
  assert.equal(syncUnqualifiedRow(detail, 1), true)
  assert.equal(detail.unqualified[0]['处理数量'], 0)
})

test('上方字段变化时同步到唯一的处理行', () => {
  const detail = {
    items: [{ 存货编码: 'CL005', 存货名称: '包装木箱', 不合格数量: 3, 不合格原因: '尺寸不良' }],
    unqualified: [{ 关联明细行号: 1, 存货编码: 'CL005', 存货名称: '包装木箱', 不合格原因: '油污', 处理方式: '退回', 处理数量: 1 }],
  }

  assert.equal(syncUnqualifiedRow(detail, 1), true)
  assert.equal(detail.unqualified[0]['不合格原因'], '尺寸不良')
  assert.equal(detail.unqualified[0]['处理数量'], 3)
})

test('恢复合格后移除该行关联的处理行', () => {
  const detail = {
    items: [{ 存货名称: '包装木箱', 不合格数量: 0, 检验结果判定: '合格' }],
    unqualified: [{ 关联明细行号: 1, 处理数量: 2 }],
  }

  assert.equal(syncUnqualifiedRow(detail, 1), true)
  assert.equal(detail.unqualified.length, 0)
})

test('同一行拆成多条处置时不覆盖数量', () => {
  const detail = {
    items: [{ 存货名称: '包装木箱', 不合格数量: 3, 不合格原因: '尺寸不良' }],
    unqualified: [
      { 关联明细行号: 1, 处理方式: '退回', 处理数量: 2 },
      { 关联明细行号: 1, 处理方式: '报废', 处理数量: 1 },
    ],
  }

  assert.equal(syncUnqualifiedRow(detail, 1), false)
  assert.deepEqual(detail.unqualified.map((row) => row['处理数量']), [2, 1])
})

test('非法行号/无明细时安全返回', () => {
  assert.equal(syncUnqualifiedRow(null, 1), false)
  assert.equal(syncUnqualifiedRow({ items: [] }, 1), false)
  assert.equal(syncUnqualifiedRow({ items: [{}] }, 0), false)
})

test('只有配置了 unqualified 页签的面板才启用联动', () => {
  assert.equal(hasUnqualifiedTab([{ key: 'items' }]), false)
  assert.equal(hasUnqualifiedTab([{ key: 'items' }, { key: 'unqualified' }]), true)
  assert.equal(hasUnqualifiedTab(null), false)
})
