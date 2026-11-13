#!/usr/bin/env node
/**
 * 数采上报演示脚本（demo，不是生产工具）。
 *
 * 用途：没有真实硬件时，验证「上报 → 流水 → 在线 → 越限报警 → 监控/统计」整条链路。
 * 这会向运行中的 MES 写真实数据（dc_sample / dc_alarm），演示完请在数据监控页处理报警，
 * 或直接删除演示流水：DELETE FROM dc_sample WHERE device_id = <演示设备 id>;
 *
 * 用法：
 *   DC_KEY=<网关 api_key> node tools/dc-ingest-demo.mjs [baseUrl] [deviceCode] [pointCode]
 * 默认：http://127.0.0.1:8080、DZ-001、W01
 */
const base = (process.argv[2] || 'http://127.0.0.1:8080').replace(/\/+$/, '')
const deviceCode = process.argv[3] || 'DZ-001'
const pointCode = process.argv[4] || 'W01'
const key = process.env.DC_KEY || ''
if (!key) {
  console.error('缺少 DC_KEY（= 设备所绑网关的 api_key）')
  process.exit(1)
}

const post = async (path, body) => {
  const res = await fetch(base + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-DC-Key': key },
    body: JSON.stringify(body),
  })
  return res.json()
}

const sleep = ms => new Promise(r => setTimeout(r, ms))

// 三帧：正常 → 正常 → 越限（上限 2.0kg 的采集点会生成一条报警）
for (const value of [0.42, 0.58, 2.35]) {
  const out = await post('/api/px/dc/ingest', {
    deviceCode,
    values: [{ pointCode, value }],
  })
  console.log(`上报 ${value}kg →`, JSON.stringify(out.data?.items?.[0] ?? out))
  await sleep(1100)
}

const beat = await post('/api/px/dc/heartbeat', { deviceCode })
console.log('心跳 →', JSON.stringify(beat.data ?? beat))
console.log('提示：打开 /mes/dc/monitor 看实时值与报警，/mes/dc/stats 看在线率')
