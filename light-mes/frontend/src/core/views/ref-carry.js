/**
 * ref-carry.js —— 「参照带出列」的纯逻辑（可 node --test）
 *
 * 背景：面板里有一类列写成「参照字段.档案字段」，例如销售订单的 `存货名称.品牌`、
 * 单据表头的 `部门.负责人`。它们不是明细行自己的数据，而是「按参照字段去档案里读一个属性」。
 * 以前这类列只有经过参照选择（refMap 把值拷进行里）才有内容，凡是**不是参照挑出来的行**
 * ——选单带过来、导入、接口/生单生成、历史数据——就永远是空的（用户看到的就是
 * 「存货品牌这一列没有数据」）。
 *
 * 这里把「带出」做成**显示期解析**：行里没有这个键（或为空）时，按 source 字段的值去档案
 * 索引里查一行，取它的 field 值显示。好处是不改存量数据、不写库、也不依赖数据是怎么进来的；
 * 行里已有值（例如用户手改过品牌）时以行内值为准，不会被档案覆盖。
 */

/** '存货名称.品牌' → { source: '存货名称', field: '品牌' }；不是带出列返回 null */
export function splitDotted(prop) {
  const text = String(prop ?? '')
  const dot = text.indexOf('.')
  if (dot <= 0 || dot === text.length - 1) return null
  return { source: text.slice(0, dot), field: text.slice(dot + 1) }
}

/** 按某个键建档案索引（键去首尾空格；空键不入索引，避免把所有空编码行并成一行） */
export function buildRefIndex(rows, keyField) {
  const index = new Map()
  for (const row of Array.isArray(rows) ? rows : []) {
    const key = String(row?.[keyField] ?? '').trim()
    if (!key || index.has(key)) continue
    index.set(key, row)
  }
  return index
}

/** 行 → 档案行：按 plan.sourceFields 依次取值，第一个能在索引里命中的就是它 */
export function archiveRowOf(row, plan) {
  if (!row || !plan?.index) return null
  for (const keyField of plan.sourceFields || []) {
    const key = String(row[keyField] ?? '').trim()
    if (!key) continue
    const hit = plan.index.get(key)
    if (hit) return hit
  }
  return null
}

/** 单元格显示值：行内非空值优先，其次档案带出值，最后空串 */
export function carriedValue(row, prop, plan) {
  const own = row?.[prop]
  if (own !== undefined && own !== null && String(own).trim() !== '') return own
  const hit = archiveRowOf(row, plan)
  if (!hit) return ''
  const value = hit[plan.field]
  return value === undefined || value === null ? '' : value
}

/**
 * 表头字段的显示值：表头只有一行（cur），逻辑同上。
 * @param {object} head 表头数据（cur）
 * @param {string} prop 字段名（可能是带出列）
 * @param {object} plans { [prop]: plan }
 */
export function headValue(head, prop, plans) {
  const own = head?.[prop]
  if (own !== undefined && own !== null && String(own).trim() !== '') return own
  return carriedValue(head, prop, plans?.[prop])
}
