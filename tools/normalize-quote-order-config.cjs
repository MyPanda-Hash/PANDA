const fs = require('node:fs')
const path = require('node:path')
const { execFileSync } = require('node:child_process')

const APPLY = process.argv.includes('--apply')
const ROOT = path.resolve(__dirname, '..')
const SALES_SQL = path.join(__dirname, 'update-sales-chain.sql')
const INIT_SQL = path.join(ROOT, 'backend', 'src', 'main', 'resources', 'db', 'init.sql')
const APPROVAL_ACTIONS = ['提交审批', '审批通过', '审批驳回', '审批情况', '弃审']

function mysql(args, options = {}) {
  return execFileSync('mysql', [
    '--default-character-set=utf8mb4', '--ssl-mode=DISABLED', '--host=localhost', '--port=3306',
    '--user=root', '--database=light_mes', '--skip-column-names', '--batch', ...args,
  ], { encoding: 'utf8', ...options })
}

function extractSourceConfig(source) {
  const match = source.match(/\('QUOTE_ORDER', '报价单', '单据', '(\{.*?\})', '1\.0', '[^']+', '[^']+'\)/s)
  if (!match) throw new Error('update-sales-chain.sql 中未找到 QUOTE_ORDER 配置')
  return JSON.parse(match[1].replaceAll('\\"', '"'))
}

function normalize(config) {
  const metadata = config.metadata
  const queryFields = metadata?.panelPageDto?.tablePages?.[0]?.queryFields || []
  const customerQuery = queryFields.find((field) => field.refPanel === 'PARTNER' && !field.dataName)
  if (customerQuery) customerQuery.dataName = '客户'

  const groups = metadata.buttonGroups || []
  const approvalAt = groups.findIndex((group) => ['审核', '审批'].includes(group.name)
    || (group.actions || []).some((action) => APPROVAL_ACTIONS.includes(action) || action === '审核'))
  metadata.buttonGroups = groups.filter((group) => !(['审核', '审批', '审批情况', '弃审'].includes(group.name)
    || (group.actions || []).some((action) => APPROVAL_ACTIONS.includes(action) || action === '审核')))
  metadata.buttonGroups.splice(approvalAt < 0 ? metadata.buttonGroups.length : approvalAt, 0, {
    name: '审批', actions: APPROVAL_ACTIONS,
  })
  const buttons = (metadata.panelButtons || []).filter((button) => !['审核', '驳回审批'].includes(button.buttonName))
  const buttonNames = new Set(buttons.map((button) => button.buttonName))
  for (const action of APPROVAL_ACTIONS) if (!buttonNames.has(action)) buttons.push({ buttonName: action })
  metadata.panelButtons = buttons

  const tab = config.detail.tabs.find((item) => item.key === 'items')
  tab.calc = [
    { target: '含税单价', formula: '报价单价 * (1 + 税率% / 100)', round: 2 },
    { target: '金额', formula: '数量 * 报价单价', round: 2 },
    { target: '含税金额', formula: '数量 * 含税单价', round: 2 },
  ]
  tab.summaryItems = [
    { label: '数量合计', field: '数量' },
    { label: '金额合计', field: '金额' },
    { label: '含税金额合计', field: '含税金额' },
  ]
  for (const field of tab.fields) {
    if (['含税单价', '金额', '含税金额', '现存量'].includes(field.dataName)) {
      field.computed = true
      delete field.defaultValue
    }
    if (field.dataName === '存货编码') {
      field.refMap = [
        { from: '存货名称', to: '存货名称' }, { from: '规格型号', to: '规格型号' },
        { from: '计量单位', to: '销售单位' },
      ]
    }
    if (field.dataName === '存货名称') {
      field.refMap = [
        { from: '存货编码', to: '存货编码' }, { from: '规格型号', to: '规格型号' },
        { from: '计量单位', to: '销售单位' },
      ]
    }
  }
  return config
}

function encodeSales(config) {
  return JSON.stringify(config).replaceAll('"', '\\"')
}

function seedStatement(config) {
  const encoded = JSON.stringify(config).replaceAll("'", "''")
  return `INSERT INTO panel_config (panel_code, panel_name, category, config) VALUES ('QUOTE_ORDER', '报价单', '单据', '${encoded}') ON DUPLICATE KEY UPDATE panel_name=VALUES(panel_name), category=VALUES(category), config=VALUES(config);`
}

const salesSource = fs.readFileSync(SALES_SQL, 'utf8')
const config = normalize(extractSourceConfig(salesSource))
const nextSales = salesSource.replace(
  /(\('QUOTE_ORDER', '报价单', '单据', ')(\{.*?\})(', '1\.0', '[^']+', '[^']+'\))/s,
  (_, prefix, _encoded, suffix) => prefix + encodeSales(config) + suffix,
)

let initSource = fs.readFileSync(INIT_SQL, 'utf8')
const statement = seedStatement(config)
const initPattern = /^INSERT INTO panel_config .*VALUES \('QUOTE_ORDER', '报价单', '单据', '.*'\) ON DUPLICATE KEY UPDATE .*;\r?$/m
const nextInit = initPattern.test(initSource)
  ? initSource.replace(initPattern, statement)
  : initSource.replace(/\s*$/, `\n\n-- 报价单正式种子：金额链与库存状况表联动（2026-08-26）\n${statement}\n`)

let databaseDifferent = true
try {
  const raw = mysql(['--execute', "SELECT config FROM light_mes.panel_config WHERE panel_code='QUOTE_ORDER'"]).trim()
  databaseDifferent = !raw || JSON.stringify(JSON.parse(raw)) !== JSON.stringify(config)
} catch (error) {
  databaseDifferent = true
}

const changes = {
  salesSeed: nextSales !== salesSource,
  initSeed: nextInit !== initSource,
  database: databaseDifferent,
}
if (APPLY) {
  if (changes.salesSeed) fs.writeFileSync(SALES_SQL, nextSales, 'utf8')
  if (changes.initSeed) fs.writeFileSync(INIT_SQL, nextInit, 'utf8')
  if (changes.database) mysql([], { input: `SET NAMES utf8mb4;\n${statement}\n` })
}
console.log(JSON.stringify({ mode: APPLY ? 'apply' : 'check', changes }, null, 2))
if (!APPLY && Object.values(changes).some(Boolean)) process.exitCode = 2
