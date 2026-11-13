/**
 * 数采（DC）页面的纯展示计算。
 *
 * 抽出来的原因：三个页面都要把「秒数 → 时长」「在线率 → 百分比」「采集点状态 → 视觉基调」
 * 渲染成同一套文案，放在组件里会出现三份略有差异的格式；这里统一一份，单测直接覆盖边界。
 */

/** 时长：后台给秒数，界面要 1h 20m / 45m / 30s；空值给破折号，不显示 0 */
export function formatSeconds(sec) {
  if (sec === null || sec === undefined || sec === '') return '—'
  const n = Number(sec)
  if (!Number.isFinite(n) || n < 0) return '—'
  if (n < 60) return `${Math.floor(n)}s`
  if (n < 3600) return `${Math.floor(n / 60)}m`
  const hours = Math.floor(n / 3600)
  const minutes = Math.floor((n % 3600) / 60)
  return minutes ? `${hours}h ${minutes}m` : `${hours}h`
}

/** 在线率：空值给破折号（未接入 ≠ 0%），其余保留一位小数 */
export function rateText(rate) {
  if (rate === null || rate === undefined || rate === '') return '—'
  const n = Number(rate)
  if (!Number.isFinite(n)) return '—'
  return `${n.toFixed(1)}%`
}

/** 采集点状态 → 视觉基调：normal=ok / alarm=alarm / stale=muted */
export function pointTone(status) {
  if (status === 'normal') return 'ok'
  if (status === 'alarm') return 'alarm'
  return 'muted'
}

/** 设备「五连」已完成项数（0-5），用于完成度与缺失提示 */
export function checkDone(device) {
  const checks = device && device.checks
  if (!checks || typeof checks !== 'object') return 0
  return Object.values(checks).filter(Boolean).length
}

/** 趋势柱的最大值：按 在线+离线 取，全 0 时按 1 兜底，避免除零 */
export function trendMax(trend) {
  if (!Array.isArray(trend) || !trend.length) return 1
  const max = trend.reduce((m, item) => {
    const online = Number(item?.online ?? 0) || 0
    const offline = Number(item?.offline ?? 0) || 0
    return Math.max(m, online + offline)
  }, 0)
  return Math.max(1, max)
}
