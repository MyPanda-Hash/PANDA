/**
 * doc-rail-rows.js —— 左侧「单据列表」栏的列与行模型（纯逻辑，可 node --test）
 *
 * 侧栏镜像**当前页单据列表**（不再额外请求）：点行只切 curIdx，与主表预览、明细区共用同一份
 * 数据，所以「第几张」永远和顶栏「第 3/27 张」对得上。前端只做本页关键字过滤；全库搜索
 * 由父组件把关键字交给后端（query.keyword）。
 *
 * 2026-11-04（参照「采购订单选择」界面）：从两行卡片改成表头列式——单号 | 日期 |
 * 面板中间列 | 审核状态。中间列只放一个「一眼能区分这张单」的表头字段，按面板挑；
 * 没挑到的面板按行里的客户/供应商/往来单位自动取一列。列位置固定，扫视比两行卡片快。
 *
 * 行的身份必须带**原始下标**：过滤后列表变了，点第 2 行仍要落到 list[原始下标]，
 * 否则筛完再点会切到别的单据（这类错位在单据类界面上是"改错单"级别的 bug）。
 */

const NO_KEYS = ['编号', '单据编号', '单号']
const DATE_KEYS = ['单据日期', '日期']
const PARTNER_KEYS = ['客户', '客户名称', '往来单位', '往来单位名称', '供应商', '供应商名称', '建议供应商', '供应单位', '委外供应商']
const STATUS_KEYS = ['单据状态', '状态']

/**
 * 面板中间列：单号/日期/审核状态之间只放一个最能区分单据的表头字段。
 * 值取不到时单元格显示「-」，列本身不隐藏——列位置稳定，翻页不会左右跳。
 * 未登记的面板由 partnerColumnOf 从行里的往来单位字段自动取一列；行里也没有就只留三列。
 * 新增面板时按业务语义补一条，不要按「配置里有这个字段」硬塞（空列等于没配）。
 */
const PANEL_MIDDLE_FIELDS = {
  // 采购链路
  PU_REQ: '请购人', // 谁提出要货；部门/建议供应商在实测数据里常空
  PU_ORDER: '供应商',
  ARRIVAL_IN: '供应商',
  PURCHASE_IN: '供应商',
  PURCHASE_RETURN: '供应商',
  PU_INVOICE: '供应商',
  INSPECTION: '供应商',
  // 销售链路
  SALE_INV: '客户',
  SO_ORDER: '客户',
  PICK_ORDER: '客户',
  QUOTE_ORDER: '客户',
  // 生产 / 领料
  MATERIAL_REQ: '领料申请人',
  DISPATCH: '加工单号',
  FINISH_INSPECT: '加工单号',
}

/** 按候选键取第一个非空值（行键是中文，不同面板的叫法不同） */
export function pick(row, keys) {
  for (const key of keys) {
    const value = row?.[key]
    if (value !== undefined && value !== null && String(value).trim() !== '') return String(value).trim()
  }
  return ''
}

export function docNoOf(row) {
  return pick(row, NO_KEYS)
}

export function docDateOf(row) {
  return pick(row, DATE_KEYS)
}

export function docPartnerOf(row) {
  return pick(row, PARTNER_KEYS)
}

export function docStatusOf(row) {
  return pick(row, STATUS_KEYS)
}

/**
 * 状态色调：与项目现有状态色一致（绿=已审核族、青蓝=已完成、琥珀=流转中、红=作废/驳回、灰=草稿）。
 * 只用于侧栏小标签，不参与任何业务判断（业务判定仍走 core/doc-status.js 的 isDocApproved）。
 */
const TONE_OK = ['已审核', '已归档', '已审批', '已通过', '已转']
const TONE_DONE = ['已完成', '已完工', '已关闭']
const TONE_PENDING = ['审批中', '待审批', '待二级审批', '生产中', '修改中', '删除申请中', '提交审批']
const TONE_DANGER = ['已作废', '已中止', '已终止', '驳回', '审批驳回']

export function statusTone(status) {
  const text = String(status || '').trim()
  if (!text) return 'draft'
  if (TONE_DONE.includes(text)) return 'done'
  if (TONE_OK.includes(text)) return 'ok'
  if (TONE_DANGER.includes(text)) return 'danger'
  if (TONE_PENDING.includes(text)) return 'pending'
  return 'draft'
}

function middleColumn(label) {
  return { key: 'f:' + label, label, kind: 'text', align: 'left', keys: [label] }
}

/** 未登记面板的兜底：本页有哪类往来单位就显示哪一列（全空则不加列） */
function partnerColumnOf(rows) {
  const list = Array.isArray(rows) ? rows : []
  for (const label of PARTNER_KEYS) {
    if (list.some((row) => row && String(row[label] ?? '').trim() !== '')) return middleColumn(label)
  }
  return null
}

/**
 * 面板列定义：单号 | 日期 | 中间列（0 或 1）| 审核状态。
 * kind 决定对齐和渲染方式：no=等宽单号、date=日期、text=普通文本、tag=状态标签。
 */
export function railColumnsOf(panelCode, rows) {
  const field = PANEL_MIDDLE_FIELDS[String(panelCode || '')]
  const middle = field ? middleColumn(field) : partnerColumnOf(rows)
  return [
    { key: 'no', label: '单号', kind: 'no', align: 'left', keys: NO_KEYS },
    { key: 'date', label: '日期', kind: 'date', align: 'left', keys: DATE_KEYS },
    ...(middle ? [middle] : []),
    { key: 'status', label: '审核状态', kind: 'tag', align: 'center', keys: STATUS_KEYS },
  ]
}

/** 一格的值：列自带 keys（或 derive）时按列取，绝不按列下标猜 */
export function railCellOf(row, column) {
  if (!column) return ''
  if (typeof column.derive === 'function') return String(column.derive(row) ?? '').trim()
  return pick(row, column.keys || [column.label])
}

/** 旧调用（不传 columns）保持原两行卡片的口径：单号/日期/往来单位/状态 */
const LEGACY_COLUMNS = [
  { key: 'no', label: '单号', kind: 'no', align: 'left', keys: NO_KEYS },
  { key: 'date', label: '日期', kind: 'date', align: 'left', keys: DATE_KEYS },
  { key: 'partner', label: '往来单位', kind: 'text', align: 'left', keys: PARTNER_KEYS },
  { key: 'status', label: '审核状态', kind: 'tag', align: 'center', keys: STATUS_KEYS },
]

/**
 * 当前页行 → 侧栏行。keyword 只过滤本页（空关键字原样返回），命中范围是本行所有可见单元格
 * （单号/日期/中间列/状态）；过滤不改变 index，点行仍然回到 list[index]。
 */
export function railRowsOf(rows, keyword, columns) {
  const list = Array.isArray(rows) ? rows : []
  const cols = Array.isArray(columns) && columns.length ? columns : LEGACY_COLUMNS
  const kw = String(keyword ?? '').trim().toLowerCase()
  const out = []
  list.forEach((row, index) => {
    if (!row) return
    const cells = cols.map((column) => railCellOf(row, column))
    if (kw && !cells.join(' ').toLowerCase().includes(kw)) return
    const status = docStatusOf(row)
    out.push({
      index,
      no: docNoOf(row),
      date: docDateOf(row),
      partner: docPartnerOf(row),
      status,
      tone: statusTone(status),
      cells,
    })
  })
  return out
}

/** 总页数（至少 1 页，与顶栏「第 x/y 张」同口径） */
export function railPageCount(total, pageSize) {
  const size = Math.max(1, Number(pageSize) || 1)
  const count = Number(total) || 0
  return Math.max(1, Math.ceil(count / size))
}
