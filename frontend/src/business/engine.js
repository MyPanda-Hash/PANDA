import request from '@core/request'
import { USE_PANELX } from '@core/env'
import { initSdk, requireAuthed } from '@core/sdk'
import {
  unwrap, errMsg, adaptPanelConfig, adaptMeta,
  adaptFormData, panelxButtonGroups, platformCall,
} from '@core/panel-engine'

// 通用层函数继续对外导出（保持既有调用方兼容）
export { unwrap, errMsg }

// ==================== 业务层：PanelX 后端代理模式 ====================
// 本地菜单/路由使用自有面板码（MANU_ORDER…），平台侧映射真实面板码。
// 平台：业务域 GroupChat_Inst_17867095995605 @ http://203.132.49.57:6612/hscx（后端 PanelxService 代理，前端不直连）
// 运行模式（2026-08-20 起仅两模式，Mock / PanelX 直连 已移除）：
//   ① 本地后端：VITE_PANELX_PROXY 空 → /api → Spring Boot(8080) → MySQL
//   ② PanelX 后端代理：VITE_PANELX_PROXY=true → /api/panelx/* → PanelxService → 平台
const PANEL_MAP = {
  MANU_ORDER: 'IML_00001_v_工作台',
  SO_ORDER: 'IML_00002_v_组织架构',
}

export function resolvePanelCode(panelCode) {
  if (!USE_PANELX) return panelCode
  return PANEL_MAP[panelCode] || panelCode
}

// 面板配置缓存（平台 getPanelConfig 相对重，列表页/表单页共用）
const _platformCfgCache = {}

async function platformConfig(panelCode) {
  const code = resolvePanelCode(panelCode)
  if (_platformCfgCache[code]) return _platformCfgCache[code]
  const sdk = await requireAuthed()
  // 注意：SDK 的 getPanelConfig 接收字符串面板码（传对象会被序列化进 query，后端 400）；
  // 返回 {state,msg,data:{metadata,dataSchema}} 包装，需取 .data
  const cfg = (await platformCall((sd) => sd.api.getPanelConfig(code)))?.data
  const adapted = adaptPanelConfig(cfg, panelCode)
  _platformCfgCache[code] = adapted
  return adapted
}

// 确保平台模式已登录；非平台模式（本地后端）直接通过
export async function ensurePanelx() {
  if (!USE_PANELX) return null
  initSdk()
  return requireAuthed()
}

// ==================== 单单据面板（metadata.singleDoc）：参照展平 ====================
// 列表/表单查询返回 1 张单据行（form_no=面板名，明细在 detail.<tabKey>）；
// 参照弹窗需把明细行展平后在前端应用 filter/keyword（单据行顶层无明细字段，后端过滤不到明细）
const SINGLE_DOC_CODES = new Set(['EMP', 'DEPT', 'INV_PRICE', 'EQUIP', 'WC', 'OP', 'WH', 'REGION', 'PROJ', 'UOM', 'REJECT', 'ROUTE', 'BOM'])

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

// 引用面板名称（弹窗标题）：异步取面板配置（本地后端 / PanelX 代理）
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
    /* 平台模式/后端无该面板时走兜底列 */
  }
  if (cols && cols.length) return cols
  return [...new Set([r.refField, r.displayField].filter(Boolean))]
}

// 拉取引用面板数据（本地后端 / PanelX 代理）
export async function queryRefRows(field, { keyword = '', pageSize = 200 } = {}) {
  const r = normRef(field)
  // 单单据面板：condition 不带 filter——单据行顶层无明细字段，后端过滤不到明细；
  // filter/keyword 在展平后的明细行上应用
  const cond = SINGLE_DOC_CODES.has(r.refPanel) ? {} : (r.filter || {})
  // 单单据面板：keyword 也不传后端（单据行无明细字段，后端匹配不到），前端展平后过滤
  const res = await queryFormDataList({ panelCode: r.refPanel, condition: cond, keyword: SINGLE_DOC_CODES.has(r.refPanel) ? '' : keyword, pageNo: 1, pageSize })
  let list = res.list || []
  if (SINGLE_DOC_CODES.has(r.refPanel) && list.length && list[0] && list[0].detail) {
    const tabKey = (await getPanelConfig(r.refPanel))?.detail?.tabs?.[0]?.key || 'items'
    list = list[0].detail[tabKey] || []
    const flt = r.filter || {}
    if (Object.keys(flt).length) {
      list = list.filter((row) => Object.entries(flt).every(([k, v]) => String(row[k]) === String(v)))
    }
    if (keyword) {
      const k = String(keyword).toLowerCase()
      list = list.filter((row) => Object.values(row).some((v) => String(v ?? '').toLowerCase().includes(k)))
    }
  }
  return list
}

// 参照显示文本：真实模式下返回 null（调用方回退显示原值）
export function refLabelOf(field, value) {
  if (value === undefined || value === null || value === '') return ''
  return null
}

// 参照字段选项解析：真实模式下选项由后端 meta 提供，前端不再本地解析
export function resolveRefOptions(field) {
  return null
}

// 字段选项统一解析：普通下拉返回原 options
export function fieldOptions(field) {
  return field.options || []
}

// ==================== 接口（本地后端 / PanelX 后端代理 双链路） ====================

export async function getPanelConfig(panelCode) {
  if (USE_PANELX) return platformConfig(panelCode)
  return unwrap(await request.get('/px/getPanelConfig', { params: { panelCode } }))
}

export async function getPermMatrix(panelCode) {
  if (USE_PANELX) {
    const sdk = await requireAuthed()
    return unwrap(await platformCall((sd) => sd.api.getPermMatrix({ panelCode: resolvePanelCode(panelCode) })))
  }
  return unwrap(await request.get('/px/getPermMatrix', { params: { panelCode } }))
}

export async function getNewFormPermMatrix({ panelCode, operationName }) {
  if (USE_PANELX) {
    const sdk = await requireAuthed()
    const p = unwrap(await platformCall((sd) => sd.api.getNewFormPermMatrix({ panelCode: resolvePanelCode(panelCode), operationName })))
    const cfg = await platformConfig(panelCode)
    const fp = cfg.metadata.panelPageDto.formPages?.[0] || {}
    return {
      data: adaptFormData(p.data),
      meta: adaptMeta(p.meta, p.dataSchema, fp.fieldNames),
      privilege: p.privilege,
      detail: { tabs: [] },
      buttonGroups: panelxButtonGroups(fp.bottomOperationBarBtn, ['新增流程']),
    }
  }
  return unwrap(await request.get('/px/getNewFormPermMatrix', { params: { panelCode, operationName } }))
}

export async function getFormDescriptor({ panelCode, code }) {
  if (USE_PANELX) {
    const sdk = await requireAuthed()
    const p = unwrap(await platformCall((sd) => sd.api.getFormDescriptor({ panelCode: resolvePanelCode(panelCode), code })))
    const cfg = await platformConfig(panelCode)
    const fp = cfg.metadata.panelPageDto.formPages?.[0] || {}
    return {
      data: adaptFormData(p.data),
      meta: adaptMeta(p.meta, p.dataSchema, fp.fieldNames),
      privilege: p.privilege,
      detail: { tabs: [] },
      buttonGroups: panelxButtonGroups(fp.bottomOperationBarBtn),
    }
  }
  return unwrap(await request.get('/px/getFormDescriptor', { params: { panelCode, code } }))
}

export async function queryFormDataList(params) {
  if (USE_PANELX) {
    const sdk = await requireAuthed()
    const p = unwrap(await platformCall((sd) => sd.api.queryFormDataList({ ...params, panelCode: resolvePanelCode(params.panelCode) })))
    return { totalSize: p.totalSize ?? 0, list: p.list || [] }
  }
  return unwrap(await request.post('/px/queryFormDataList', params))
}

export async function callButton({ panelCode, buttonName, formData, buttonParam }) {
  if (USE_PANELX) {
    const sdk = await requireAuthed()
    const res = await platformCall((sd) => sd.api.callButton({ panelCode: resolvePanelCode(panelCode), buttonName, formData, buttonParam }))
    return unwrap(res)
  }
  // 本地后端：按钮名对齐后端（中止执行/整单中止→中止、草稿→取消中止、保存类→提交）
  const apiName = buttonName === '中止执行' || buttonName === '整单中止' ? '中止' : buttonName === '草稿' ? '取消中止' : buttonName === '保存' || buttonName === '保存为草稿' || buttonName === '保存新增' ? '提交' : buttonName
  return unwrap(await request.post('/px/callButton', { panelCode, buttonName: apiName, formData, buttonParam }))
}

export async function deleteForms({ panelCode, rowCodes }) {
  if (USE_PANELX) {
    const sdk = await requireAuthed()
    const res = await platformCall((sd) => sd.api.deleteForms({ panelCode: resolvePanelCode(panelCode), rowCodes }))
    return unwrap(res)
  }
  return unwrap(await request.post('/px/deleteForms', { panelCode, rowCodes }))
}

// ==================== 专属视图数据（生产看板 / 返修工作台） ====================
// 数据源原为 mock 种子（Mock 模式 2026-08-20 移除），现恒空；页面显示提示文案
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
