/**
 * plan-groups.js — 工单排产看板的分组与计数
 *
 * 看板有两个入口（独立页 /mes/plan 与「待排产」列表里的排产弹窗），
 * 两处必须给出同一套分区结果，否则同一张加工单在两个入口里长得不一样。
 * 这里只放纯函数，不碰请求与 DOM，便于单测。
 */

/** 产品键：优先编码，其次名称，都没有归到「—」 */
function groupKeyOf(row) {
  return row?.['产品编码'] || row?.['产品名称'] || '—'
}

/**
 * 按产品分区，每区自带计划/已排/可排汇总与状态。
 *
 * <p>多产品加工单的工序原样平铺时，看不出谁卡在哪一步；
 * 分区后每件产品自成一条 工序1→工序n 的链。
 *
 * @param {Array<object>} rows /api/px/planBoard 返回的工序行
 * @returns {Array<{key:string, productCode:string, productName:string, spec:string,
 *   planQty:number, plannedQty:number, availableQty:number, stateLabel:string,
 *   tagType:string, rows:Array<object>}>}
 */
export function groupByProduct(rows) {
  const list = Array.isArray(rows) ? rows : []
  const groups = new Map()
  for (const row of list) {
    if (!row) continue
    const key = groupKeyOf(row)
    if (!groups.has(key)) {
      groups.set(key, {
        key,
        productCode: row['产品编码'] || '',
        productName: row['产品名称'] || '',
        spec: row['规格型号'] || '',
        planQty: 0,
        plannedQty: 0,
        availableQty: 0,
        rows: [],
      })
    }
    const g = groups.get(key)
    g.rows.push(row)
    g.plannedQty += Number(row['已排产量'] || 0)
    g.availableQty += Number(row['可排产量'] || 0)
    // 计划数量是每道工序各自的定额，块头只展示一次，取区内最大值避免重复累加
    g.planQty = Math.max(g.planQty, Number(row['计划数量'] || 0))
  }
  return [...groups.values()].map((g) => {
    g.rows.sort((a, b) => Number(a['工序顺序'] || 0) - Number(b['工序顺序'] || 0))
    if (g.availableQty <= 0) {
      g.stateLabel = '已排满'
      g.tagType = 'info'
    } else if (g.plannedQty > 0) {
      g.stateLabel = '部分排产'
      g.tagType = 'warning'
    } else {
      g.stateLabel = '未排产'
      g.tagType = 'success'
    }
    return g
  })
}

/** 此刻能排的工序数（可排产量 > 0），一键排产按钮上的数字 */
export function pendingStepCount(rows) {
  const list = Array.isArray(rows) ? rows : []
  return list.filter((r) => r && Number(r['可排产量']) > 0).length
}

/** 头部摘要：几个产品、几道工序、还有几道能排 */
export function boardSummary(rows) {
  const list = Array.isArray(rows) ? rows : []
  return `${groupByProduct(list).length} 个产品 · ${list.length} 道工序 · 待排 ${pendingStepCount(list)} 道 · 按工序看可排产量并分批排产`
}

/** 数量显示：整数不带小数，小数最多两位 */
function qtyText(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return '0'
  // 小数最多两位，多余的 0 去掉（30.50 → 30.5）
  return n === Math.round(n) ? String(n) : String(Number(n.toFixed(2)))
}

/**
 * 销售订单明细的产品文案：「名称 ×数量 单位」，多行用顿号分开。
 *
 * <p>排产列表只要看清这批做什么，不堆编码和规格；明细键是 items，
 * 字段是 存货名称/数量/销售单位（加工单那边才是 products/产品名称）。
 */
export function saleProductSummary(items) {
  const list = Array.isArray(items) ? items.filter((it) => it && (it['存货名称'] || it['存货编码'])) : []
  if (!list.length) return '—'
  return list
    .map((it) => `${it['存货名称'] || it['存货编码']} ×${qtyText(it['数量'])}${it['销售单位'] || ''}`)
    .join('、')
}

/** 销售订单数量：明细各行数量之和 */
export function saleOrderQty(items) {
  const list = Array.isArray(items) ? items : []
  return list.reduce((sum, it) => sum + Number(it?.['数量'] || 0), 0)
}
