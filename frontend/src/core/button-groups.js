const SCAN_FILL_ACTION = '扫描填单'
const COLUMN_PREFS_ACTION = '表格调整'
const FIELD_MANAGE_ACTION = '字段管理'
const AUDIT_ACTION = '审核'
const APPROVAL_GROUP_NAME = '审批'
const SELECT_GROUP_NAME = '选单'
const SELECT_ACTION = '选单'

/** 审批流动作：出现在这些动作里的组，主按钮按「当前状态能做什么」动态取值 */
const APPROVAL_ACTIONS = ['提交审批', '审批通过', '审批驳回', '驳回审批', AUDIT_ACTION]
/** 审批组主按钮的优先级：状态流转在前，「审批情况」是只读查看永远排最后 */
const APPROVAL_PRIORITY = ['提交审批', AUDIT_ACTION, '审批通过', '审批驳回', '驳回审批', '弃审', '审批情况']
/** 双决策状态（审批中）里与「通过」并列露出的动作，不藏进下拉 */
const APPROVAL_SECONDARY = ['审批驳回', '驳回审批']

/**
 * 收纳组：名字只是分类（更多/工具/设置/智能），点组名本身没有明确动作。
 * 这类组的主按钮只负责展开菜单，不执行第一个动作——否则过滤掉未实现动作后，
 * 「更多」的首个动作会变成「导入」这类不相干的重动作，点了像是误触。
 */
const CATCH_ALL_GROUPS = ['更多', '工具', '设置', '智能']

export function isCatchAllGroup(group) {
  return CATCH_ALL_GROUPS.includes(String(group?.name ?? '').trim())
}

function cloneGroups(rawGroups) {
  return (Array.isArray(rawGroups) ? rawGroups : [])
    .filter((group) => group && typeof group === 'object')
    .map((group) => ({
      ...group,
      actions: [...new Set(actionsOf(group))],
    }))
}

function actionsOf(group) {
  if (Array.isArray(group?.actions)) return group.actions
  if (Array.isArray(group?.items)) return group.items
  return []
}

function enabledFlag(value) {
  return value === true || value === 1 || String(value ?? '').trim().toLowerCase() === 'true'
}

export function supportsScanFill(metadata) {
  return String(metadata?.panelCategory ?? '').trim().endsWith('单据')
    && !enabledFlag(metadata?.readonly)
    && !enabledFlag(metadata?.readOnly)
}

/** Keep old toolbar configurations compatible without mutating their arrays. */
export function ensureScanFillAction(rawGroups, metadata) {
  const groups = (Array.isArray(rawGroups) ? rawGroups : [])
    .filter((group) => group && typeof group === 'object')
    .map((group) => ({
      ...group,
      actions: [...new Set(actionsOf(group))],
    }))

  const categoryKnown = metadata
    && typeof metadata === 'object'
    && Object.prototype.hasOwnProperty.call(metadata, 'panelCategory')
  if (!categoryKnown) return groups

  for (const group of groups) {
    group.actions = group.actions.filter((action) => action !== SCAN_FILL_ACTION)
  }
  if (!supportsScanFill(metadata)) return groups

  const moreGroup = groups.find((group) => group.name === '更多')
  if (moreGroup) {
    if (!moreGroup.actions.length) moreGroup.actions.push('刷新')
    moreGroup.actions.push(SCAN_FILL_ACTION)
  } else {
    groups.push({ name: '更多', actions: ['刷新', SCAN_FILL_ACTION] })
  }
  return groups
}

/**
 * 表格列定制入口（阶段 C）：「表格调整」注入「更多」组。supported 为 false
 * （引擎未提供 saveColumnPrefs 可选方法）时确保入口不存在。同样不改动原数组。
 */
export function ensureColumnPrefsAction(rawGroups, supported) {
  const groups = (Array.isArray(rawGroups) ? rawGroups : [])
    .filter((group) => group && typeof group === 'object')
    .map((group) => ({
      ...group,
      actions: [...new Set(actionsOf(group))],
    }))

  if (!supported) {
    for (const group of groups) {
      group.actions = group.actions.filter((action) => action !== COLUMN_PREFS_ACTION)
    }
    return groups
  }

  const moreGroup = groups.find((group) => group.name === '更多')
  if (moreGroup) {
    if (!moreGroup.actions.length) moreGroup.actions.push('刷新')
    if (!moreGroup.actions.includes(COLUMN_PREFS_ACTION)) moreGroup.actions.push(COLUMN_PREFS_ACTION)
  } else {
    groups.push({ name: '更多', actions: ['刷新', COLUMN_PREFS_ACTION] })
  }
  return groups
}

/**
 * 字段管理入口（面板自助加字段）：「字段管理」注入「更多」组。
 * supported = 引擎提供了 addCustomField 且当前用户有该面板「修改」权限（服务端仍会再判一次）。
 */
export function ensureFieldManageAction(rawGroups, supported) {
  const groups = (Array.isArray(rawGroups) ? rawGroups : [])
    .filter((group) => group && typeof group === 'object')
    .map((group) => ({
      ...group,
      actions: [...new Set(actionsOf(group))],
    }))

  if (!supported) {
    for (const group of groups) {
      group.actions = group.actions.filter((action) => action !== FIELD_MANAGE_ACTION)
    }
    return groups
  }

  const moreGroup = groups.find((group) => group.name === '更多')
  if (moreGroup) {
    if (!moreGroup.actions.length) moreGroup.actions.push('刷新')
    if (!moreGroup.actions.includes(COLUMN_PREFS_ACTION)) moreGroup.actions.push(COLUMN_PREFS_ACTION)
    if (!moreGroup.actions.includes(FIELD_MANAGE_ACTION)) moreGroup.actions.push(FIELD_MANAGE_ACTION)
  } else {
    groups.push({ name: '更多', actions: ['刷新', COLUMN_PREFS_ACTION, FIELD_MANAGE_ACTION] })
  }
  return groups
}

/**
 * 审批组（含 提交审批 / 审批通过 / 审批驳回 / 审核）：这类组同一状态只有一个动作能执行，
 * 主按钮必须跟着状态走，否则会出现「主按钮是灰的、能点的全在隐藏下拉里」。
 */
export function isApprovalGroup(group) {
  return actionsOf(group).some((action) => APPROVAL_ACTIONS.includes(action))
}

/**
 * 组的主按钮动作（决定按钮文字与点击行为）。
 * 审批组：按 APPROVAL_PRIORITY 取第一个当前可用的动作（草稿→提交审批，审批中→审批通过，已审核→弃审）；
 * 其它组保持原语义——始终是第一个动作，禁用时按钮就是灰的（禁用即不执行）。
 */
export function primaryActionOf(group, isDisabled) {
  const actions = actionsOf(group)
  if (!actions.length) return ''
  if (!isApprovalGroup(group)) return actions[0]
  const usable = typeof isDisabled === 'function' ? actions.filter((action) => !isDisabled(action)) : actions
  if (!usable.length) return actions[0]
  for (const action of APPROVAL_PRIORITY) {
    if (usable.includes(action)) return action
  }
  return usable[0]
}

/**
 * 与主按钮并列露出的次要动作（审批中是「审批驳回」）：只在该动作当前可用时给出，
 * 避免把两个决策都压进下拉。其余组返回空数组。
 */
export function approvalSecondaryOf(group, primary, isDisabled) {
  if (!isApprovalGroup(group)) return []
  return actionsOf(group).filter((action) => (
    APPROVAL_SECONDARY.includes(action)
    && action !== primary
    && !(typeof isDisabled === 'function' && isDisabled(action))
  ))
}

/**
 * 面板是否配了选单来源：`selectConfig`（单来源）或 `selectConfigs`（多来源，按动作名取）。
 * 两个都没配的面板点「选单」没有来源可选，入口不该出现。
 */
export function hasSelectConfig(config) {
  if (!config || typeof config !== 'object') return false
  if (config.selectConfig && typeof config.selectConfig === 'object') return true
  const configs = config.selectConfigs
  return !!configs && typeof configs === 'object' && Object.keys(configs).length > 0
}

/**
 * 选单入口：没配 selectConfig/selectConfigs 的面板整组隐藏「选单」组，并从其他组里摘掉「选单」动作；
 * supported=true 时原样返回（只做克隆，不改调用方数组）。
 * 理由：无来源可选的选单按钮过去只会弹一句「暂未实现」，既误导也不可执行。
 */
export function ensureSelectAction(rawGroups, supported) {
  const groups = cloneGroups(rawGroups)
  if (supported) return groups
  const kept = groups
    .filter((group) => group.name !== SELECT_GROUP_NAME)
    .map((group) => ({ ...group, actions: group.actions.filter((action) => action !== SELECT_ACTION) }))
  return kept.filter((group) => group.actions.length > 0)
}

/**
 * 面板是否提供「审核」动作：表单页底部按钮或面板按钮清单里出现「审核」。
 * 报表、只读面板一律不支持（对齐后端：报表无单据状态流转）。
 */
export function supportsAudit(metadata) {
  if (!metadata || typeof metadata !== 'object') return false
  if (enabledFlag(metadata.readonly) || enabledFlag(metadata.readOnly)) return false
  if (String(metadata.panelCategory ?? '').trim() === '报表') return false

  const hasAudit = (list) => (Array.isArray(list) ? list : []).some((item) => {
    const name = typeof item === 'string' ? item : item?.buttonName
    return String(name ?? '').trim() === AUDIT_ACTION
  })

  if (hasAudit(metadata.panelButtons)) return true
  const formPages = metadata?.panelPageDto?.formPages
  return Array.isArray(formPages) && formPages.some((page) => hasAudit(page?.bottomOperationBarBtn))
}

/**
 * 审核入口：「审核」并入「审批」组并放在首位 —— 列表页此前只有审批组（审批通过/驳回/弃审），
 * 草稿态的主按钮被置灰且点了没有反应，用户找不到审核入口；「审核」只存在于表单页底部。
 * 主干流程（报价单→销售订单→出库单→发票）要求「已审核」才能生单，因此列表页必须能审核。
 * supported=false（报表/只读/面板未配置审核）时确保该动作不出现，与原行为一致。
 */
export function ensureAuditAction(rawGroups, supported) {
  const groups = cloneGroups(rawGroups)
  if (!supported) {
    for (const group of groups) {
      group.actions = group.actions.filter((action) => action !== AUDIT_ACTION)
    }
    return groups
  }
  // 面板自带「审核」组（销售出库单这类直接审核面板）：审核在列表页已经可见，
  // 不能再往「审批」组里复制一份，否则会渲染出两个「审核」入口。
  if (groups.some((group) => group.name === AUDIT_ACTION && group.actions.includes(AUDIT_ACTION))) return groups

  for (const group of groups) {
    group.actions = group.actions.filter((action) => action !== AUDIT_ACTION)
  }

  const approvalGroup = groups.find((group) => group.name === APPROVAL_GROUP_NAME)
  if (approvalGroup) approvalGroup.actions = [AUDIT_ACTION, ...approvalGroup.actions]
  else groups.push({ name: APPROVAL_GROUP_NAME, actions: [AUDIT_ACTION] })
  return groups
}
