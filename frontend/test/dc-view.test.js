import test from 'node:test'
import assert from 'node:assert/strict'

import { checkDone, formatSeconds, pointTone, rateText, trendMax } from '../src/business/dc-view.js'

test('formatSeconds：空值给破折号，秒/分/时逐级进位', () => {
  assert.equal(formatSeconds(null), '—')
  assert.equal(formatSeconds(undefined), '—')
  assert.equal(formatSeconds(''), '—')
  assert.equal(formatSeconds('abc'), '—')
  assert.equal(formatSeconds(-5), '—')

  assert.equal(formatSeconds(0), '0s')
  assert.equal(formatSeconds(30), '30s')
  assert.equal(formatSeconds(59), '59s')
  assert.equal(formatSeconds(60), '1m')
  assert.equal(formatSeconds(2700), '45m')
  assert.equal(formatSeconds(3600), '1h')
  assert.equal(formatSeconds(4800), '1h 20m')
})

test('formatSeconds：超过一天仍按小时累计，不丢分钟', () => {
  assert.equal(formatSeconds(25 * 3600), '25h')
  assert.equal(formatSeconds(25 * 3600 + 61), '25h 1m')
  assert.equal(formatSeconds(86400 * 2), '48h')
})

test('rateText：null 给破折号，其余一位小数带百分号', () => {
  assert.equal(rateText(null), '—')
  assert.equal(rateText(undefined), '—')
  assert.equal(rateText(''), '—')
  assert.equal(rateText(0), '0.0%')
  assert.equal(rateText(66.66), '66.7%')
  assert.equal(rateText(100), '100.0%')
})

test('pointTone：采集点状态映射到三种视觉基调', () => {
  assert.equal(pointTone('normal'), 'ok')
  assert.equal(pointTone('alarm'), 'alarm')
  assert.equal(pointTone('stale'), 'muted')
  assert.equal(pointTone(undefined), 'muted')
  assert.equal(pointTone('weird'), 'muted')
})

test('checkDone：五连完成数只数 true，缺 checks 记 0', () => {
  assert.equal(checkDone(null), 0)
  assert.equal(checkDone({}), 0)
  assert.equal(checkDone({ checks: {} }), 0)
  assert.equal(checkDone({ checks: { gateway: true, project: true } }), 2)
  assert.equal(
    checkDone({ checks: { gateway: true, project: true, scada: true, rule: true, debug: true } }),
    5,
  )
  assert.equal(checkDone({ checks: { gateway: 1, project: 'yes', scada: false } }), 2)
})

test('trendMax：取每日在线+离线最大值，空数据按 1 兜底', () => {
  assert.equal(trendMax(null), 1)
  assert.equal(trendMax([]), 1)
  assert.equal(trendMax([{ online: 0, offline: 0 }]), 1)
  assert.equal(trendMax([{ online: 2, offline: 3 }, { online: 1, offline: 0 }]), 5)
  assert.equal(trendMax([{ online: null, offline: 2 }]), 2)
})
