/**
 * inspection-qty.js — 检验类明细的数量约束
 *
 * 合格 / 不合格数量必须落在 [0, 报检数量] 内，且两者之和不能超过报检数量。
 * 成品报检单、检验单、过程检验单都是这个口径，所以抽成一处，表单联动时统一夹住，
 * 避免出现「报检 10、合格 11、不合格 -1」这种输入。
 */

function num(value) {
  const n = Number(value)
  return Number.isFinite(n) ? n : 0
}

function round2(value) {
  return Math.round(value * 100) / 100
}

/** 上限：报检数量优先，检验单退到「检验数量」 */
function baseOf(row) {
  if (row['报检数量'] !== undefined && row['报检数量'] !== null && row['报检数量'] !== '') {
    return num(row['报检数量'])
  }
  if (row['检验数量'] !== undefined && row['检验数量'] !== null && row['检验数量'] !== '') {
    return num(row['检验数量'])
  }
  return 0
}

/**
 * 把一行检验明细的数量夹到合法范围，原地修改。
 *
 * @param {object} row 明细行
 * @returns {boolean} 是否发生了调整
 */
export function clampInspectionQty(row) {
  if (!row) return false
  const base = baseOf(row)
  if (!(base > 0)) return false
  const ok = Math.min(Math.max(num(row['合格数量']), 0), base)
  let ng = Math.min(Math.max(num(row['不合格数量']), 0), base)
  if (round2(ok + ng) > base) ng = Math.max(0, round2(base - ok))
  const changed = num(row['合格数量']) !== ok || num(row['不合格数量']) !== ng
  if (changed) {
    row['合格数量'] = ok
    row['不合格数量'] = ng
  }
  return changed
}
