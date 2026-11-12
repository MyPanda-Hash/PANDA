// 选单弹窗的明细行处理：按工序分组 + 逐行勾选。
// 领料申请单要按「工序」分批领料，所以选单时先按 材料行的领料工序 归组，
// 组头可以整组勾选，组内可以单行勾选（已领满的行不允许再勾）。

/** 行上的领料工序：加工单材料明细用「领料工序」，工序明细用「工序名称」。 */
function processOf(line) {
  return String(line?.['领料工序'] ?? line?.['工序名称'] ?? '').trim()
}

/** 计划量：材料行是「计划数量」，部分来源行只有「数量」。 */
export function lineQty(line) {
  const v = line?.['计划数量'] ?? line?.['数量'] ?? 0
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}

/** 倒冲料：报工时自动扣账，不能手工领（「不倒冲」里也含"倒冲"两个字，要排掉）。 */
export function isBackflush(line) {
  const mode = String(line?.['材料倒冲方式'] ?? '')
  return mode.includes('倒冲') && !mode.includes('不倒冲')
}

/** 可领量 = 计划数量 − 累计领用数量（不小于 0）。 */
export function lineAvailable(line) {
  const used = Number(line?.['累计领用数量'] ?? 0)
  const left = lineQty(line) - (Number.isFinite(used) ? used : 0)
  return Math.max(0, Math.round(left * 100) / 100)
}

/**
 * 按工序分组。
 * @returns {Array<{key:string,label:string,count:number,total:number,lines:Array<{line:object,idx:number,available:number}>}>}
 */
export function groupByProcess(lines) {
  const arr = Array.isArray(lines) ? lines : []
  const order = []
  const map = new Map()
  arr.forEach((line, idx) => {
    const key = processOf(line) || '未指定工序'
    if (!map.has(key)) {
      map.set(key, { key, label: key, count: 0, total: 0, lines: [] })
      order.push(key)
    }
    const group = map.get(key)
    const backflush = isBackflush(line)
    group.lines.push({ line, idx, backflush, available: backflush ? 0 : lineAvailable(line) })
    group.count += 1
  })
  for (const key of order) {
    const group = map.get(key)
    group.total = Math.round(group.lines.reduce((sum, l) => sum + lineQty(l.line), 0) * 100) / 100
  }
  return order.map((key) => map.get(key))
}

/** 勾选结果 → 原始行号（默认全选）。 */
export function pickedIndexes(state, count) {
  if (!Array.isArray(state)) return Array.from({ length: count || 0 }, (_, i) => i)
  const out = []
  state.forEach((on, idx) => {
    if (on !== false) out.push(idx)
  })
  return out
}

/** 把 [fromIdx] 这一组设为 on/off，返回新的勾选数组（不修改入参）。 */
export function toggled(state, indexes, on) {
  const next = Array.isArray(state) ? [...state] : []
  for (const idx of indexes || []) next[idx] = !!on
  return next
}
