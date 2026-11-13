/**
 * 单据明细的通用金额计算（与面板配置里的 `detail.tabs[].calc` 配套）。
 *
 * 放在 core 层是为了能单独跑单测：engine.js 依赖 @core 别名，node --test 起不来。
 *
 * 委外加工费用单（OUTSOURCE_FEE）是首个用它的面板：
 * 明细「费用金额」= 数量 × 委外单价，表头「费用合计」= 各行费用金额之和。
 */

/** 四舍五入到指定小数位，先按 1eN 放大再取整，避开 308.32499999 这类二进制误差。 */
function round(value, digits) {
  const factor = 10 ** digits
  return Math.round((Number(value) + Number.EPSILON) * factor) / factor
}

function num(value) {
  const n = Number(value)
  return Number.isFinite(n) ? n : 0
}

/**
 * 按「数量 × 委外单价」重算每行的「费用金额」，返回新数组（不改原对象）。
 *
 * - 数量或单价为 0/空 → 金额 0，不产生 NaN；
 * - 保留两位小数；
 * - 只回写「费用金额」，其它列（费用项目/产品名称/备注）原样保留。
 */
export function feeRowTotals(rows, options = {}) {
  const qtyKey = options.qtyKey || '数量'
  const priceKey = options.priceKey || '委外单价'
  const amountKey = options.amountKey || '费用金额'
  const digits = options.digits ?? 2
  return (Array.isArray(rows) ? rows : []).map((row) => {
    const qty = num(row?.[qtyKey])
    const price = num(row?.[priceKey])
    return { ...row, [amountKey]: round(qty * price, digits) }
  })
}

/** 表头「费用合计」= 各行费用金额之和；空明细返回 0。 */
export function feeHeaderTotal(rows, options = {}) {
  const amountKey = options.amountKey || '费用金额'
  const digits = options.digits ?? 2
  const sum = (Array.isArray(rows) ? rows : []).reduce((s, row) => s + num(row?.[amountKey]), 0)
  return round(sum, digits)
}

/**
 * 表头自动汇总：按 `detail.tabs[].headerCalc` 声明的规则，把明细列汇总回表头字段。
 *
 * 配置形态（写在明细页签上，因为来源列属于该页签）：
 * ```json
 * "headerCalc": [{ "target": "费用合计", "source": "费用金额", "op": "sum", "round": 2 }]
 * ```
 * `op` 省略按求和；目前只支持 sum（页签内多列相加没有业务意义）。
 * 规则里 target 必须是表头已有字段，否则跳过（避免把值写到不存在的键上）。
 */
export function applyHeaderCalc(form, rules, rows) {
  if (!form || !Array.isArray(rules) || !rules.length) return form
  const list = Array.isArray(rows) ? rows : []
  for (const rule of rules) {
    const target = rule?.target
    if (!target) continue
    const source = rule?.source || target
    const digits = rule?.round ?? 2
    const sum = list.reduce((s, row) => s + num(row?.[source]), 0)
    form[target] = round(sum, digits)
  }
  return form
}
