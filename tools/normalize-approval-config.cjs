const fs = require('node:fs')
const path = require('node:path')
const { execFileSync } = require('node:child_process')

const APPLY = process.argv.includes('--apply')
const ROOT = path.resolve(__dirname, '..')
const INIT_SQL = path.join(ROOT, 'backend', 'src', 'main', 'resources', 'db', 'init.sql')
const U = {
  audit: '\u5ba1\u6838',
  approval: '\u5ba1\u6279',
  submit: '\u63d0\u4ea4\u5ba1\u6279',
  approve: '\u5ba1\u6279\u901a\u8fc7',
  reject: '\u5ba1\u6279\u9a73\u56de',
  rejectLegacy: '\u9a73\u56de\u5ba1\u6279',
  history: '\u5ba1\u6279\u60c5\u51b5',
  unaudit: '\u5f03\u5ba1',
}
const ACTIONS = [U.submit, U.approve, U.reject, U.history, U.unaudit]
const APPROVAL_PANELS = new Set([
  'SO_ORDER', 'PURCHASE_IN', 'FINISH_IN', 'OTHER_IN', 'SALE_OUT', 'MATERIAL_OUT', 'OTHER_OUT',
  'MANU_ORDER', 'PROCESS_REPORT', 'INIT_AP', 'INIT_AR', 'INIT_BALANCE', 'BOM', 'ROUTE', 'PU_REQ',
  'ARRIVAL_IN', 'DISPATCH', 'FINISH_INSPECT', 'INSPECTION', 'MATERIAL_REQ', 'PICK_ORDER',
  'PU_IN', 'PU_ORDER', 'SALE_INV', 'TRANSFER', 'OUTSOURCE_ORDER', 'OUTSOURCE_ISSUE', 'OUTSOURCE_IN',
  'OUTSOURCE_FEE', 'QUOTE_ORDER', 'SALE_INVOICE', 'EXPENSE', 'SALE_COST_ALLOC', 'PU_INVOICE',
  'PU_COST_ALLOC', 'STOCK_CHECK', 'LOCATION_ADJUST', 'SERIAL_NO',
])

function normalizeGroups(panelCode, groups) {
  if (!Array.isArray(groups)) return { groups, changed: false }
  const hasWorkflow = groups.some((group) => (group.actions || group.items || []).includes(U.submit))
  if (!APPROVAL_PANELS.has(panelCode) && !hasWorkflow) return { groups, changed: false }

  const result = []
  let template = null
  let insertAt = -1
  for (const group of groups) {
    const actions = group.actions || group.items || []
    const workflowGroup = actions.includes(U.submit)
      || ([U.audit, U.approval, U.history, U.unaudit].includes(group.name)
        && actions.some((action) => [U.audit, ...ACTIONS, U.rejectLegacy].includes(action)))
    if (workflowGroup) {
      if (insertAt < 0) insertAt = result.length
      template ||= group
      continue
    }
    result.push({
      ...group,
      actions: [...new Set(actions.map((action) => action === U.rejectLegacy ? U.reject : action))],
    })
  }
  result.splice(insertAt < 0 ? result.length : insertAt, 0, {
    ...(template || {}),
    name: U.approval,
    actions: ACTIONS,
  })
  return { groups: result, changed: JSON.stringify(result) !== JSON.stringify(groups) }
}

function normalizeConfig(panelCode, config) {
  const current = config?.metadata?.buttonGroups
  const workflow = APPROVAL_PANELS.has(panelCode)
    || (Array.isArray(current) && current.some((group) => (group.actions || group.items || []).includes(U.submit)))
  if (!workflow) return false
  const normalized = normalizeGroups(panelCode, current)
  let changed = normalized.changed
  if (normalized.changed) config.metadata.buttonGroups = normalized.groups
  if (Array.isArray(config.metadata.panelButtons)) {
    const before = JSON.stringify(config.metadata.panelButtons)
    config.metadata.panelButtons = config.metadata.panelButtons.filter((button, index, buttons) => (
      ![U.audit, U.rejectLegacy].includes(button.buttonName)
      && buttons.findIndex((candidate) => candidate.buttonName === button.buttonName) === index
    ))
    const names = config.metadata.panelButtons.map((button) => button.buttonName)
    for (const action of ACTIONS) {
      if (!names.includes(action)) config.metadata.panelButtons.push({ buttonName: action })
    }
    changed ||= before !== JSON.stringify(config.metadata.panelButtons)
  }
  return changed
}

function mysql(args, options = {}) {
  return execFileSync('mysql', [
    '--default-character-set=utf8mb4',
    '--ssl-mode=DISABLED',
    '--host=localhost',
    '--port=3306',
    '--user=root',
    '--skip-column-names',
    '--batch',
    ...args,
  ], { encoding: 'utf8', ...options })
}

function normalizeDatabase() {
  const output = mysql(['--execute', 'SELECT panel_code, config FROM light_mes.panel_config ORDER BY panel_code'])
  const updates = []
  const changed = []
  for (const line of output.split(/\r?\n/)) {
    if (!line) continue
    const separator = line.indexOf('\t')
    if (separator < 0) continue
    const panelCode = line.slice(0, separator)
    const config = JSON.parse(line.slice(separator + 1))
    if (!normalizeConfig(panelCode, config)) continue
    changed.push(panelCode)
    const encoded = JSON.stringify(config).replaceAll("'", "''")
    updates.push(`UPDATE light_mes.panel_config SET config='${encoded}' WHERE panel_code='${panelCode}';`)
  }
  if (APPLY && updates.length) mysql([], { input: `SET NAMES utf8mb4;\n${updates.join('\n')}\n` })
  return changed
}

function normalizeSeed() {
  const source = fs.readFileSync(INIT_SQL, 'utf8')
  const changed = []
  let touched = false

  function replaceConfig(line, prefix, panelCode, encodedConfig, suffix, carriageReturn) {
    let config
    try {
      config = JSON.parse(encodedConfig.replaceAll("''", "'"))
    } catch (error) {
      throw new Error(`cannot parse panel config in init.sql: ${panelCode}: ${error.message}`)
    }
    if (!normalizeConfig(panelCode, config)) {
      if (APPLY && APPROVAL_PANELS.has(panelCode) && carriageReturn) {
        touched = true
        return line.slice(0, -1)
      }
      return line
    }
    changed.push(panelCode)
    touched = true
    return prefix + JSON.stringify(config).replaceAll("'", "''") + suffix
  }

  const standalonePattern = /^(INSERT INTO panel_config \(panel_code, panel_name, category, config\) VALUES \('([^']+)', '[^']*', '[^']*', ')(.*)('\) ON DUPLICATE KEY UPDATE config = VALUES\(config\);)(\r?)$/gm
  const tuplePattern = /^(\('([^']+)', '[^']*', '[^']*', ')(\{.*\})('\)(?:,|;)?)(\r?)$/gm
  const splitTuplePattern = /^(\('([^']+)', '[^']*', '[^']*',\r?\n')(\{.*\})('\)(?:,)?)(\r?)$/gm
  let next = source.replace(standalonePattern, replaceConfig)
  next = next.replace(tuplePattern, replaceConfig)
  next = next.replace(splitTuplePattern, replaceConfig)
  if (APPLY && touched) fs.writeFileSync(INIT_SQL, next, 'utf8')
  return changed
}

const database = normalizeDatabase()
const seed = normalizeSeed()
console.log(JSON.stringify({ mode: APPLY ? 'apply' : 'check', database, seed }, null, 2))
if (!APPLY && (database.length || seed.length)) process.exitCode = 2
