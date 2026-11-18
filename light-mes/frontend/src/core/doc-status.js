// 已审批判据（PanelxList / PanelxForm 共享）：印章与绿行统一用同一把尺子。
// 两类审批链路最终都落 单据状态=已审核：
//   直接审核：草稿 → 已审核（表头不写 审批状态）
//   审批流：草稿 → 审批中 → 已通过/已驳回（落盘同为 已审核，表头写 审批状态=已通过）
// 已审核后的生产流转（生产中/已完工/已关闭）视为仍已审批；弃审/驳回回草稿则不再算。
// 兼容历史数据：表头 审批状态=已审批/已通过 也判已审批（数据值比较，不进 tt()）。
export const APPROVED_DOC_STATUSES = ['已审核', '生产中', '已完工', '已关闭']

export function isDocApproved(row) {
  if (!row) return false
  const status = row['单据状态']
  // 单据状态优先：弃审/驳回会把状态退回草稿/审批中，即使表头残留历史的「审批状态」也不能再显示印章。
  if (status === '草稿' || status === '审批中') return false
  if (APPROVED_DOC_STATUSES.includes(status)) return true
  // 没有单据状态可判时（历史数据）才退回表头审批状态
  return ['已审批', '已通过'].includes(row['审批状态'])
}

/**
 * 明细区块左上角的印章。
 *
 * <p>以前固定写「已审批」，单据已经进入生产流转了印章还停在审批那一刻。
 * 现在文字跟单据状态走：已审核→已审批；生产中→生产中；已完工→已完工；
 * 已关闭/已中止照原样显示。色调区分「审批通过 / 进行中 / 完工 / 终止」四态。
 */
export function docStatusStamp(row) {
  if (!isDocApproved(row)) return null
  const status = row['单据状态']
  if (status === '生产中') return { text: '生产中', tone: 'doing' }
  if (status === '已完工') return { text: '已完工', tone: 'done' }
  if (status === '已关闭' || status === '已中止') return { text: status, tone: 'stop' }
  return { text: '已审批', tone: 'ok' }
}
