import assert from 'node:assert/strict'
import test from 'node:test'

import { flattenArchiveRows } from '../src/business/archive-rows.js'

/** 设备档案：metadata.singleDoc=true，设备在 detail.equips 明细里 */
const EQUIP_CONFIG = {
  metadata: { singleDoc: true },
  detail: { tabs: [{ key: 'equips', label: '设备明细' }] },
}

const EQUIP_DOC = {
  编号: 'EQUIP-DOC',
  备注: '设备',
  detail: {
    equips: [
      { 编号: 'EQUIP-001', 设备编码: 'SB001', 设备名称: '锯床-01', 停用: false },
      { 编号: 'EQUIP-002', 设备编码: 'SB002', 设备名称: '锯床-02', 停用: false },
    ],
  },
}

test('单单据档案展平成明细行，一行一台设备', () => {
  const rows = flattenArchiveRows([EQUIP_DOC], EQUIP_CONFIG)
  assert.equal(rows.length, 2)
  assert.deepEqual(rows.map((r) => r['设备名称']), ['锯床-01', '锯床-02'])
  assert.equal(rows[0]['设备编码'], 'SB001')
})

test('普通档案（一行一张单）原样返回，班组不会被当成明细', () => {
  const rows = {
    编号: 'TEAM-001',
    班组编码: 'BZ001',
    班组名称: '下料班',
  }
  assert.deepEqual(flattenArchiveRows([rows], { metadata: {}, detail: {} }), [rows])
})

test('单据里没有该明细页签时返回空数组，不把单据头当档案行', () => {
  assert.deepEqual(flattenArchiveRows([{ 编号: 'EQUIP-DOC', 备注: '空' }], EQUIP_CONFIG), [])
})

test('多张单单据档案合并明细', () => {
  const second = { detail: { equips: [{ 编号: 'EQUIP-003', 设备名称: '锯床-03' }] } }
  const rows = flattenArchiveRows([EQUIP_DOC, second], EQUIP_CONFIG)
  assert.equal(rows.length, 3)
})

test('拿不到面板配置时按普通列表处理', () => {
  const doc = { 编号: 'X', 名称: 'Y' }
  assert.deepEqual(flattenArchiveRows([doc], null), [doc])
})

test('空输入不炸', () => {
  assert.deepEqual(flattenArchiveRows(null, EQUIP_CONFIG), [])
  assert.deepEqual(flattenArchiveRows([], EQUIP_CONFIG), [])
})
