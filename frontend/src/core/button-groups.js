const SCAN_FILL_ACTION = '扫描填单'
const COLUMN_PREFS_ACTION = '表格调整'

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
