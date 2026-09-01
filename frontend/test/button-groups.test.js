import assert from 'node:assert/strict'
import test from 'node:test'

import { ensureScanFillAction } from '../src/core/button-groups.js'

test('adds scan fill to an editable sales document with an old toolbar config', () => {
  const source = [{
    name: '更多',
    actions: ['放弃', '草稿', '导入', '下载导入模板', '整单中止', '附件', '刷新', '消息'],
  }]

  const result = ensureScanFillAction(source, { panelCategory: '单据' })

  assert.deepEqual(result[0].actions, [
    '放弃', '草稿', '导入', '下载导入模板', '整单中止', '附件', '刷新', '消息', '扫描填单',
  ])
  assert.deepEqual(source[0].actions, [
    '放弃', '草稿', '导入', '下载导入模板', '整单中止', '附件', '刷新', '消息',
  ])
})

test('deduplicates scan fill and creates the more group when needed', () => {
  const existing = [{ name: '更多', actions: ['刷新', '扫描填单', '扫描填单'] }]
  const deduplicated = ensureScanFillAction(existing, { panelCategory: '单据' })
  const created = ensureScanFillAction([], { panelCategory: '单据' })

  assert.deepEqual(deduplicated[0].actions, ['刷新', '扫描填单'])
  assert.deepEqual(created, [{ name: '更多', actions: ['刷新', '扫描填单'] }])
})

test('supports editable opening-balance documents', () => {
  const result = ensureScanFillAction([], { panelCategory: '期初单据' })

  assert.deepEqual(result, [{ name: '更多', actions: ['刷新', '扫描填单'] }])
})

test('does not expose scan fill on readonly or non-document panels', () => {
  const groups = [{ name: '更多', actions: ['刷新', '扫描填单'] }]

  assert.deepEqual(
    ensureScanFillAction(groups, { panelCategory: '单据', readonly: true }),
    [{ name: '更多', actions: ['刷新'] }],
  )
  assert.deepEqual(
    ensureScanFillAction(groups, { panelCategory: '报表' }),
    [{ name: '更多', actions: ['刷新'] }],
  )
})

test('keeps a descriptor-provided scan action when metadata is unavailable', () => {
  const groups = [{ name: '更多', actions: ['刷新', '扫描填单', '扫描填单'] }]

  assert.deepEqual(
    ensureScanFillAction(groups, null),
    [{ name: '更多', actions: ['刷新', '扫描填单'] }],
  )
})
