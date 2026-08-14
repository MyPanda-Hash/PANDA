// ==================== 面板引擎内核（通用层） ====================
// 跨项目可复用。规则：禁止 import 任何业务文件（@/business/**）。
// 业务相关（面板码映射、面板配置注册表、分发链）由业务层持有或注入。
import { requireAuthed } from './sdk'

export function unwrap(res) {
  if (!res) return res
  return res.data ?? res
}

export function errMsg(e) {
  return e?.response?.data?.message || e?.errorDescription || e?.message || String(e)
}

// 平台 dataType（STRING/Decimal/下拉框('IML_00001_CM','文本');/Relate…）→ 引擎中文类型
export function mapType(t) {
  const s = String(t || '').trim()
  if (!s) return '文本'
  if (s.startsWith('下拉框') || s === 'Relate') return '下拉框'
  if (['Decimal', 'Double', 'Float', 'BigDecimal', 'Long', 'Number'].includes(s) || s.includes('小数') || s.includes('金额')) return '小数'
  if (['Integer', 'INTEGER'].includes(s) || s.includes('整数')) return '整数'
  if (['Date', 'DATE', 'DateTime', 'Timestamp', '时间', '日期'].includes(s)) return '日期'
  if (['Boolean', 'BOOL', 'boolean', '是否'].includes(s)) return '是否'
  if (['图片', 'Image'].includes(s)) return '图片'
  return '文本'
}

export function parseJson(v) {
  if (typeof v === 'string') {
    try { return JSON.parse(v) } catch (e) { return {} }
  }
  return v || {}
}

// 平台 getPanelConfig → 引擎配置（列表页查询区/网格列/工具栏；平台面板无明细）
export function adaptPanelConfig(platformCfg, requestedCode) {
  const meta = parseJson(platformCfg?.metadata)
  const dataSchema = parseJson(platformCfg?.dataSchema)
  const fields = dataSchema.fields || []
  const tp = meta.panelPageDto?.tablePages?.[0] || {}
  const fp = meta.panelPageDto?.formPages?.[0] || {}
  const tableName = tp.tableName || '数据列表'
  const colNames = String(tp.tableColNames || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)
  const topBtns = (tp.topBarBtn || []).map((b) => b.buttonName)
  const queryFields = fields.map((f) => ({ dataName: f.dataName, dataType: mapType(f.dataType), options: [] }))
  return {
    metadata: {
      panelCode: requestedCode,
      panelName: meta.panelName || requestedCode,
      panelCategory: meta.panelCategory || '',
      panelState: meta.panelState || null,
      panelPageDto: {
        tablePages: [
          {
            tableName,
            queryFields,
            gridTabs: [{ label: tableName, rowSource: '', columns: colNames }],
            topBarBtn: tp.topBarBtn || [],
            events: tp.events || [],
          },
        ],
        formPages: fp.formName || fp.fieldNames ? [fp] : [],
      },
      panelButtons: (meta.panelButtons || []).map((b) => b.buttonName),
      buttonGroups: [{ name: '操作', actions: topBtns.length ? topBtns : ['新增流程', '删除', '刷新'] }],
    },
    dataSchema: {
      type: 'object',
      fields: fields.map((f) => ({ dataName: f.dataName, dataType: mapType(f.dataType), isRequired: !!f.isRequired, defaultValue: f.defaultValue, options: [] })),
    },
    detail: { tabs: [] },
  }
}

// 平台 meta（code=拼音/英文）→ 引擎 meta（code=中文名，与平台数据键一致，表单才能绑定）
export function adaptMeta(platformMeta, dataSchema, fieldNames) {
  const byName = {}
  for (const f of parseJson(dataSchema).fields || []) byName[f.dataName] = f
  const names = fieldNames
    ? String(fieldNames).split(',').map((s) => s.trim()).filter(Boolean)
    : []
  let out = (platformMeta || []).map((m) => {
    const s = byName[m.name] || {}
    return {
      code: m.name,
      name: m.name,
      dataType: mapType(s.dataType || m.dataType),
      isNotNull: !!m.isNotNull,
      defaultValue: m.defaultValue,
      options: [],
      autoCode: false,
    }
  })
  if (names.length) out = out.filter((m) => names.includes(m.name))
  return out
}

// 平台面板状态字段（如 测试流程状态）→ 视图的 单据状态（驱动只读/状态标签）
export function adaptFormData(data) {
  const out = { ...(data || {}) }
  if (out['测试流程状态'] !== undefined && out['单据状态'] === undefined) {
    out['单据状态'] = out['测试流程状态']
  }
  return out
}

export function panelxButtonGroups(bottomBtns, extra = []) {
  const actions = [...extra, ...(bottomBtns || []).map((b) => b.buttonName)]
  return [{ name: '操作', actions: actions.length ? actions : ['提交'] }]
}

// 平台 API 统一包装：非 200 状态 / 认证失效 → 归一化为可识别错误（驱动登录弹窗）
export async function platformCall(fn) {
  const sdk = await requireAuthed()
  try {
    const res = await fn(sdk)
    if (res && res.state !== undefined && String(res.state) !== '200') {
      throw { response: { data: { message: res.msg || ('平台调用失败：' + res.state) } } }
    }
    return res
  } catch (e) {
    if (e && e.response) throw e
    const failed = !sdk.user || !sdk.user.isAuthenticated()
    const msg = String((e && (e.message || e.msg)) || e || '')
    if (failed || /未登录|登录已失效|401/i.test(msg)) {
      throw { response: { data: { message: 'PanelX 平台未登录' } } }
    }
    throw e
  }
}