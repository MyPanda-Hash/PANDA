/**
 * 检验单「不合格处理」页签联动（PanelxList / PanelxForm 共用，纯函数，不依赖业务层）。
 *
 * 规则：
 * - 上方检验行满足「不合格」条件（不合格数量 > 0，或 判定/检验结果判定 不是「合格」）时，
 *   在 unqualified 页签维护一条关联该行的处理行：带入 存货编码/名称/规格/仓库/单位/不合格原因，
 *   处理数量默认等于不合格数量（单行时随上方数量同步）。
 * - 上方恢复合格时，移除该行关联的处理行，避免残留处置数量参与保存校验。
 * - 同一行已被拆成多条处置时不再覆盖数量，交给用户自行拆分。
 */

export const UNQUALIFIED_KEY = 'unqualified'

const COPY_FIELDS = ['存货编码', '存货名称', '规格型号', '仓库', '计量单位', '采购单位']
const REASON_FIELDS = ['不合格原因', '缺陷原因', '原因']

function text(value) {
  return value == null ? '' : String(value).trim()
}

/** 面板是否配置了不合格处理页签 */
export function hasUnqualifiedTab(tabs) {
  return Array.isArray(tabs) && tabs.some((tab) => tab && tab.key === UNQUALIFIED_KEY)
}

/** 上方检验行是否属于不合格（数量优先，其次看判定字段） */
export function isUnqualifiedRow(row) {
  if (!row) return false
  if ((Number(row['不合格数量']) || 0) > 0) return true
  const verdict = text(row['检验结果判定']) || text(row['判定'])
  return verdict !== '' && verdict !== '合格'
}

/**
 * 按明细行号同步不合格处理行。
 * @param {object} detail 单据明细（含主明细与 unqualified）
 * @param {number} lineNo 关联明细行号（从 1 开始）
 * @param {string} itemsKey 主明细页签 key（默认 items）
 * @returns {boolean} 是否发生了变更
 */
export function syncUnqualifiedRow(detail, lineNo, itemsKey = 'items') {
  const items = detail ? detail[itemsKey] : null
  if (!detail || !Array.isArray(items) || !Number.isInteger(lineNo) || lineNo <= 0) return false
  const row = items[lineNo - 1]
  if (!row) return false

  const unqualified = isUnqualifiedRow(row)
  let treatments = Array.isArray(detail[UNQUALIFIED_KEY]) ? detail[UNQUALIFIED_KEY] : null
  const linked = treatments
    ? treatments.filter((item) => Number(item && item['关联明细行号']) === lineNo)
    : []

  if (!unqualified) {
    if (!linked.length) return false
    for (let index = treatments.length - 1; index >= 0; index -= 1) {
      if (Number(treatments[index] && treatments[index]['关联明细行号']) === lineNo) treatments.splice(index, 1)
    }
    return true
  }

  if (!treatments) {
    treatments = []
    detail[UNQUALIFIED_KEY] = treatments
  }

  const copy = {}
  for (const key of COPY_FIELDS) {
    if (text(row[key]) !== '') copy[key] = row[key]
  }
  for (const key of REASON_FIELDS) {
    if (text(row[key]) !== '') {
      copy['不合格原因'] = row[key]
      break
    }
  }
  const quantity = Number(row['不合格数量']) || 0

  if (!linked.length) {
    treatments.push({
      关联明细行号: lineNo,
      ...copy,
      处理方式: '退回',
      处理数量: quantity,
      报废类型: '',
    })
    return true
  }
  if (linked.length > 1) return false

  Object.assign(linked[0], copy)
  linked[0]['处理数量'] = quantity
  return true
}
