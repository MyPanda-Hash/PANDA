/**
 * 数采（DC = Data Collection）接口封装。
 *
 * 对应后端 /api/px/dc/**，响应统一 { code, message, data }；code!==200 抛 Error(message)。
 * 前缀 /api 由 @core/request 的 baseURL 处理。
 *
 * 硬件接入侧（前端不调用，只在配置页「数据接入说明」里展示）：
 *   POST /api/px/dc/ingest     { gatewayCode, deviceCode, values: [{ pointCode, value }] }
 *   POST /api/px/dc/heartbeat  { deviceCode }
 */
import request from '@core/request'

function unwrap(res, fallback) {
  if (res && res.code !== undefined && Number(res.code) !== 200) {
    throw new Error(res.message || fallback)
  }
  return res?.data ?? res
}

function deviceParams(deviceId) {
  return deviceId === undefined || deviceId === null || deviceId === '' ? undefined : { deviceId }
}

/** 设备配置页：统计 + 网关列表 + 设备五连检查 + 检查项定义 */
export async function getDcConfig() {
  return unwrap(await request.get('/px/dc/config'), '加载数采配置失败')
}

/** 新增/修改网关；apiKey 留空表示不改 */
export async function saveDcGateway(payload) {
  return unwrap(await request.post('/px/dc/gateway/save', payload), '保存网关失败')
}

export async function deleteDcGateway(id) {
  return unwrap(await request.post('/px/dc/gateway/delete', { id }), '删除网关失败')
}

/** 新增/修改设备；编辑必须带 id */
export async function saveDcDevice(payload) {
  return unwrap(await request.post('/px/dc/device/save', payload), '保存设备失败')
}

export async function deleteDcDevice(id) {
  return unwrap(await request.post('/px/dc/device/delete', { id }), '删除设备失败')
}

/** 标记「数据调试」已完成 */
export async function debugDcDevice(id) {
  return unwrap(await request.post('/px/dc/device/debug', { id }), '标记调试失败')
}

export async function getDcPoints(deviceId) {
  return unwrap(await request.get('/px/dc/points', { params: deviceParams(deviceId) }), '加载采集点失败')
}

export async function saveDcPoint(payload) {
  return unwrap(await request.post('/px/dc/point/save', payload), '保存采集点失败')
}

export async function deleteDcPoint(id) {
  return unwrap(await request.post('/px/dc/point/delete', { id }), '删除采集点失败')
}

export async function getDcRules(deviceId) {
  return unwrap(await request.get('/px/dc/rules', { params: deviceParams(deviceId) }), '加载数采规则失败')
}

export async function saveDcRule(payload) {
  return unwrap(await request.post('/px/dc/rule/save', payload), '保存数采规则失败')
}

export async function deleteDcRule(id) {
  return unwrap(await request.post('/px/dc/rule/delete', { id }), '删除数采规则失败')
}

/** 实时监控：设备 + 采集点 + 最新流水 + 报警 */
export async function getDcMonitor(deviceId) {
  return unwrap(await request.get('/px/dc/monitor', { params: deviceParams(deviceId) }), '加载监控数据失败')
}

/** 处理报警 */
export async function handleDcAlarm(id, note) {
  return unwrap(await request.post('/px/dc/alarm/handle', { id, note }), '处理报警失败')
}

/** 状态统计：from/to 为 YYYY-MM-DD */
export async function getDcStats(from, to) {
  return unwrap(await request.get('/px/dc/stats', { params: { from, to } }), '加载状态统计失败')
}
