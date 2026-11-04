/**
 * doc-rail-rows.js —— 左侧「单据选择」栏的行模型（纯逻辑，可 node --test）
 *
 * 侧栏镜像**当前页单据列表**（不再额外请求）：点行只切 curIdx，与主表预览、明细区共用同一份
 * 数据，所以「第几张」永远和顶栏「第 3/27 张」对得上。前端只做本页关键字过滤；全库搜索
 * 由父组件把关键字交给后端（query.keyword）。
 *
 * 行的身份必须带**原始下标**：过滤后列表变了，点第 2 行仍要落到 list[原始下标]，
 * 否则筛完再点会切到别的单据（这类错位在单据类界面上是"改错单"级别的 bug）。
 */

const NO_KEYS = ['编号', '单据编号', '单号']
const DATE_KEYS = ['单据日期', '日期']
const PARTNER_KEYS = ['客户', '供应商', '往来单位', '往来单位名称']
const STATUS_KEYS = ['单据状态', '状态']

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

/**
 * 当前页行 → 侧栏行。keyword 只过滤本页（空关键字原样返回）；
 * 过滤不改变 index，点行仍然回到 list[index]。
 */
export function railRowsOf(rows, keyword) {
  const list = Array.isArray(rows) ? rows : []
  const kw = String(keyword ?? '').trim().toLowerCase()
  const out = []
  list.forEach((row, index) => {
    if (!row) return
    const no = docNoOf(row)
    const date = docDateOf(row)
    const partner = docPartnerOf(row)
    const status = docStatusOf(row)
    if (kw) {
      const hay = `${no} ${date} ${partner} ${status}`.toLowerCase()
      if (!hay.includes(kw)) return
    }
    out.push({ index, no, date, partner, status, tone: statusTone(status) })
  })
  return out
}

/** 总页数（至少 1 页，与顶栏「第 x/y 张」同口径） */
export function railPageCount(total, pageSize) {
  const size = Math.max(1, Number(pageSize) || 1)
  const count = Number(total) || 0
  return Math.max(1, Math.ceil(count / size))
}
