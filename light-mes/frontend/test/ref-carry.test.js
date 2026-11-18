import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  archiveRowOf,
  buildRefIndex,
  carriedValue,
  headValue,
  splitDotted,
} from '../src/core/views/ref-carry.js'

test('splitDotted: 只有「参照字段.属性」算带出列', () => {
  assert.deepEqual(splitDotted('存货名称.品牌'), { source: '存货名称', field: '品牌' })
  assert.deepEqual(splitDotted('部门.负责人'), { source: '部门', field: '负责人' })
  assert.equal(splitDotted('存货名称'), null)
  assert.equal(splitDotted('.品牌'), null)
  assert.equal(splitDotted('存货名称.'), null)
  assert.equal(splitDotted(undefined), null)
})

test('buildRefIndex: 按键建索引，空键不占位，重复键取首行', () => {
  const index = buildRefIndex([
    { 存货编码: 'CP001', 品牌: '轻铝' },
    { 存货编码: ' CP002 ', 品牌: '精工' },
    { 存货编码: '', 品牌: '无编码' },
    { 存货编码: 'CP001', 品牌: '重复行' },
  ], '存货编码')
  assert.equal(index.size, 2)
  assert.equal(index.get('CP002').品牌, '精工')
  assert.equal(index.get('CP001').品牌, '轻铝')
})

test('carriedValue: 行里有值就以行内为准（不被档案覆盖）', () => {
  const plan = { sourceFields: ['存货编码'], field: '品牌', index: buildRefIndex([{ 存货编码: 'CP001', 品牌: '轻铝' }], '存货编码') }
  assert.equal(carriedValue({ 存货编码: 'CP001', '存货名称.品牌': '手改品牌' }, '存货名称.品牌', plan), '手改品牌')
})

test('carriedValue: 行里没这个键时按档案带出（选单/导入/生单/历史数据都适用）', () => {
  const plan = { sourceFields: ['存货编码'], field: '品牌', index: buildRefIndex([{ 存货编码: 'CP001', 品牌: '轻铝' }], '存货编码') }
  assert.equal(carriedValue({ 存货编码: 'CP001', 存货名称: '铝棒 Φ80' }, '存货名称.品牌', plan), '轻铝')
})

test('carriedValue: 行里是空串也继续带出；带不出返回空串', () => {
  const plan = { sourceFields: ['存货编码'], field: '品牌', index: buildRefIndex([{ 存货编码: 'CP001', 品牌: '轻铝' }], '存货编码') }
  assert.equal(carriedValue({ 存货编码: 'CP001', '存货名称.品牌': '' }, '存货名称.品牌', plan), '轻铝')
  assert.equal(carriedValue({ 存货编码: 'CP999' }, '存货名称.品牌', plan), '')
  assert.equal(carriedValue({}, '存货名称.品牌', plan), '')
  assert.equal(carriedValue({ 存货编码: 'CP001' }, '存货名称.品牌', null), '')
})

test('archiveRowOf: 多个来源键依次尝试（编码没命中再用名称）', () => {
  const index = buildRefIndex([{ 存货编码: '', 存货名称: '铝棒 Φ80', 品牌: '轻铝' }], '存货名称')
  const plan = { sourceFields: ['存货编码', '存货名称'], field: '品牌', index }
  assert.equal(archiveRowOf({ 存货编码: 'CP404', 存货名称: '铝棒 Φ80' }, plan).品牌, '轻铝')
  assert.equal(archiveRowOf({ 存货编码: '', 存货名称: '铝棒 Φ80' }, plan).品牌, '轻铝')
})

test('headValue: 表头带出列同口径（部门.负责人）', () => {
  const plans = {
    '部门.负责人': {
      sourceFields: ['部门'],
      field: '负责人',
      index: buildRefIndex([{ 部门名称: '销售一部' }].map((r) => ({ ...r, 负责人: '刘经理' })), '部门名称'),
    },
  }
  // 行内只有「部门」值，按索引键（部门名称）命中 → 带出负责人
  assert.equal(headValue({ 部门: '销售一部' }, '部门.负责人', plans), '刘经理')
  // 行内已有值优先
  assert.equal(headValue({ 部门: '销售一部', '部门.负责人': '周经理' }, '部门.负责人', plans), '周经理')
  // 命中不到档案时为空，不会显示 undefined
  assert.equal(headValue({ 部门: '不存在部门' }, '部门.负责人', plans), '')
  assert.equal(headValue({ 部门: '销售一部' }, '部门.负责人', {}), '')
})
