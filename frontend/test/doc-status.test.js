import assert from 'node:assert/strict'
import test from 'node:test'

import { isDocApproved } from '../src/core/doc-status.js'

test('弃审回草稿后不再显示已审批印章（即使表头残留审批状态）', () => {
  assert.equal(isDocApproved({ 单据状态: '草稿', 审批状态: '已通过' }), false)
  assert.equal(isDocApproved({ 单据状态: '草稿', 审批状态: '已审批' }), false)
})

test('审批中不算已审批', () => {
  assert.equal(isDocApproved({ 单据状态: '审批中', 审批状态: '已通过' }), false)
})

test('已审核及生产流转仍显示印章', () => {
  for (const status of ['已审核', '生产中', '已完工', '已关闭']) {
    assert.equal(isDocApproved({ 单据状态: status }), true, status)
  }
})

test('历史数据无单据状态时回退表头审批状态', () => {
  assert.equal(isDocApproved({ 审批状态: '已通过' }), true)
  assert.equal(isDocApproved({ 审批状态: '已审批' }), true)
  assert.equal(isDocApproved({ 审批状态: '已驳回' }), false)
})

test('空行不显示印章', () => {
  assert.equal(isDocApproved(null), false)
  assert.equal(isDocApproved(undefined), false)
  assert.equal(isDocApproved({}), false)
})
