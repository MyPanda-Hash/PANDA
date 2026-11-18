import assert from 'node:assert/strict'
import test from 'node:test'

import { permOfAction } from '../src/business/permissions.js'

test('maps purchase status actions to edit permission', () => {
  assert.equal(permOfAction('关闭'), 'edit')
  assert.equal(permOfAction('中止'), 'edit')
  assert.equal(permOfAction('取消中止'), 'edit')
})

test('maps generate purchase receipt to add permission', () => {
  assert.equal(permOfAction('生成采购入库单'), 'generate')
  assert.equal(permOfAction('生成领料申请单'), 'generate')
})
