import request from '@core/request'
import { unwrap, errMsg } from '@core/panel-engine'

// 通用层函数继续对外导出（保持既有调用方兼容）
export { unwrap, errMsg }

// 数据访问固定为 SQL 后端：/api/px/* -> Spring Boot -> MySQL。

// ==================== 单单据面板（metadata.singleDoc）：参照展平 ====================
// 列表/表单查询返回 1 张单据行（form_no=面板名，明细在 detail.<tabKey>）；
// 参照弹窗需把明细行展平后在前端应用 filter/keyword（单据行顶层无明细字段，后端过滤不到明细）
const SINGLE_DOC_CODES = new Set(['EMP', 'DEPT', 'INV', 'INV_PRICE', 'EQUIP', 'WC', 'OP', 'WH', 'REGION', 'PROJ', 'UOM', 'REJECT', 'ROUTE', 'BOM'])

// ==================== 参照字段：弹窗拉取面板数据（开发约束十一-1） ====================
// 字段约定：{ dataType: '参照', refPanel, refField, displayField, filter, refMap, refMulti, refColumns }
// 交互：点击参照字段 → 弹窗展示 refPanel 面板数据列表 → 勾选行 → 确定导入（值写 refField，refMap 带出其他字段）
// 兼容两种字段形态：原始配置字段（refPanel/refField/...）与 meta 字段的 ref（panel/field/display/...）

function normRef(r) {
  if (!r) return {}
  const src = r.ref && typeof r.ref === 'object' ? r.ref : r
  return {
    dataType: '参照',
    refPanel: src.panel || src.refPanel,
    refField: src.field || src.refField,
    displayField: src.display || src.displayField,
    filter: src.filter,
    refMap: src.map || src.refMap,
    refMulti: src.multi || src.refMulti,
    refColumns: src.columns || src.refColumns,
  }
}

// 引用面板名称（弹窗标题）：异步取 SQL 后端面板配置
export async function refPanelName(field) {
  const r = normRef(field)
  try {
    const cfg = await getPanelConfig(r.refPanel)
    return (cfg && cfg.metadata && cfg.metadata.panelName) || r.refPanel
  } catch (e) {
    return r.refPanel
  }
}

// 弹窗表格列：优先字段 refColumns，其次引用面板网格列，最后 refField/displayField
export async function refColumns(field) {
  const r = normRef(field)
  if (r.refColumns && r.refColumns.length) return r.refColumns
  let cols = null
  try {
    const cfg = await getPanelConfig(r.refPanel)
    cols = cfg?.metadata?.panelPageDto?.tablePages?.[0]?.gridTabs?.[0]?.columns
  } catch (e) {
    /* SQL 后端无该面板时使用兜底列 */
  }
  if (cols && cols.length) return cols
  return [...new Set([r.refField, r.displayField].filter(Boolean))]
}

// 拉取引用面板数据（SQL 后端）
export async function queryRefRows(field, { keyword = '', pageSize = 200 } = {}) {
  const r = normRef(field)
  const filter = r.filter || {}
  const hasAlternativeFilter = Object.values(filter).some(Array.isArray)
  // 单单据面板：condition 不带 filter——单据行顶层无明细字段，后端过滤不到明细；
  // filter/keyword 在展平后的明细行上应用
  const cond = SINGLE_DOC_CODES.has(r.refPanel) || hasAlternativeFilter ? {} : filter
  // 2026-08-25：参照面板有「审核」流程（单据类面板）时，仅已审核来源单据可选（对齐 T+：已审核才能选择生单）
  if (!cond['单据状态']) {
    try {
      const cfg = await getPanelConfig(r.refPanel)
      const hasAudit = (cfg?.metadata?.buttonGroups || []).some((g) =>
        (g.actions || []).some((a) => a === '审核'))
      if (hasAudit) cond['单据状态'] = '已审核'
    } catch (e) {
      /* 配置不可得时不强制过滤 */
    }
  }
  // 单单据面板：keyword 也不传后端（单据行无明细字段，后端匹配不到），前端展平后过滤
  const res = await queryFormDataList({ panelCode: r.refPanel, condition: cond, keyword: SINGLE_DOC_CODES.has(r.refPanel) ? '' : keyword, pageNo: 1, pageSize })
  let list = res.list || []
  if (SINGLE_DOC_CODES.has(r.refPanel) && list.some((row) => row?.detail)) {
    const tabKey = (await getPanelConfig(r.refPanel))?.detail?.tabs?.[0]?.key || 'items'
    list = list.flatMap((doc) => (doc?.detail?.[tabKey] || []).map((row) => (
      r.refPanel === 'INV' ? { 所属类别: doc['类别'] || '', ...row } : row
    )))
    if (keyword) {
      const k = String(keyword).toLowerCase()
      list = list.filter((row) => Object.values(row).some((v) => String(v ?? '').toLowerCase().includes(k)))
    }
  }
  if (Object.keys(filter).length) {
    list = list.filter((row) => Object.entries(filter).every(([key, expected]) => {
      const candidates = Array.isArray(expected) ? expected : [expected]
      return candidates.some((value) => String(row[key]) === String(value))
    }))
  }
  return list
}

// SQL 后端返回原始值；这里返回 null 让调用方直接显示该值
export function refLabelOf(field, value) {
  if (value === undefined || value === null || value === '') return ''
  return null
}

// 参照字段选项由 SQL 后端 meta 提供，前端不再本地解析
export function resolveRefOptions(field) {
  return null
}

// 字段选项统一解析：普通下拉返回原 options
export function fieldOptions(field) {
  return field.options || []
}

// ==================== SQL 后端接口 ====================

export async function getPanelConfig(panelCode) {
  return unwrap(await request.get('/px/getPanelConfig', { params: { panelCode } }))
}

export async function getPermMatrix(panelCode) {
  return unwrap(await request.get('/px/getPermMatrix', { params: { panelCode } }))
}

export async function getNewFormPermMatrix({ panelCode, operationName }) {
  return unwrap(await request.get('/px/getNewFormPermMatrix', { params: { panelCode, operationName } }))
}

export async function getFormDescriptor({ panelCode, code }) {
  return unwrap(await request.get('/px/getFormDescriptor', { params: { panelCode, code } }))
}

export async function queryFormDataList(params) {
  return unwrap(await request.post('/px/queryFormDataList', params))
}

export async function callButton({ panelCode, buttonName, formData, buttonParam }) {
  // 按钮名对齐 SQL 后端（中止执行/整单中止→中止、草稿→取消中止、保存类→提交）
  const apiName = buttonName === '中止执行' || buttonName === '整单中止' ? '中止' : buttonName === '草稿' ? '取消中止' : buttonName === '保存' || buttonName === '保存为草稿' || buttonName === '保存新增' ? '提交' : buttonName
  return unwrap(await request.post('/px/callButton', { panelCode, buttonName: apiName, formData, buttonParam }))
}

export async function deleteForms({ panelCode, rowCodes }) {
  return unwrap(await request.post('/px/deleteForms', { panelCode, rowCodes }))
}

// ==================== 专属视图数据（生产看板 / 返修工作台） ====================
// SQL 数据接口尚未实现，页面显示未接入提示
export function getProdBoard() {
  return null
}

export function getReworkTasks() {
  return []
}

export function reworkAction(row, action) {
  return false
}

// 工具栏快捷键提示（对齐真实 T+ 按钮）
export const SHORTCUTS = {
  保存: 'Alt+S',
  保存新增: 'Alt+\\',
  保存打印: 'Alt+G',
  直接打印: 'Alt+P',
  打印: 'Alt+;',
  预览: 'Alt+/',
  打印模板设置: 'Alt+,',
  导出: 'Alt+X',
  放弃: 'Alt+Z',
}
