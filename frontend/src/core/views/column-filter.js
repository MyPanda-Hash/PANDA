/**
 * column-filter.js —— 明细/主表「列头筛选」的纯逻辑（可 node --test）
 *
 * 交互对齐 YINJIA-MES 的列头筛选：点列头出一个小输入框，边打边过滤；命中时列头显示一个
 * 「筛选词 ×」标签，点标签即清除。筛选只作用于**当前视图的行**（内存过滤，不改后端条件、
 * 不写库），所以分页合计仍然按整块全部行算，与「查询」区的服务端条件互不干扰。
 *
 * 匹配语法（只认一种前缀，避免用户学到一套正则）：
 *   普通文本       包含（不区分大小写）
 *   =值            精确等于
 *   !=值           不等于
 *   >数值 / >=数值  数值比较（千分位逗号忽略）
 *   <数值 / <=数值  数值比较
 *   空（纯空白）    不过滤
 * 数值比较时两侧都取不到数字 → 该行不命中（而不是把非数字行当 0 混进来）。
 */

const OPS = ['>=', '<=', '!=', '=', '>', '<']

/** 解析筛选词：返回 { op, value }；op='contains' 表示包含匹配，null 表示不过滤 */
export function parseColumnFilter(raw) {
  const text = String(raw ?? '').trim()
  if (!text) return null
  for (const op of OPS) {
    if (text.startsWith(op)) {
      const value = text.slice(op.length).trim()
      if (!value) return null
      return { op: op === '!=' ? 'ne' : op, value }
    }
  }
  return { op: 'contains', value: text }
}

/** 去掉千分位后的数值；取不到有限数返回 null */
export function toNumber(value) {
  if (value === null || value === undefined) return null
  const text = String(value).replace(/,/g, '').trim()
  if (!text) return null
  const number = Number(text)
  return Number.isFinite(number) ? number : null
}

/** 单元格值是否命中筛选词（raw 为列头输入框原文） */
export function matchColumnFilter(cellValue, raw) {
  const parsed = parseColumnFilter(raw)
  if (!parsed) return true
  const cellText = cellValue === null || cellValue === undefined ? '' : String(cellValue)
  switch (parsed.op) {
    case 'contains':
      return cellText.toLowerCase().includes(parsed.value.toLowerCase())
    case '=':
      return cellText.trim() === parsed.value
    case 'ne':
      return cellText.trim() !== parsed.value
    default: {
      const left = toNumber(cellText)
      const right = toNumber(parsed.value)
      if (left === null || right === null) return false
      if (parsed.op === '>') return left > right
      if (parsed.op === '>=') return left >= right
      if (parsed.op === '<') return left < right
      return left <= right
    }
  }
}

/**
 * 对行数组应用全部列筛选（AND 组合）。
 * @param {Array<object>} rows 原始行
 * @param {object} filters { [prop]: 筛选词原文 }
 * @param {Array<string>} props 参与筛选的列（顺序无关）；传空/不传则用 filters 的键
 * @returns {Array<object>} 过滤后的新数组（不改入参）
 */
export function applyColumnFilters(rows, filters, props) {
  const list = Array.isArray(rows) ? rows : []
  const entries = (Array.isArray(props) && props.length ? props : Object.keys(filters || {}))
    .filter((prop) => parseColumnFilter(filters?.[prop]))
    .map((prop) => ({ prop, raw: filters[prop] }))
  if (!entries.length) return list
  return list.filter((row) => entries.every(({ prop, raw }) => matchColumnFilter(row?.[prop], raw)))
}

/** 生效中的筛选词数量（列头显示「筛选 N」与清除入口用） */
export function activeFilterCount(filters) {
  return Object.values(filters || {}).filter((raw) => parseColumnFilter(raw)).length
}
