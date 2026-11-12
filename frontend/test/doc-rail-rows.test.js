import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  docDateOf,
  docNoOf,
  docPartnerOf,
  docStatusOf,
  railCellOf,
  railColumnsOf,
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
  assert.deepEqual(out[0], {
    index: 0,
    no: 'SO-1',
    date: '2026-11-01',
    partner: '华东铝业',
    status: '已审核',
    tone: 'ok',
    cells: ['SO-1', '2026-11-01', '华东铝业', '已审核'],
  })
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

test('railColumnsOf: 请购单显示请购人，采购单显示供应商，销售单显示客户', () => {
  assert.deepEqual(railColumnsOf('PU_REQ', []).map((c) => c.label), ['单号', '日期', '请购人', '审核状态'])
  assert.deepEqual(railColumnsOf('PU_ORDER', []).map((c) => c.label), ['单号', '日期', '供应商', '审核状态'])
  assert.deepEqual(railColumnsOf('ARRIVAL_IN', []).map((c) => c.label), ['单号', '日期', '供应商', '审核状态'])
  assert.deepEqual(railColumnsOf('SO_ORDER', []).map((c) => c.label), ['单号', '日期', '客户', '审核状态'])
  assert.deepEqual(railColumnsOf('DISPATCH', []).map((c) => c.label), ['单号', '日期', '加工单号', '审核状态'])
})

test('railColumnsOf: 未登记面板按行里的往来单位自动取一列，全空时只留三列', () => {
  assert.deepEqual(
    railColumnsOf('UNKNOWN', [{ 客户: '华东铝业' }]).map((c) => c.label),
    ['单号', '日期', '客户', '审核状态'],
  )
  assert.deepEqual(
    railColumnsOf('UNKNOWN', [{ 建议供应商: '' }, { 建议供应商: '中天精工' }]).map((c) => c.label),
    ['单号', '日期', '建议供应商', '审核状态'],
  )
  assert.deepEqual(
    railColumnsOf('UNKNOWN', [{ 委外供应商: '苏州精工' }]).map((c) => c.label),
    ['单号', '日期', '委外供应商', '审核状态'],
  )
  assert.deepEqual(railColumnsOf('UNKNOWN', [{}]).map((c) => c.label), ['单号', '日期', '审核状态'])
})

test('railCellOf: 按列的 keys 取值，空值返回空串；derive 列优先', () => {
  const row = { 编号: 'CG-1', 单据日期: '2026-11-03', 请购人: '李丽', 单据状态: '草稿' }
  const cols = railColumnsOf('PU_REQ', [row])
  assert.deepEqual(cols.map((c) => railCellOf(row, c)), ['CG-1', '2026-11-03', '李丽', '草稿'])
  assert.equal(railCellOf({}, { label: 'x', keys: ['x'] }), '')
  assert.equal(railCellOf({}, { label: 'x', derive: () => '派生' }), '派生')
})

test('railRowsOf: 列式单元格与过滤使用同一份列定义（中间列也能搜到）', () => {
  const rows = [
    { 编号: 'CG-2026-11-0301', 单据日期: '2026-11-03', 请购人: '李丽', 单据状态: '已审核' },
    { 编号: 'CG-2026-11-0401', 单据日期: '2026-11-04', 请购人: '王芳', 单据状态: '草稿' },
  ]
  const cols = railColumnsOf('PU_REQ', rows)
  const hit = railRowsOf(rows, '李丽', cols)
  assert.equal(hit.length, 1)
  assert.deepEqual(hit[0].cells, ['CG-2026-11-0301', '2026-11-03', '李丽', '已审核'])
  assert.equal(hit[0].index, 0)
  assert.equal(railRowsOf(rows, '王芳', cols)[0].index, 1)
  assert.equal(railRowsOf(rows, '已审核', cols)[0].index, 0)
})


// ---------- 侧栏补到 5 列 / 单文档面板列明细 ----------

test('railColumnsOf: 单据侧栏补第二业务列，凑到 5 列', () => {
  const rows = [{ 单据编号: 'SO-1', 单据日期: '2026-11-03', 客户: '华东铝业', 业务员: '张伟' }]
  const cols = railColumnsOf('SO_ORDER', rows)
  assert.deepEqual(cols.map((c) => c.label), ['单号', '日期', '客户', '业务员', '审核状态'])
})

test('railColumnsOf: 第二业务列全空时不加空列', () => {
  const rows = [{ 单据编号: 'X-1', 单据日期: '2026-11-03', 客户: '华东铝业' }]
  const cols = railColumnsOf('SO_ORDER', rows)
  assert.deepEqual(cols.map((c) => c.label), ['单号', '日期', '客户', '审核状态'])
})

test('railColumnsOf: 第二业务列不与第一中间列重复', () => {
  // PU_ORDER 的第一中间列是供应商；兜底候选里的「经手人」为空时不应再出现同一列
  const rows = [{ 单据编号: 'PO-1', 单据日期: '2026-11-03', 供应商: '北方机械' }]
  const cols = railColumnsOf('PU_ORDER', rows)
  const labels = cols.map((c) => c.label)
  assert.equal(new Set(labels).size, labels.length, '不应出现重复列名')
})

test('工序汇报单侧栏列出产品名称与工序（工序取明细第一行）', () => {
  const rows = [{
    单据日期: '2026-11-11',
    单据编号: 'GX-20261111-003',
    产品名称: '轴套 C',
    客户: '华东热处理厂',
    单据状态: '已审核',
    detail: { items: [{ 工序名称: '抛光', 工序编码: 'PX010' }] },
  }]
  const cols = railColumnsOf('PROCESS_REPORT', rows)
  assert.deepEqual(cols.map((c) => c.label), ['单号', '日期', '产品名称', '工序', '审核状态'])
  assert.equal(railCellOf(rows[0], cols[2]), '轴套 C')
  assert.equal(railCellOf(rows[0], cols[3]), '抛光')
})

test('首件报检单侧栏列出加工单号与工序（一工序一单）', () => {
  const rows = [{
    单据日期: '2026-11-11',
    单据编号: 'SJ-20261111-002',
    加工单号: 'SO-20261110-001-1',
    单据状态: '草稿',
    detail: { items: [{ 工序名称: '车削', 工序编码: 'PX002' }] },
  }]
  const cols = railColumnsOf('FIRST_INSPECT', rows)
  assert.deepEqual(cols.map((c) => c.label), ['单号', '日期', '加工单号', '工序', '审核状态'])
  assert.equal(railCellOf(rows[0], cols[2]), 'SO-20261110-001-1')
  assert.equal(railCellOf(rows[0], cols[3]), '车削')
})
