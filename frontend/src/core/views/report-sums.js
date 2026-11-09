/**
 * 报表/明细表格的合计行计算。
 *
 * 合计只对数值字段求和；报表可以在 gridTabs 里声明 `summaryColumns`
 * （如库存台账只合计金额），声明后其余数值列（数量、单价）不再相加 ——
 * 不同计量单位的数量加在一起没有业务意义。
 */

/** 该报表格子声明的合计列；未声明返回 null（按原逻辑合计所有数值列）。 */
export function summaryColumnsOf(gridTab) {
  const cols = gridTab?.summaryColumns
  return Array.isArray(cols) && cols.length ? cols : null
}

/**
 * @param columns   列定义（[{property}]）
 * @param rows      数据行（占位行不参与）
 * @param fieldDefOf 列名 → 字段元数据（取 dataType 判断是否数值）
 * @param onlyColumns 只合计这些列；null/undefined 表示所有数值列都合计
 */
export function computeSums(columns, rows, fieldDefOf, onlyColumns) {
  const sums = []
  const real = (rows || []).filter((r) => !r._placeholder)
  columns.forEach((col, i) => {
    if (i === 0) {
      sums[i] = '合计'
      return
    }
    // 只对「小数/整数」类型的字段求和：纯数字文本（身份证号/手机号/编码）不参与合计
    const f = fieldDefOf(col.property)
    const isNumeric = f && (f.dataType === '小数' || f.dataType === '整数')
    const allowed = !onlyColumns || onlyColumns.includes(col.property)
    const vals = real.map((r) => Number(r[col.property]))
    sums[i] =
      isNumeric && allowed && vals.length && vals.every((v) => Number.isFinite(v))
        ? Math.round(vals.reduce((a, b) => a + b, 0) * 100) / 100
        : ''
  })
  return sums
}