/**
 * 按钮动作支持表：哪些动作真的有实现，决定工具栏显示什么。
 *
 * 背景：面板配置（panel_config.metadata.buttonGroups）里带着大量 T+ 按钮名，
 * 后端 PxService.callButton 的 switch 只实现了其中一部分，其余会抛
 * 「未定义按钮规则：xxx（请注册 PanelActionHandler）」。与其点出来报错，不如不显示。
 *
 * 三个来源：
 * 1. FRONTEND_ACTIONS —— 前端自己处理的（弹窗、打印、导入、内联编辑等）
 * 2. BACKEND_ACTIONS  —— PxService.callButton 的 switch 分支，必须与 Java 保持一致
 *    （tools/action-support-audit.mjs 会解析 Java 源码比对，漂移即报错）
 * 3. EXTRA_ACTIONS    —— 通过 PanelActionHandler 扩展点注册的动作（当前只有「变更」）
 *
 * 报表专属动作（导出/打印/发送邮件/退出）只在 reportMode 下可用：
 * 列表页没有这些实现，点了会走 callButton → 后端也没有对应 case。
 */

/** 前端自己实现的动作 */
export const FRONTEND_ACTIONS = new Set([
  '新增', '新建', '新增流程', '修改',
  '保存', '保存新增', '保存为草稿', '提交',
  '删除', '删除单据', '刷新',
  '查询', '查找', '恢复', '导入',
  '预览', '扫描填单', '表格调整', '字段管理',
  '审核', '弃审',
])

/** 与 PxService.callButton 的 switch 分支一一对应（排序无关） */
export const BACKEND_ACTIONS = new Set([
  '刷新', '新增流程', '保存', '保存新增', '保存为草稿', '提交', '删除',
  '审核', '弃审', '关闭', '手工完工', '中止', '整单中止', '取消中止',
  '生成生产加工单', '生成工序汇报单（自制汇报）', '生成采购订单', '生成采购入库单',
  '生成进货单', '生成产成品入库单', '生成检验单', '生成材料出库单',
  '生成调拨单', '生成调拨单(分单)', '生成委外发料单', '生成委外发料单(分单)',
  '生成委外入库单', '生成委外加工费用单',
  '生成销售订单', '生成销售订单(普通销售)',
  '生成销售发票', '生成销售发票(普通销售)', '生成销售发票(销售退货)',
  '开票', '作废', '红冲',
  '生成销售费用分摊单', '生成采购发票', '生成采购发票（普通采购）', '生成采购发票（采购退货）',
  '生成采购费用分摊单',
  '生成销售出库单', '生成销售出库单(普通销售)', '生成销售退货单',
  '生成销货单', '生成销货单(普通销售)',
  '提交审批', '审批通过', '审批驳回', '驳回审批', '审批情况',
  '核销', '反核销', '冲抵', '反冲抵',
])

/** PanelActionHandler 扩展点注册的动作 */
export const EXTRA_ACTIONS = new Set(['变更'])

/** 只有报表面板才有的动作 */
export const REPORT_ACTIONS = new Set(['导出', '打印', '发送邮件', '退出'])

/** 选单类动作（选单 / 选销售订单…）：需要面板配了 selectConfig 才有来源 */
export function isSelectAction(action) {
  return typeof action === 'string' && action !== '' && (action === '选单' || action.startsWith('选'))
}

/**
 * 动作是否有实现。
 * @param {string} action 动作名（中文数据键，不翻译）
 * @param {{reportMode?: boolean, hasSelect?: boolean}} options
 */
export function isActionSupported(action, options = {}) {
  if (typeof action !== 'string' || action === '') return false
  const { reportMode = false, hasSelect = false } = options
  if (isSelectAction(action)) return hasSelect
  if (REPORT_ACTIONS.has(action)) return reportMode
  return FRONTEND_ACTIONS.has(action) || BACKEND_ACTIONS.has(action) || EXTRA_ACTIONS.has(action)
}

/**
 * 过滤按钮组：摘掉没有实现的动作，动作清空的组一并去掉。
 * 不改动入参（面板配置是响应式数据，就地改会污染缓存）。
 */
export function filterSupportedActions(rawGroups, options = {}) {
  return (Array.isArray(rawGroups) ? rawGroups : [])
    .filter((group) => group && typeof group === 'object')
    .map((group) => {
      const actions = Array.isArray(group.actions) ? group.actions : (Array.isArray(group.items) ? group.items : [])
      return { ...group, actions: actions.filter((action) => isActionSupported(action, options)) }
    })
    .filter((group) => group.actions.length > 0)
}