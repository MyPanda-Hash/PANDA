// 11 项权限动作（与后端 RoleService.PERMISSION_ACTIONS 对齐；顺序即权限矩阵列序）
// review/adjust 为预留权限位：当前无对应按钮，矩阵中置灰展示
export const PERM_ACTIONS = [
  { code: 'view', name: '查看' },
  { code: 'query', name: '查询' },
  { code: 'add', name: '新增' },
  { code: 'edit', name: '修改' },
  { code: 'delete', name: '删除' },
  { code: 'export', name: '导出' },
  { code: 'print', name: '打印' },
  { code: 'audit', name: '审批' },
  { code: 'price', name: '价格' },
  { code: 'review', name: '复核', reserved: true },
  { code: 'adjust', name: '调整', reserved: true },
]

// 按钮名 → 权限码（与后端 PxService.ACTION_BUTTONS 对齐；未列出的按钮不受权限控制）
// 「生成XX」生单按钮统一按 add 控制（后端 permOfButton 同口径）
const BUTTON_PERM_MAP = {
  查找: 'query', 查询: 'query', 刷新: 'query',
  新增: 'add', 新增流程: 'add', 引入常用单据: 'add', 选单: 'add', 扫描填单: 'add',
  修改: 'edit', 保存: 'edit', 保存新增: 'edit', 保存为草稿: 'edit',
  中止: 'edit', 中止执行: 'edit', 整单中止: 'edit', 草稿: 'edit', 取消中止: 'edit',
  删除: 'delete', 删除单据: 'delete',
  导出: 'export', 明细标签打印: 'export', 下载导入模板: 'export', 导入: 'export',
  直接打印: 'print', 打印: 'print', 预览: 'print', 打印模板设置: 'print', 打印情况: 'print', 保存打印: 'print',
  提交审批: 'audit', 审批通过: 'audit', 审批驳回: 'audit', 驳回审批: 'audit', 审批情况: 'audit', 弃审: 'audit', 审核: 'audit',
}

/** 按钮所需权限码；无权限控制要求的按钮返回 null */
export function permOfAction(action) {
  if (typeof action !== 'string') return null
  if (BUTTON_PERM_MAP[action]) return BUTTON_PERM_MAP[action]
  if (action.startsWith('生成')) return 'add'
  return null
}

/**
 * 按角色权限过滤按钮组：未授权权限码对应的按钮从分组中移除，空分组丢弃。
 * admin 或无矩阵数据（旧会话）时原样返回（不拦截）。
 */
export function filterButtonGroups(rawGroups, panelCode, user) {
  if (!Array.isArray(rawGroups)) return rawGroups
  if (!user || user.isAdmin) return rawGroups
  // 旧会话（panelPerms 为空且可见面板非空）不拦截，等重新登录/fetchPerms 后生效
  const hasMatrix = Object.keys(user.panelPerms || {}).length > 0
  if (!hasMatrix && user.visiblePanels?.length) return rawGroups
  const out = []
  for (const group of rawGroups) {
    const actions = (group.actions || group.items || []).filter((a) => {
      const perm = permOfAction(a)
      return !perm || user.hasPerm(panelCode, perm)
    })
    if (actions.length) out.push({ ...group, actions })
  }
  return out
}

const PRICE_FIELD_RE = /单价|金额|价税|售价|成本价|报价/

/** price 权限未授权时，价格/金额类字段只读（表头与明细同口径） */
export function priceFieldLocked(fieldName, panelCode, user) {
  if (!fieldName || !user || user.isAdmin) return false
  return PRICE_FIELD_RE.test(fieldName) && !user.hasPerm(panelCode, 'price')
}
