import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  docDateOf,
  docNoOf,
  docPartnerOf,
  docStatusOf,
  railPageCount,
  railRowsOf,
  statusTone,
} from '../src/core/views/doc-rail-rows.js'

test('docNoOf: 按 编号 → 单据编号 → 单号 取值', () => {
  assert.equal(docNoOf({ 编号: 'SO-1', 单据编号: 'SO-2' }), 'SO-1')
  assert.equal(docNoOf({ 单据编号: 'SO-2' }), 'SO-2')
  assert.equal(docNoOf({ 单号: 'SO-3' }), 'SO-3')
  assert.equal(docNoOf({ 单据编号: '   ' }), '')
  assert.equal(docNoOf(null), '')
})

test('docDateOf / docPartnerOf / docStatusOf: 兼容各面板的叫法', () => {
  assert.equal(docDateOf({ 单据日期: '2026-11-03' }), '2026-11-03')
  assert.equal(docDateOf({ 日期: '2026-11-04' }), '2026-11-04')
  assert.equal(docPartnerOf({ 客户: '华东铝业' }), '华东铝业')
  assert.equal(docPartnerOf({ 供应商: '华东热处理厂' }), '华东热处理厂')
  assert.equal(docPartnerOf({ 往来单位: '西部材料' }), '西部材料')
  assert.equal(docStatusOf({ 单据状态: '已审核' }), '已审核')
  assert.equal(docStatusOf({ 状态: '启用' }), '启用')
})

test('statusTone: 已审核族=ok，已完成/已关闭=done，流转中=pending，作废/驳回=danger，其余=draft', () => {
  assert.equal(statusTone('已审核'), 'ok')
  assert.equal(statusTone('已审批'), 'ok')
  assert.equal(statusTone('已完成'), 'done')
  assert.equal(statusTone('已关闭'), 'done')
  assert.equal(statusTone('审批中'), 'pending')
  assert.equal(statusTone('生产中'), 'pending')
  assert.equal(statusTone('已作废'), 'danger')
  assert.equal(statusTone('审批驳回'), 'danger')
  assert.equal(statusTone('草稿'), 'draft')
  assert.equal(statusTone(''), 'draft')
  assert.equal(statusTone(undefined), 'draft')
})

test('railRowsOf: 空关键字原样保留全部行，并带上原始下标', () => {
  const rows = [
    { 编号: 'SO-1', 单据日期: '2026-11-01', 客户: '华东铝业', 单据状态: '已审核' },
    { 编号: 'SO-2', 单据日期: '2026-11-02', 客户: '中天精工', 单据状态: '草稿' },
  ]
  const out = railRowsOf(rows, '')
  assert.equal(out.length, 2)
  assert.deepEqual(out[0], { index: 0, no: 'SO-1', date: '2026-11-01', partner: '华东铝业', status: '已审核', tone: 'ok' })
  assert.equal(out[1].index, 1)
})

test('railRowsOf: 过滤后 index 仍指向原始行（筛完再点不会切错单）', () => {
  const rows = [
    { 编号: 'SO-1', 客户: '华东铝业', 单据状态: '已审核' },
    { 编号: 'SO-2', 客户: '中天精工', 单据状态: '草稿' },
    { 编号: 'SO-3', 客户: '华东铝业', 单据状态: '草稿' },
  ]
  const out = railRowsOf(rows, '华东')
  assert.deepEqual(out.map((r) => r.index), [0, 2])
  assert.deepEqual(out.map((r) => r.no), ['SO-1', 'SO-3'])
})

test('railRowsOf: 关键字命中单号/日期/客户/状态，忽略大小写与首尾空格', () => {
  const rows = [
    { 编号: 'so-1', 单据日期: '2026-11-01', 客户: '华东铝业', 单据状态: '已审核' },
    { 编号: 'SO-2', 单据日期: '2026-11-02', 客户: '中天精工', 单据状态: '审批中' },
  ]
  assert.equal(railRowsOf(rows, ' SO-1 ').length, 1)
  assert.equal(railRowsOf(rows, '11-02')[0].no, 'SO-2')
  assert.equal(railRowsOf(rows, '中天')[0].no, 'SO-2')
  assert.equal(railRowsOf(rows, '审批中')[0].no, 'SO-2')
  assert.equal(railRowsOf(rows, '不存在').length, 0)
})

test('railRowsOf: 入参不是数组返回空数组，空行跳过', () => {
  assert.deepEqual(railRowsOf(null, 'SO'), [])
  assert.deepEqual(railRowsOf([null, undefined], ''), [])
})

test('railRowsOf: 放 100 行也不丢行、不串下标', () => {
  const rows = Array.from({ length: 100 }, (_, i) => ({ 编号: 'SO-' + i, 单据状态: '草稿' }))
  const all = railRowsOf(rows, '')
  assert.equal(all.length, 100)
  assert.equal(all[57].index, 57)
  assert.equal(all[57].no, 'SO-57')
})

test('railPageCount: 至少 1 页，按每页条数上取整', () => {
  assert.equal(railPageCount(0, 20), 1)
  assert.equal(railPageCount(1, 20), 1)
  assert.equal(railPageCount(20, 20), 1)
  assert.equal(railPageCount(21, 20), 2)
  assert.equal(railPageCount(128, 50), 3)
  assert.equal(railPageCount(10, 0), 10)
})
