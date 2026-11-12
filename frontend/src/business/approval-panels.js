/**
 * 审批面板清单：业务总览的流程节点靠它判断「这个环节要不要审批」。
 *
 * <p>口径与后端 `PxService#APPROVAL_PANELS` 一致（那边决定提交审批/审批通过/弃审是否有实现）。
 * 前端没有接口能一次拿到全部面板的按钮配置，所以这里维护一份清单，并用单测
 * （`frontend/test/approval-panels.test.js`）直接解析 Java 源码比对，漂移即失败。
 */

export const APPROVAL_PANELS = new Set([
  'SO_ORDER', 'PURCHASE_IN', 'FINISH_IN', 'OTHER_IN', 'SALE_OUT', 'MATERIAL_OUT', 'OTHER_OUT',
  'MANU_ORDER', 'INIT_AP', 'INIT_AR', 'INIT_BALANCE', 'BOM', 'ROUTE', 'PU_REQ',
  'ARRIVAL_IN', 'FINISH_INSPECT', 'FIRST_INSPECT', 'INSPECTION', 'PROCESS_INSPECTION',
  'MATERIAL_REQ', 'PICK_ORDER',
  'PU_ORDER', 'SALE_INV', 'PURCHASE_RETURN',
  'TRANSFER', 'OUTSOURCE_ORDER', 'OUTSOURCE_ISSUE', 'OUTSOURCE_IN', 'OUTSOURCE_FEE',
  'QUOTE_ORDER', 'SALE_INVOICE', 'EXPENSE', 'SALE_COST_ALLOC', 'PU_INVOICE', 'PU_COST_ALLOC',
  'STOCK_CHECK', 'LOCATION_ADJUST',
])

/** 该面板是否走审批流。 */
export function requiresApproval(panelCode) {
  return APPROVAL_PANELS.has(String(panelCode || '').trim())
}

/** 未审批的单据状态：草稿（还没提交）与审批中（提交了还没通过）。 */
export function isUnapprovedStatus(status) {
  const text = String(status || '').trim()
  return text === '草稿' || text === '审批中'
}

/**
 * 该追踪环节是不是「卡在未审批」：既被判为卡点，单据又停在草稿/审批中。
 *
 * <p>只认这两个状态：已审核却卡住说明是下游没跟上，与审批无关，不能标成待审批。
 */
export function isApprovalStall(step) {
  if (!step || step['是否卡点'] !== true) return false
  return isUnapprovedStatus(step['单据状态'])
}
