/**
 * 列表/明细表格的分页与高度计算（纯函数，便于单测；不依赖 Vue 与 Element Plus）。
 *
 * 背景：明细区表格原来固定按 5 行高度计算，表单里 10 行数据只露出 5 行、剩下被迫在表内滚动。
 * 现在表格高度跟着「每页行数」走，超出一页的行翻页查看，合计行仍按整块全部行计算。
 */

/** 每页行数可选项：界面下拉直接用它渲染 */
export const ROWS_PER_PAGE_OPTIONS = [10, 20, 50]

/** 默认每页行数：一屏能完整看清，且不把页面撑到需要滚动 */
export const DEFAULT_PAGE_SIZE = 10

/** 表头高度（el-table 表头在 size=small 下实测 32px） */
export const HEAD_H = 32

/** 单行高度（el-table size=small、单行文本时实测 33px；31 是压缩态，会少 2px 触发内滚动条） */
export const ROW_H = 33

/** 合计行高度（show-summary 的表尾） */
export const FOOT_H = 32

/** 把任意输入收敛成合法的每页行数 */
export function normalizePageSize(size, options = ROWS_PER_PAGE_OPTIONS) {
  const n = Number(size)
  return options.includes(n) ? n : DEFAULT_PAGE_SIZE
}

/** 表格像素高度：表头 + 整页行 + 可选合计行 */
export function gridHeight(size, hasFooter = false) {
  const rows = Math.max(0, Number(size) || 0)
  return HEAD_H + rows * ROW_H + (hasFooter ? FOOT_H : 0)
}

/** 总页数：无数据也算 1 页，避免页码显示 第 1/0 页 */
export function pageCount(total, size) {
  const rows = Math.max(0, Number(total) || 0)
  const per = Math.max(1, Number(size) || 1)
  return Math.max(1, Math.ceil(rows / per))
}

/** 页码收敛到 [1, pageCount] */
export function clampPage(page, total, size) {
  const last = pageCount(total, size)
  const n = Math.floor(Number(page) || 1)
  if (n < 1) return 1
  return n > last ? last : n
}

/** 当前页的行切片：行数不足一页时原样返回（不分页） */
export function pageSlice(rows, page, size) {
  const list = Array.isArray(rows) ? rows : []
  const per = Math.max(1, Number(size) || 1)
  if (list.length <= per) return list
  const current = clampPage(page, list.length, per)
  const start = (current - 1) * per
  return list.slice(start, start + per)
}

/** 补占位行到整页，保证表格高度不随数据多少跳动；已存在的行保持引用（行内编辑仍写回明细） */
export function padRows(rows, size) {
  const list = Array.isArray(rows) ? rows.slice() : []
  const per = Math.max(0, Number(size) || 0)
  while (list.length < per) list.push({ _placeholder: true })
  return list
}
