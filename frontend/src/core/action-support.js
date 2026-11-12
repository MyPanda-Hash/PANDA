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
 * 3. EXTRA_ACTIONS    —— 通过 PanelActionHandler 扩展点注册的动作（当前有「变更」「执行分摊」）
 *
 * 报表专属动作（导出/打印/发送邮件/退出）只在 reportMode 下可用：
 * 列表页没有这些实现，点了会走 callButton → 后端也没有对应 case。
 */

/**
 * 采购发票行选来源单（前端弹窗，不调后端 callButton）。
 * 名字以「选」开头但不是选单生单动作，不能按 selectConfig 判定是否支持。
 */
export const INVOICE_LINE_ACTION = '选入库单行'

/**
 * 采购发票 Excel 导入（前端弹窗，按「发票号码 + 供应商」分组逐张调保存）。
 * 与明细追加动作「导入」区分：这里整张发票建档，不追加到当前单据。
 */
export const INVOICE_IMPORT_ACTION = '导入发票'

/** 前端自己实现的动作 */
export const FRONTEND_ACTIONS = new Set([
  '新增', '新建', '新增流程', '修改',
  '保存', '保存新增', '保存为草稿', '提交',
  '删除', '删除单据', '刷新',
  '查询', '查找', '恢复', '导入',
  '预览', '扫描填单', '表格调整', '字段管理', '打印发票', '排产',
  // 专用选单弹窗（不走 selectConfig，见 PanelxList 的分支）：
  // 加工单选销售订单 → 排产工作台；工序汇报选生产加工单 → 逐道报工；成品报检选生产加工单 → 报检候选
  '选销售订单', '选生产加工单',
  '审核', '弃审',
  INVOICE_LINE_ACTION,
  INVOICE_IMPORT_ACTION,
])

/** 与 PxService.callButton 的 switch 分支一一对应（排序无关） */
export const BACKEND_ACTIONS = new Set([
  '刷新', '新增流程', '保存', '保存新增', '保存为草稿', '提交', '删除',
  '审核', '弃审', '关闭', '手工完工', '中止', '整单中止', '取消中止',
  '齐套检查', '选工艺路线', '按产成品重算材料',
  '生成生产加工单', '生成工序汇报单（自制汇报）', '生成请购单',
  '生成采购订单', '生成到货单', '生成采购入库单',
  '生成产成品入库单', '生成检验单', '生成材料出库单',
  '生成领料申请单', '生成成品报检单',
  '生成返工生产加工单（按累计报废入库数量）', '生成补投生产加工单（按累计报废入库数量）',
  '倒冲差异调整',
  '中止（释放未执行量）',
  '生成调拨单', '生成调拨单(分单)', '生成委外发料单', '生成委外发料单(分单)',
  '生成委外入库单', '生成委外加工费用单',
  '生成销售订单', '生成销售订单(普通销售)',
  '生成销售发票', '生成销售发票(普通销售)', '生成销售发票(销售退货)',
  // 2026-11-05：开票/作废/红冲 此前登记在此表但前后端均无实现，
  // 按钮会显示、点了必然 409「未定义按钮规则」。按本表设计意图移除，待实现后再登记。
  '生成销售费用分摊单', '生成采购发票', '生成采购发票（普通采购）', '生成采购发票（采购退货）',
  '生成采购费用分摊单',
  '生成销售出库单', '生成销售出库单(普通销售)',
  // 生成销售退货单：后端无对应 case（销售退货走 SALE_RETURN 面板本身），暂不登记。
  '生成销货单', '生成销货单(普通销售)',
  '提交审批', '审批通过', '审批驳回', '驳回审批', '审批情况',
  '核销', '反核销',
  // 冲抵/反冲抵：后端无对应 case（收付款核销走「核销/反核销」），暂不登记。
  '生成采购退货单',
  '确认处置',
])

/** PanelActionHandler 扩展点注册的动作 */
export const EXTRA_ACTIONS = new Set(['变更', '执行分摊'])

/** 只有报表面板才有的动作 */
export const REPORT_ACTIONS = new Set(['导出', '打印', '发送邮件', '退出'])

/** 选单类动作（选单 / 选销售订单…）：需要面板配了 selectConfig 才有来源 */
export function isSelectAction(action) {
  if (typeof action !== 'string' || action === '') return false
  // 「选工艺路线」是加工单按路线带出工序的后端动作，不是选单来源，别被「选」字前缀误判；
  // 「选销售订单 / 选生产加工单」是专用弹窗动作，由代码分支处理，不依赖面板 selectConfig。
  if (action === '选工艺路线') return false
  if (action === '选销售订单' || action === '选生产加工单') return false
  if (action === INVOICE_LINE_ACTION) return false
  return action === '选单' || action.startsWith('选')
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