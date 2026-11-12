// 工艺路线的工序流程：弹窗里给产品选中路线后，把这条路线要走的工序按顺序列出来。
// 工序顺序以「加工顺序」为准；历史数据没填加工顺序的，按原有行序排在后面（稳定排序）。

/**
 * @param {Array} processes 路线明细行（工艺路线单据的 detail.processes）
 * @returns {Array<{seq:number, code:string, name:string, workshop:string}>}
 */
export function routeFlowChips(processes) {
  if (!Array.isArray(processes) || !processes.length) return []
  const indexed = processes.map((row, index) => ({ row: row || {}, index }))
  indexed.sort((a, b) => {
    const sa = Number(a.row['加工顺序']) || 0
    const sb = Number(b.row['加工顺序']) || 0
    const aHas = sa > 0
    const bHas = sb > 0
    if (aHas && bHas && sa !== sb) return sa - sb
    if (aHas !== bHas) return aHas ? -1 : 1
    return a.index - b.index
  })
  return indexed.map(({ row }, i) => ({
    seq: i + 1,
    code: String(row['工序编码'] ?? '').trim(),
    name: String(row['工序名称'] ?? '').trim(),
    workshop: String(row['生产车间'] ?? '').trim(),
  }))
}

/** 下拉选项上的摘要：`3 道工序 · 下料→车削→检验` */
export function routeFlowSummary(processes) {
  const chips = routeFlowChips(processes)
  if (!chips.length) return ''
  return `${chips.length} 道工序 · ${chips.map((c) => c.name).filter(Boolean).join('→')}`
}
