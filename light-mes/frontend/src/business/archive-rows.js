/**
 * archive-rows.js — 基础档案取「一行一个档案」
 *
 * 档案面板有两种存法：
 * - 普通档案（班组、往来单位）：一行一张单据，`data` 就是档案字段；
 * - 单单据档案（设备 metadata.singleDoc）：全厂一张单据，档案行在 `detail[页签]` 里。
 *
 * 排产、报工这类需要「可选班组/设备列表」的地方只关心档案行，
 * 结构判断集中在这里，调用方不用各自去猜 detail 的页签键。
 */

/** 明细页签键：取第一个页签；配置缺省时退回常见键 */
function detailKeyOf(config) {
  const tab = config?.detail?.tabs?.[0]
  return tab?.key || config?.detailTabs?.[0]?.key || ''
}

/**
 * @param {Array<object>} docs queryFormDataList 返回的单据行（单单据档案才带 detail）
 * @param {object|null} config 面板配置，用来判断是否单单据档案及明细页签
 * @returns {Array<object>} 档案行；单单据档案展平明细，普通档案原样返回
 */
export function flattenArchiveRows(docs, config) {
  const list = Array.isArray(docs) ? docs.filter(Boolean) : []
  if (!config) return list
  const singleDoc = config?.metadata?.singleDoc === true
  const key = detailKeyOf(config)
  if (!singleDoc) return list
  if (!key) return list
  return list.flatMap((doc) => (doc?.detail?.[key] || []))
}
