import request from '@core/request'
import { unwrap, errMsg } from '@core/panel-engine'
import { buildRefIndex } from '@core/views/ref-carry'
import { filterButtonGroups, priceFieldLocked } from './permissions'
import { flattenArchiveRows } from './archive-rows.js'

// 通用层函数继续对外导出（保持既有调用方兼容）
export { unwrap, errMsg }
// 权限渲染层过滤（阶段 B）：经引擎通道暴露，core 渲染器不得直接 import business 层
export { filterButtonGroups, priceFieldLocked }

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

// 构造参照查询条件：单单据面板/备选过滤不带 filter（单据行顶层无明细字段，后端过滤不到，
// filter/keyword 在展平后的明细行上应用）；参照面板有「审核」流程（单据类面板）时仅已审核
// 来源单据可选（对齐 T+：已审核才能选择生单）。行数计数（refRowCount）与弹窗/下拉取数
// 共用同一口径，保证计数与可选行集一致。
function buildRefCondition(refConfig, filter) {
  const singleDoc = refConfig?.metadata?.singleDoc === true
  const hasAlternativeFilter = Object.values(filter || {}).some(Array.isArray)
  const cond = singleDoc || hasAlternativeFilter ? {} : { ...filter }
  if (!cond['单据状态']) {
    const hasAudit = (refConfig?.metadata?.buttonGroups || []).some((g) =>
      (g.actions || []).some((a) => ['审核', '提交审批'].includes(a)))
    if (hasAudit) cond['单据状态'] = '已审核'
  }
  return cond
}

// 拉取引用面板数据（SQL 后端）
export async function queryRefRows(field, { keyword = '', pageSize = 200 } = {}) {
  const r = normRef(field)
  const filter = r.filter || {}
  let refConfig = null
  try {
    refConfig = await getPanelConfig(r.refPanel)
  } catch (e) {
    /* 配置不可得时按普通多单据面板查询 */
  }
  const singleDoc = refConfig?.metadata?.singleDoc === true
  const cond = buildRefCondition(refConfig, filter)
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

/**
 * 基础档案行（班组、设备、工作中心等）：一行一个档案。
 *
 * <p>普通档案一行一张单据（`data` 就是档案字段）；设备这类是「单单据档案」，
 * 全厂一张单，档案行在 `detail[页签]` 里。这里统一判定并展平，
 * 调用方不再各自猜明细页签——先前排产的设备下拉按普通列表取，拿到的是单据头，永远是空的。
 */
export async function loadArchiveRows(panelCode, { pageSize = 500 } = {}) {
  const res = await queryFormDataList({ panelCode, pageNo: 1, pageSize })
  let config = null
  try {
    config = await getPanelConfig(panelCode)
  } catch (e) {
    /* 配置不可得时按普通档案处理 */
  }
  return flattenArchiveRows(res?.list || [], config)
}

/**
 * 参照面板可选数据行数（参照字段动态形态判定：≤20 行下拉直选，>20 行弹窗搜索定位）。
 * 与 queryRefRows 同口径：单单据面板/备选过滤的行集需前端展平后计数；
 * 其余按相同 condition（含「已审核」过滤）取 totalSize，保证计数与弹窗内行数一致。
 */
export async function refRowCount(field) {
  const r = normRef(field)
  try {
    let refConfig = null
    try {
      refConfig = await getPanelConfig(r.refPanel)
    } catch (e) {
      /* 配置不可得时按普通多单据面板计数 */
    }
    const clientFiltered = refConfig?.metadata?.singleDoc === true
      || Object.values(r.filter || {}).some(Array.isArray)
    if (clientFiltered) {
      const rows = await queryRefRows(field, {})
      return rows.length
    }
    const res = await queryFormDataList({
      panelCode: r.refPanel,
      condition: buildRefCondition(refConfig, r.filter || {}),
      pageNo: 1,
      pageSize: 1,
    })
    return res.totalSize || 0
  } catch (e) {
    return 0
  }
}

/**
 * 参照下拉远程搜索选项：返回 [{ label, value, row }]。
 * 保留原始 row，选中后调用方复用弹窗同款确认回填链路（含 refMap 带出）。
 */
export async function refSelectOptions(field, keyword = '') {
  const r = normRef(field)
  const rows = await queryRefRows(field, { keyword, pageSize: 100 })
  const displayField = r.displayField || r.refField
  return rows.map((row) => ({
    label: String(row[displayField] ?? ''),
    value: row[r.refField] ?? '',
    row,
  }))
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

/**
 * 参照面板「档案索引」：按 keyField 建 Map，供「参照带出列」（存货名称.品牌 / 部门.负责人）
 * 在显示期按行内参照值查档案属性。属于可选能力：core 渲染器用特性探测决定是否启用。
 * 缓存 60s（与单位下拉同口径）：档案刚维护完不至于一直看到旧值，也不会每行都打接口。
 */
const ARCHIVE_TTL_MS = 60000
const archiveCache = new Map()

/** 清空档案索引缓存（保存/新增档案后调用，避免带出旧值） */
export function clearArchiveCache() {
  archiveCache.clear()
}

export async function archiveIndex(panelCode, keyField) {
  if (!panelCode || !keyField) return null
  const cacheKey = `${panelCode}|${keyField}`
  const hit = archiveCache.get(cacheKey)
  if (hit && Date.now() - hit.at < ARCHIVE_TTL_MS) return hit.index
  const rows = await queryRefRows({ dataType: '参照', refPanel: panelCode }, { pageSize: 500 })
  const index = buildRefIndex(rows, keyField)
  archiveCache.set(cacheKey, { at: Date.now(), index })
  return index
}

// ==================== SQL 后端接口 ====================

export async function getPanelConfig(panelCode) {
  // 配置可能在后端启动时被 Registry 重写；加时间戳避免浏览器/代理缓存旧 panel_config。
  return normalizeApprovalConfig(unwrap(await request.get('/px/getPanelConfig', {
    params: { panelCode, _t: Date.now() },
  })))
}

/**
 * 保存登录用户的表格列定制（阶段 C：顺序/显隐/别名）。columns 传空数组 = 恢复默认。
 * 引擎可选方法：core 渲染器通过 typeof 特性探测决定是否显示「表格调整」入口。
 */
/** 字段布局：表头 + 明细字段的顺序/列宽/显隐（字段管理弹窗初始化）。 */
export async function fieldLayout(panelCode) {
  return unwrap(await request.get('/px/fieldLayout', { params: { panelCode, _t: Date.now() } }))
}

/** 保存字段布局（全公司共用，需要该面板的「调整」权限）。 */
export async function saveFieldLayout({ panelCode, rows }) {
  return unwrap(await request.post('/px/saveFieldLayout', { panelCode, rows }))
}

export async function saveColumnPrefs({ panelCode, columns }) {
  return unwrap(await request.post('/px/saveColumnPrefs', { panelCode, columns }))
}

/**
 * 面板自助加字段（可选引擎能力）：只在 px_custom_field 登记元数据，读取出口并进配置。
 * 服务端校验字段名/类型/重名与「修改」权限。
 */
export async function addCustomField(payload) {
  return unwrap(await request.post('/px/addCustomField', payload))
}

/** 停用自定义字段（只删元数据，业务数据保留） */
export async function retireCustomField(payload) {
  return unwrap(await request.post('/px/retireCustomField', payload))
}

/**
 * 业务流程追踪：按任意单号正反向找全相关单据，返回每一步状态与卡点。
 * 供业务总览的「选择单据追踪」使用。
 */
export async function traceProcess(docNo) {
  return unwrap(await request.get('/px/trace', { params: { docNo } }))
}

// ==================== 界面语言（阶段 A：tt() 显示层翻译） ====================

/** 可选语言清单（zh-CN 恒首位） */
export async function localeList() {
  return unwrap(await request.get('/locale/list'))
}

/** 词条字典（zh/空 locale 后端短路返回空 dict；失败键不返回，前端回退原文） */
export async function localeDict({ locale, keys }) {
  return unwrap(await request.post('/locale/dict', { locale, keys }))
}

export async function getPermMatrix(panelCode) {
  return unwrap(await request.get('/px/getPermMatrix', { params: { panelCode } }))
}

export async function getNewFormPermMatrix({ panelCode, operationName }) {
  return normalizeApprovalPayload(
    unwrap(await request.get('/px/getNewFormPermMatrix', {
      params: { panelCode, operationName, _t: Date.now() },
    })),
  )
}

export async function getFormDescriptor({ panelCode, code }) {
  return normalizeApprovalPayload(
    unwrap(await request.get('/px/getFormDescriptor', {
      params: { panelCode, code, _t: Date.now() },
    })),
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

/**
 * 生单预览：销售订单 → 生产加工单。
 * 返回逐行的 需求数量 / 现存量 / 已下单生产数量 / 建议生产数量 / 是否不足。
 */
export async function previewProduce({ docNo }) {
  return unwrap(await request.post('/px/previewProduce', { docNo }))
}

/**
 * 出库预览：销售订单 → 销售出库单。
 * 返回逐行的 需求 / 已出库 / 剩余待出 / 现存量 / 建议出库数量 / 是否不足。
 */
export async function previewSaleOut({ docNo, warehouse }) {
  return unwrap(await request.post('/px/previewSaleOut', { docNo, warehouse: warehouse || undefined }))
}

/**
 * 入库预览：生产加工单 → 产成品入库单。
 * 返回逐行的 计划 / 已入库 / 剩余入库 / 建议入库数量。
 */
/** 导出开票清单（CSV）。返回 Blob，由调用方触发下载。 */
/** 响应拦截器已把响应体直接解出，blob 请求拿到的是 Blob 本身，不是 axios response。 */
function asBlob(v, mime) {
  if (v instanceof Blob) return mime ? new Blob([v], { type: mime }) : v
  return new Blob([v], mime ? { type: mime } : undefined)
}

export async function invoiceExport(codes) {
  return asBlob(await request.post('/px/invoiceExport', { codes }, { responseType: 'blob' }))
}

/** 某张发票已归档的官方票面文件列表。 */
export async function invoiceFileList(code) {
  return unwrap(await request.get('/px/invoiceFile/list', { params: { code } }))
}

/** 回传官方票面（OFD / PDF）。 */
export async function invoiceFileUpload(code, file) {
  const fd = new FormData()
  fd.append('file', file)
  return unwrap(await request.post(`/px/invoiceFile/upload?code=${encodeURIComponent(code)}`, fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }))
}

/** 下载官方票面：带鉴权头取 blob，再用临时链接触发保存。 */
export async function invoiceFileDownload(id, fileName) {
  const data = await request.get('/px/invoiceFile/download', { params: { id }, responseType: 'blob' })
  const url = URL.createObjectURL(asBlob(data, mimeOf(fileName)))
  const a = document.createElement('a')
  a.href = url
  a.download = fileName || 'invoice'
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 2000)
}

/**
 * 预览官方票面。
 *
 * <p>PDF 浏览器能直接渲染，所以先同步开一个空白窗口（否则 await 之后会被弹窗拦截），
 * 取到 blob 再把窗口地址换成 blob URL。OFD 浏览器不支持渲染，调用方只给下载。
 */
export async function invoiceFilePreview(id, fileName) {
  const win = window.open('', '_blank')
  try {
    const data = await request.get('/px/invoiceFile/download', { params: { id }, responseType: 'blob' })
    const url = URL.createObjectURL(asBlob(data, mimeOf(fileName)))
    if (win) win.location.href = url
    else window.open(url, '_blank')
    setTimeout(() => URL.revokeObjectURL(url), 60000)
  } catch (e) {
    if (win) win.close()
    throw e
  }
}

function mimeOf(fileName) {
  return String(fileName || '').toLowerCase().endsWith('.ofd') ? 'application/ofd' : 'application/pdf'
}

/** 触发浏览器保存 Blob（导出清单用）。 */
export function saveBlob(blob, fileName) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = fileName || 'export.csv'
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 2000)
}

/** 按排产批次的领料需求。 */
export async function materialIssueBoard(manuOrderNo) {
  return unwrap(await request.get('/px/materialIssueBoard', { params: { manuOrderNo } }))
}

/** 某排产批次的报工记录。 */
export async function batchReports(dispatchNo) {
  return unwrap(await request.get('/px/batchReports', { params: { dispatchNo } }))
}

/** 核销弹窗：可核销的往来单与未结金额。 */
export async function openWriteOffLines(panelCode, formNo) {
  return unwrap(await request.get('/px/openWriteOffLines', { params: { panelCode, formNo } }))
}

/** 按批次领料：生成材料出库单（多行）。 */
export async function issueBatch(payload) {
  return unwrap(await request.post('/px/issueBatch', payload))
}

/** 单种材料领料（兼容旧调用）。 */
export async function issueByBatch(payload) {
  return unwrap(await request.post('/px/issueByBatch', payload))
}

/** 按排产批次报工看板。 */
export async function reportBatchBoard() {
  return unwrap(await request.get('/px/reportBatchBoard'))
}

/** 排产批次看板的「未开工」列：已下发加工单里还没排产的工序（可排产量 > 0）。 */
export async function pendingPlanBoard() {
  return unwrap(await request.get('/px/pendingPlanBoard'))
}

/** 按排产批次报工：合格 + 不合格 = 报工数量。 */
export async function reportByBatch(dispatchNo, qualified, rejected) {
  return unwrap(await request.post('/px/reportByBatch', { 排产单号: dispatchNo, 合格数量: qualified, 不合格数量: rejected }))
}

/** 首件在前、报工在后：给该批次所属工序补建首件报检单（一工序一单，已存在则返回原单号）。 */
export async function firstInspectByBatch(dispatchNo) {
  return unwrap(await request.post('/px/firstInspectByBatch', { 排产单号: dispatchNo }))
}

/** 工单排产：某加工单每道工序的可排产量与已有批次。 */
export async function planBoard(manuOrderNo) {
  return unwrap(await request.get('/px/planBoard', { params: { manuOrderNo } }))
}

/** 销售订单排产看板：一行一个订单行，带订单数量/已排/可排与预计加工单号。 */
export async function saleOrderPlanBoard(saleOrderNo) {
  return unwrap(await request.get('/px/saleOrderPlanBoard', { params: { saleOrderNo } }))
}

/** 销售订单排产：按订单行生成生产加工单（单号 = 销售订单号-行号），生成即已审核。 */
export async function planFromSalesOrder(payload) {
  return unwrap(await request.post('/px/planFromSalesOrder', payload))
}

/** 建排产批次（一张派工单 = 一个批次）。 */
export async function planBatch(payload) {
  return unwrap(await request.post('/px/planBatch', payload))
}

/** 一键排产：把当前可排的工序全部排成批次（后端直接走到已审核） */
export async function planAll(payload) {
  return unwrap(await request.post('/px/planAll', payload))
}

/** 工序报工工作台：一行一道工序，带剩余量与可否报工。 */
export async function processReportBoard(manuOrderNo) {
  return unwrap(await request.get('/px/processReportBoard', { params: manuOrderNo ? { manuOrderNo } : {} }))
}

/** 按工序报工：生成只含该工序一行的工序汇报单。 */
/** 成品报检候选：已报完全部工序、且还没生成过成品报检单的加工单。 */
export async function finishInspectCandidates() {
  return unwrap(await request.get('/px/finishInspectCandidates'))
}

/** 按加工单生成成品报检单（单号 = 加工单号-JY）。 */
export async function finishInspectFromMo(manuOrderNo) {
  return unwrap(await request.post('/px/finishInspectFromMo', { 加工单号: manuOrderNo }))
}

/** 工序汇报选单看板：加工单 + 每道工序的计划/已报/未报与可否报工。 */
export async function processReportPickBoard() {
  return unwrap(await request.get('/px/processReportPickBoard'))
}

export async function reportProcess(manuOrderNo, processCode, quantity) {
  return unwrap(await request.post('/px/reportProcess', { 加工单号: manuOrderNo, 工序编码: processCode, 报工数量: quantity }))
}

/** 开票工作台：以销货单为行，带发票号与状态。 */
export async function invoiceWorkbench() {
  return unwrap(await request.get('/px/invoiceWorkbench'))
}

/** 开票方（销售方）信息，销售发票打印用。 */
export async function companyInfo() {
  return unwrap(await request.get('/px/companyInfo'))
}

export async function previewFinishIn({ docNo }) {
  return unwrap(await request.post('/px/previewFinishIn', { docNo }))
}

export async function callButton({ panelCode, buttonName, formData, buttonParam }) {
  // 按钮名对齐 SQL 后端（中止执行/整单中止→中止、草稿→取消中止、保存类→提交）
  const apiName = buttonName === '中止执行' || buttonName === '整单中止' ? '中止' : buttonName === '草稿' ? '取消中止' : buttonName === '保存' || buttonName === '保存为草稿' || buttonName === '保存新增' ? '提交' : buttonName
  return unwrap(await request.post('/px/callButton', { panelCode, buttonName: apiName, formData, buttonParam }))
}

// ==================== 采购发票：行级选入库单/退货单行 ====================

/**
 * 采购发票可选来源行（已审核采购入库单/采购退货单的未开票行）。
 * mode=purchase|return；supplier 为空时不限供应商；keyword 匹配单号/存货（供应商用 supplier 过滤）。
 */
export async function invoiceSourceLines({ mode = 'purchase', supplier = '', keyword = '' } = {}) {
  return unwrap(await request.get('/px/invoiceSourceLines', {
    params: { mode, supplier, keyword },
  }))
}

function invoiceSourceLineKey(row) {
  const panel = String(row?.['来源面板'] ?? '').trim()
  const no = String(row?.['来源单号'] ?? '').trim()
  const line = String(row?.['来源行号'] ?? '').trim()
  if (!panel && !no && !line) return ''
  return `${panel}|${no}|${line}`
}

/**
 * 采购发票「选入库单行」：把选中的来源行转换为发票表体行。
 * - 数量取本次开票数量（退货为负）；来源数量及入库/已开票/未开票数量保持正数。
 * - 整行开票时沿用来源税额/含税金额；只开部分数量时按 本次数量 ÷ 未开票数量 折算（2 位精度）。
 * - 退货行税额、价税合计取负；采购行沿用来源符号。
 * - 已存在于明细中的来源行（来源面板+来源单号+来源行号）跳过。
 * 返回 { rows, duplicates }，由列表/表单共用。
 */
export function buildInvoiceLineRows(selectedRows, mode = 'purchase', existingRows = []) {
  const seen = new Set()
  for (const row of Array.isArray(existingRows) ? existingRows : []) {
    const key = invoiceSourceLineKey(row)
    if (key) seen.add(key)
  }

  const rows = []
  let duplicates = 0
  for (const source of Array.isArray(selectedRows) ? selectedRows : []) {
    if (!source) continue
    const key = invoiceSourceLineKey(source)
    if (key && seen.has(key)) {
      duplicates += 1
      continue
    }
    if (key) seen.add(key)

    const notInvoiced = Number(source['未开票数量'])
    const selectedQty = roundDecimal(Math.abs(Number(source.selectedQty ?? source['未开票数量'])), 4)
    const ratio = Number.isFinite(notInvoiced) && notInvoiced > 0
      ? Math.min(1, selectedQty / notInvoiced)
      : 1
    const tax = roundDecimal((Number(source['税额']) || 0) * ratio, 2)
    const total = roundDecimal((Number(source['含税金额'] ?? source['价税合计']) || 0) * ratio, 2)
    const sign = mode === 'return' ? -1 : 1

    rows.push({
      存货编码: source['存货编码'] ?? '',
      存货名称: source['存货名称'] ?? '',
      规格型号: source['规格型号'] ?? '',
      计量单位: source['计量单位'] ?? '',
      数量: selectedQty * sign,
      无税单价: Number(source['单价']) || 0,
      '税率%': Number(source['税率%']) || 0,
      税额: mode === 'return' ? -Math.abs(tax) : tax,
      金额: mode === 'return' ? -Math.abs(total - Math.abs(tax)) : roundDecimal(total - tax, 2),
      价税合计: mode === 'return' ? -Math.abs(total) : total,
      来源面板: source['来源面板'] ?? '',
      来源单号: source['来源单号'] ?? '',
      来源行号: source['来源行号'] ?? '',
      来源数量: selectedQty,
      源单类型: source['来源面板'] ?? '',
      源单编号: source['来源单号'] ?? '',
      源单行号: source['来源行号'] ?? '',
      入库数量: Number(source['入库数量']) || 0,
      已开票数量: Number(source['已开票数量']) || 0,
      未开票数量: Number(source['未开票数量']) || 0,
    })
  }
  return { rows, duplicates }
}

/** 采购发票表头金额：由明细价税合计/税额汇总（红字行已是负数，直接相加）。 */
export function invoiceHeaderTotals(rows) {
  let net = 0
  let tax = 0
  let gross = 0
  for (const row of Array.isArray(rows) ? rows : []) {
    const lineGross = Number(row?.['价税合计']) || 0
    const lineTax = Number(row?.['税额']) || 0
    gross += lineGross
    tax += lineTax
    net += lineGross - lineTax
  }
  return {
    不含税金额: roundDecimal(net, 2),
    税额: roundDecimal(tax, 2),
    价税合计: roundDecimal(gross, 2),
  }
}

export async function deleteForms({ panelCode, rowCodes }) {
  return unwrap(await request.post('/px/deleteForms', { panelCode, rowCodes }))
}

/**
 * Upload a voucher image to the MES backend. The backend owns the cloud OCR
 * credentials and returns schema-whitelisted form data for user confirmation.
 */
export async function recognizeFormImage({ panelCode, image }) {
  const body = new FormData()
  body.append('panelCode', panelCode)
  body.append('image', image, image.name || 'voucher.jpg')
  const response = await request.post('/ocr/scan-form', body, { timeout: 60000 })
  if (response?.code && response.code !== 200) {
    throw new Error(response.message || 'OCR 识别失败')
  }
  return unwrap(response)
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

/** 计划分析可选的销售订单：已审核且还有未排产数量。 */
export async function planAnalyzeCandidates() {
  return unwrap(await request.get('/px/planAnalyzeCandidates'))
}

/** 计划分析：一张销售订单 → 成品 / 物料缺口 / 工艺路线 / 风险 / 结论。 */
export async function planAnalyze(salesOrderNo) {
  return unwrap(await request.post('/px/planAnalyze', { salesOrderNo }))
}

/** 计划分析 → 生成请购单（草稿）；codes 省略表示全部可请购行。 */
export async function planAnalyzePurchaseReq(salesOrderNo, codes) {
  return unwrap(await request.post('/px/planAnalyzePurchaseReq', { salesOrderNo, codes: codes || [] }))
}
