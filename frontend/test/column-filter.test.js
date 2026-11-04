import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  activeFilterCount,
  applyColumnFilters,
  matchColumnFilter,
  parseColumnFilter,
  toNumber,
} from '../src/core/views/column-filter.js'

/* ── 解析：普通文本=包含，带前缀才走比较 ── */
test('parseColumnFilter: 空白不过滤', () => {
  assert.equal(parseColumnFilter(''), null)
  assert.equal(parseColumnFilter('   '), null)
  assert.equal(parseColumnFilter(null), null)
})

test('parseColumnFilter: 文本/等于/不等于/数值比较', () => {
  assert.deepEqual(parseColumnFilter('铝棒'), { op: 'contains', value: '铝棒' })
  assert.deepEqual(parseColumnFilter(' = CP001 '), { op: '=', value: 'CP001' })
  assert.deepEqual(parseColumnFilter('!=已审核'), { op: 'ne', value: '已审核' })
  assert.deepEqual(parseColumnFilter('>=10'), { op: '>=', value: '10' })
  assert.deepEqual(parseColumnFilter('<0'), { op: '<', value: '0' })
  // 只有前缀没有值 → 视为不过滤，避免「打了 1 个字符就把表清空」
  assert.equal(parseColumnFilter('>'), null)
  assert.deepEqual(parseColumnFilter('> 5'), { op: '>', value: '5' })
})

test('toNumber: 忽略千分位，取不到数字返回 null', () => {
  assert.equal(toNumber('1,234.5'), 1234.5)
  assert.equal(toNumber(''), null)
  assert.equal(toNumber('铝棒'), null)
  assert.equal(toNumber(null), null)
})

/* ── 单格匹配 ── */
test('matchColumnFilter: 包含不区分大小写', () => {
  assert.equal(matchColumnFilter('铝棒 Φ80', 'Φ80'), true)
  assert.equal(matchColumnFilter('CP001', 'cp0'), true)
  assert.equal(matchColumnFilter('CP001', 'CP2'), false)
})

test('matchColumnFilter: 精确等于/不等于', () => {
  assert.equal(matchColumnFilter('已审核', '=已审核'), true)
  assert.equal(matchColumnFilter('已审核中', '=已审核'), false)
  assert.equal(matchColumnFilter('草稿', '!=已审核'), true)
})

test('matchColumnFilter: 数值比较按数字而非字符串（9 < 10）', () => {
  assert.equal(matchColumnFilter(9, '>=10'), false)
  assert.equal(matchColumnFilter(10, '>=10'), true)
  assert.equal(matchColumnFilter('1,000', '>900'), true)
  // 非数字格不参与数值比较：宁可漏一行，也不要把空值当 0 混进来
  assert.equal(matchColumnFilter('', '>=0'), false)
  assert.equal(matchColumnFilter('铝棒', '>=0'), false)
})

/* ── 整块行筛选 ── */
test('applyColumnFilters: 多列 AND、不改入参', () => {
  const rows = [
    { 存货编码: 'CP001', 存货名称: '铝棒 Φ80', 数量: 10 },
    { 存货编码: 'CP002', 存货名称: '铝板 6061', 数量: 100 },
    { 存货编码: 'CP003', 存货名称: '铝型材-散热片', 数量: 300 },
  ]
  const out = applyColumnFilters(rows, { 存货名称: '铝', 数量: '>=100' })
  assert.deepEqual(out.map((r) => r.存货编码), ['CP002', 'CP003'])
  // 原数组未被替换/裁掉
  assert.equal(rows.length, 3)
})

test('applyColumnFilters: 无有效筛选时原样返回（同一引用，省一次数组分配）', () => {
  const rows = [{ 数量: 1 }]
  assert.equal(applyColumnFilters(rows, { 数量: '  ' }), rows)
  assert.equal(applyColumnFilters(rows, {}), rows)
  assert.equal(applyColumnFilters(null, { 数量: '1' }).length, 0)
})

test('applyColumnFilters: 传了 props 时只筛这些列（忽略其它键）', () => {
  const rows = [{ 存货名称: '铝棒', 备注: '加急' }]
  assert.equal(applyColumnFilters(rows, { 备注: '加急' }, ['存货名称']).length, 1)
  assert.equal(applyColumnFilters(rows, { 存货名称: '铝板' }, ['存货名称']).length, 0)
})

test('activeFilterCount: 只有生效的筛选词计数', () => {
  assert.equal(activeFilterCount({ 存货名称: '铝', 数量: '', 备注: '> ' }), 1)
  assert.equal(activeFilterCount({}), 0)
})
