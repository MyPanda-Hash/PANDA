import request from '@core/request'
import { unwrap, errMsg } from '@core/panel-engine'

// 通用层函数继续对外导出（保持既有调用方兼容）
export { unwrap, errMsg }

/** 财务字段十进制四舍五入，修正 15.5 * 1.13 = 17.514999... 一类二进制浮点边界。 */
export function roundDecimal(value, digits = 2) {
  const number = Number(value)
  if (!Number.isFinite(number)) return 0
  const factor = 10 ** digits
  const scaled = number * factor
  const tolerance = Number.EPSILON * Math.max(1, Math.abs(scaled)) * 4
  const rounded = scaled >= 0
    ? Math.floor(scaled + 0.5 + tolerance)
    : Math.ceil(scaled - 0.5 - tolerance)
  return rounded / factor
}

// 数据访问固定为 SQL 后端：/api/px/* -> Spring Boot -> MySQL。

const APPROVAL_WORKFLOW_ACTIONS = ['提交审批', '审批通过', '审批驳回', '审批情况', '弃审']
/**
 * 兼容历史面板配置：审批面板只保留一个审批组，主按钮直接提交审批。
 * 后端也会执行同样的归一化，这里兼容数据库中的旧配置。
 */
export function normalizeApprovalGroups(rawGroups, forceWorkflow = false) {
  const groups = Array.isArray(rawGroups) ? rawGroups : []
  const hasWorkflow = forceWorkflow
    || groups.some((group) => (group.actions || group.items || []).includes('提交审批'))
  if (!hasWorkflow) {
    return groups.map((group) => ({
      ...group,
      actions: [...new Set((group.actions || group.items || []).map((action) => (
        action === '驳回审批' ? '审批驳回' : action
      )))],
    }))
  }

  const result = []
  let approvalGroup = null
  let insertAt = -1
  for (const group of groups) {
    const actions = group.actions || group.items || []
    const isWorkflowGroup = actions.includes('提交审批')
      || (['审核', '审批', '审批情况', '弃审'].includes(group.name)
        && actions.some((action) => ['审核', ...APPROVAL_WORKFLOW_ACTIONS, '驳回审批'].includes(action)))
    if (isWorkflowGroup) {
      if (insertAt < 0) insertAt = result.length
      approvalGroup ||= group
      continue
    }
    result.push({
      ...group,
      actions: [...new Set(actions.map((action) => (action === '驳回审批' ? '审批驳回' : action)))],
    })
  }
  result.splice(insertAt < 0 ? result.length : insertAt, 0, {
    ...(approvalGroup || {}),
    name: '审批',
    actions: [...APPROVAL_WORKFLOW_ACTIONS],
  })
  return result
}

function normalizeApprovalConfig(config) {
  if (!config?.metadata) return config
  return {
    ...config,
    metadata: {
      ...config.metadata,
      buttonGroups: normalizeApprovalGroups(config.metadata.buttonGroups),
    },
  }
}

function normalizeApprovalPayload(payload) {
  if (!payload) return payload
  return {
    ...payload,
    buttonGroups: normalizeApprovalGroups(payload.buttonGroups),
  }
}

// ==================== 单单据面板（metadata.singleDoc）：参照展平 ====================
// 列表/表单查询返回 1 张单据行（form_no=面板名，明细在 detail.<tabKey>）；
// 参照弹窗需把明细行展平后在前端应用 filter/keyword（单据行顶层无明细字段，后端过滤不到明细）

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
  let refConfig = null
  try {
    refConfig = await getPanelConfig(r.refPanel)
  } catch (e) {
    /* 配置不可得时按普通多单据面板查询 */
  }
  const singleDoc = refConfig?.metadata?.singleDoc === true
  // 单单据面板：condition 不带 filter——单据行顶层无明细字段，后端过滤不到明细；
  // filter/keyword 在展平后的明细行上应用
  const cond = singleDoc || hasAlternativeFilter ? {} : { ...filter }
  // 2026-08-25：参照面板有「审核」流程（单据类面板）时，仅已审核来源单据可选（对齐 T+：已审核才能选择生单）
  if (!cond['单据状态']) {
    try {
      const hasAudit = (refConfig?.metadata?.buttonGroups || []).some((g) =>
        (g.actions || []).some((a) => ['审核', '提交审批'].includes(a)))
      if (hasAudit) cond['单据状态'] = '已审核'
    } catch (e) {
      /* 配置不可得时不强制过滤 */
    }
  }
  // 单单据面板：keyword 也不传后端（单据行无明细字段，后端匹配不到），前端展平后过滤
  const res = await queryFormDataList({ panelCode: r.refPanel, condition: cond, keyword: singleDoc ? '' : keyword, pageNo: 1, pageSize })
  let list = res.list || []
  if (singleDoc && list.some((row) => row?.detail)) {
    const tabKey = refConfig?.detail?.tabs?.[0]?.key || 'items'
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
  return normalizeApprovalConfig(unwrap(await request.get('/px/getPanelConfig', { params: { panelCode } })))
}

export async function getPermMatrix(panelCode) {
  return unwrap(await request.get('/px/getPermMatrix', { params: { panelCode } }))
}

export async function getNewFormPermMatrix({ panelCode, operationName }) {
  return normalizeApprovalPayload(
    unwrap(await request.get('/px/getNewFormPermMatrix', { params: { panelCode, operationName } })),
  )
}

export async function getFormDescriptor({ panelCode, code }) {
  return normalizeApprovalPayload(
    unwrap(await request.get('/px/getFormDescriptor', { params: { panelCode, code } })),
  )
}

export async function queryFormDataList(params) {
  return unwrap(await request.post('/px/queryFormDataList', params))
}

/**
 * 按库存状况表口径回填明细现存量：有仓库取仓库库存，无仓库取全部仓库合计。
 * 选择存货后即时调用，避免引用存货档案中的静态值。
 */
export async function fillCurrentStock(rows) {
  const targets = (Array.isArray(rows) ? rows : [rows]).filter((row) => (
    row && Object.prototype.hasOwnProperty.call(row, '现存量')
      && (row['存货编码'] || row['产品编码'] || row['材料编码'] || row['存货名称'] || row['产品名称'] || row['材料名称'])
  ))
  if (!targets.length) return 0
  const result = await queryFormDataList({ panelCode: 'STOCK_STATUS', condition: {}, pageNo: 1, pageSize: 500 })
  const stockRows = result.list || []
  for (const row of targets) {
    const code = String(row['存货编码'] || row['产品编码'] || row['材料编码'] || '').trim()
    const name = String(row['存货名称'] || row['产品名称'] || row['材料名称'] || row['存货'] || '').trim()
    const warehouse = String(row['仓库'] || row['预出仓库'] || row['出库仓库'] || '').trim()
    const quantity = stockRows.reduce((sum, stock) => {
      const stockCode = String(stock['存货编码'] || '').trim()
      const stockName = String(stock['存货'] || '').trim()
      const stockWarehouse = String(stock['仓库'] || '').trim()
      const itemMatches = code ? code === stockCode : name === stockName
      if (!itemMatches || (warehouse && warehouse !== stockWarehouse)) return sum
      const value = Number(stock['现存量(主)'])
      return sum + (Number.isFinite(value) ? value : 0)
    }, 0)
    row['现存量'] = Math.round(quantity * 100) / 100
    if (Object.prototype.hasOwnProperty.call(row, '现存量说明')) {
      row['现存量说明'] = warehouse ? `库存状况表（${warehouse}）` : '库存状况表（全部仓库）'
    }
  }
  return targets.length
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
