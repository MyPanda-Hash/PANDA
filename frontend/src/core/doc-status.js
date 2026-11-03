// 已审批判据（PanelxList / PanelxForm 共享）：印章与绿行统一用同一把尺子。
// 两类审批链路最终都落 单据状态=已审核：
//   直接审核：草稿 → 已审核（表头不写 审批状态）
//   审批流：草稿 → 审批中 → 已通过/已驳回（落盘同为 已审核，表头写 审批状态=已通过）
// 已审核后的生产流转（生产中/已完工/已关闭）视为仍已审批；弃审/驳回回草稿则不再算。
// 兼容历史数据：表头 审批状态=已审批/已通过 也判已审批（数据值比较，不进 tt()）。
export const APPROVED_DOC_STATUSES = ['已审核', '生产中', '已完工', '已关闭']

export function isDocApproved(row) {
  if (!row) return false
  return APPROVED_DOC_STATUSES.includes(row['单据状态']) || ['已审批', '已通过'].includes(row['审批状态'])
}
