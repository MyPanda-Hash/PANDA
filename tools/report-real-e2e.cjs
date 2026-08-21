// report-real-e2e.cjs: browser baseline for the 28 real-backend report panels.
const { spawn } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')

const PORT = 9225
const BASE = 'http://127.0.0.1:4173'
const API_BASE = 'http://127.0.0.1:8080'
const OUT = 'F:/INCER/light-mes/docs/ref/mes-live'
const PROFILE = path.join(process.env.TEMP || 'C:/Windows/Temp', 'light-mes-report-e2e')
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

const PANELS = [
  'PURCHASE_IN_DETAIL', 'FINISH_IN_DETAIL', 'OTHER_IN_DETAIL', 'SALE_OUT_DETAIL', 'MATERIAL_OUT_DETAIL', 'OTHER_OUT_DETAIL',
  'PURCHASE_IN_STATS', 'FINISH_IN_STATS', 'OTHER_IN_STATS', 'SALE_OUT_STATS', 'MATERIAL_OUT_STATS', 'OTHER_OUT_STATS',
  'COST_MAINTAIN', 'STOCK_STATUS', 'STOCK_SUMMARY', 'STOCK_LEDGER',
  'SALES_ORDER_DETAIL', 'SALES_ORDER_STATS', 'SALES_ORDER_EXEC', 'SALES_ORDER_PROGRESS',
  'MANU_ORDER_DETAIL', 'MANU_ORDER_STATS', 'MANU_PROC_STATS', 'PROC_DETAIL', 'PROC_STATS',
  'SALARY_DETAIL', 'SALARY_STATS', 'REWORK_REPORT',
]

const SCREENSHOTS = new Set(['STOCK_SUMMARY', 'SALES_ORDER_EXEC', 'MANU_ORDER_STATS', 'SALARY_STATS'])

function edgePath() {
  return [
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  ].find(fs.existsSync)
}

async function ensureBrowser() {
  try {
    const response = await fetch(`http://127.0.0.1:${PORT}/json/version`)
    if (response.ok) return
  } catch (_) {}
  const edge = edgePath()
  if (!edge) throw new Error('Microsoft Edge not found')
  fs.mkdirSync(PROFILE, { recursive: true })
  const child = spawn(edge, [
    '--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=${PROFILE}`,
    '--no-first-run', '--no-default-browser-check', '--disable-gpu',
    '--window-size=1600,1000', '--force-device-scale-factor=1', 'about:blank',
  ], { detached: true, stdio: 'ignore' })
  child.unref()
  for (let i = 0; i < 40; i++) {
    try {
      const response = await fetch(`http://127.0.0.1:${PORT}/json/version`)
      if (response.ok) return
    } catch (_) {}
    await sleep(500)
  }
  throw new Error('Edge CDP startup timeout')
}

async function connect() {
  const targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()
  const pages = targets.filter((target) => target.type === 'page' && !target.url.startsWith('edge://'))
  const page = pages.find((target) => target.url.startsWith(BASE)) || pages.find((target) => target.url === 'about:blank')
  if (!page) throw new Error('No page target')
  const ws = new WebSocket(page.webSocketDebuggerUrl)
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject })
  let id = 0
  const pending = new Map()
  const errors = []
  ws.onmessage = (event) => {
    const message = JSON.parse(event.data)
    if (message.id && pending.has(message.id)) {
      const waiter = pending.get(message.id)
      pending.delete(message.id)
      message.error ? waiter.reject(new Error(message.error.message)) : waiter.resolve(message.result)
    } else if (message.method === 'Runtime.exceptionThrown') {
      const detail = message.params.exceptionDetails
      errors.push((detail.exception?.description || detail.text || '').slice(0, 300))
    }
  }
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const messageId = ++id
    pending.set(messageId, { resolve, reject })
    ws.send(JSON.stringify({ id: messageId, method, params }))
  })
  const evaluate = async (expression) => {
    const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true, timeout: 30000 })
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails).slice(0, 500))
    return result.result.value
  }
  return { ws, send, evaluate, errors }
}

async function waitFor(cdp, expression, timeout = 30000) {
  const start = Date.now()
  while (Date.now() - start < timeout) {
    if (await cdp.evaluate(expression)) return true
    await sleep(500)
  }
  return false
}

async function login(cdp) {
  const response = await fetch(`${API_BASE}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ userName: 'admin', password: '123456' }),
  })
  const payload = await response.json()
  if (!response.ok || payload.code !== 200 || !payload.data?.token) throw new Error('Local MES login API failed')
  await cdp.send('Page.navigate', { url: `${BASE}/#/login?e2e=${Date.now()}` })
  await waitFor(cdp, `location.origin === ${JSON.stringify(BASE)}`)
  await cdp.evaluate(`(() => {
    localStorage.setItem('mes_token', ${JSON.stringify(payload.data.token)})
    localStorage.setItem('mes_user', ${JSON.stringify(JSON.stringify(payload.data.user))})
    localStorage.setItem('mes_login_date', new Date().toISOString().slice(0, 10))
    localStorage.setItem('mes_init_done', '1')
    return true
  })()`)
  await cdp.send('Page.reload', { ignoreCache: true })
  await waitFor(cdp, `document.readyState === 'complete' && !!localStorage.getItem('mes_token')`, 15000)
  await cdp.send('Page.navigate', { url: `${BASE}/?e2e=${Date.now()}#/dashboard` })
  const loggedIn = await waitFor(cdp, `location.hash.includes('/dashboard') && (document.body.innerText || '').includes('\u6211\u7684\u684c\u9762')`, 20000)
  if (!loggedIn) {
    const state = await cdp.evaluate(`JSON.stringify({ href: location.href, token: !!localStorage.getItem('mes_token'), text: (document.body.innerText || '').slice(0, 160) })`)
    throw new Error('Local MES login failed: ' + state)
  }
  await cdp.evaluate(`document.querySelector('.wz-close')?.click(); true`)
  const unobstructed = await waitFor(cdp, `!document.querySelector('.wizard-mask')`, 5000)
  if (!unobstructed) throw new Error('Initialization wizard obstructs the report workspace')
  return payload.data.token
}

async function writeApiBaseline(token) {
  const headers = { Authorization: `Bearer ${token}` }
  const results = []
  for (const code of PANELS) {
    const configResponse = await fetch(`${API_BASE}/api/px/getPanelConfig?panelCode=${code}`, { headers })
    const queryResponse = await fetch(`${API_BASE}/api/px/queryFormDataList`, {
      method: 'POST',
      headers: { ...headers, 'Content-Type': 'application/json' },
      body: JSON.stringify({ panelCode: code, condition: {}, pageNo: 1, pageSize: 50 }),
    })
    const config = await configResponse.json()
    const query = await queryResponse.json()
    if (!configResponse.ok || config.code !== 200 || !queryResponse.ok || query.code !== 200) {
      throw new Error(`Report API failed: ${code} config=${config.code} query=${query.code}`)
    }
    const metadata = config.data?.metadata || {}
    const fields = config.data?.dataSchema?.fields || []
    const list = query.data?.list || []
    const gridTab = metadata.panelPageDto?.tablePages?.[0]?.gridTabs?.[0]
    const queryFields = metadata.panelPageDto?.tablePages?.[0]?.queryFields || []
    results.push({
      code,
      name: metadata.panelName || '',
      configCode: config.code,
      queryCode: query.code,
      columns: fields.length,
      total: query.data?.totalSize ?? 0,
      returned: list.length,
      firstKeys: Object.keys(list[0] || {}).slice(0, 5),
      columnGroups: (gridTab?.columnGroups || []).map((group) => group.label),
      numericFields: fields.filter((field) => field.dataType === '小数').map((field) => field.dataName),
      queryReferences: queryFields.filter((field) => field.dataType === '参照').map((field) => ({
        dataName: field.dataName,
        refPanel: field.refPanel,
        refField: field.refField,
        filter: field.filter,
      })),
    })
  }
  fs.writeFileSync(path.join(OUT, 'report-real-api-20260821.json'), JSON.stringify(results, null, 2), 'utf8')
}

async function screenshot(cdp, code) {
  const image = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false })
  fs.writeFileSync(path.join(OUT, `report-real-${code}.png`), Buffer.from(image.data, 'base64'))
}

async function verifyReference(cdp, test) {
  await cdp.send('Page.navigate', { url: `${BASE}/?refE2e=${Date.now()}#/panelx/list/${test.panel}` })
  const ready = await waitFor(cdp, `!!document.querySelector('.report-heading strong')
    && document.querySelectorAll('.report-table .el-table__body-wrapper tbody tr').length > 0
    && !document.querySelector('.el-dialog')
    && ![...document.querySelectorAll('.el-loading-mask')].some((item) => item.offsetParent !== null)`, 40000)
  if (!ready) throw new Error(`Report not ready for reference test: ${test.panel}`)
  const before = await cdp.evaluate(`document.querySelectorAll('.report-table .el-table__body-wrapper tbody tr').length`)
  const opened = await cdp.evaluate(`(() => {
    const field = [...document.querySelectorAll('.fields .field')].find((item) => item.querySelector('label')?.innerText.trim() === ${JSON.stringify(test.field)})
    field?.querySelector('.query-ref .el-button')?.click()
    return !!field
  })()`)
  if (!opened || !(await waitFor(cdp, `!!document.querySelector('.el-dialog .rpd')`, 20000))) {
    throw new Error(`Reference dialog did not open: ${test.panel}/${test.field}`)
  }
  const loaded = await waitFor(cdp, `(() => {
    const root = document.querySelector('.el-dialog')
    return root?.querySelector('.el-dialog__title')?.innerText.includes(${JSON.stringify(test.title)})
      && root.querySelectorAll('.el-table__header-wrapper th').length > 0
      && root.querySelectorAll('.el-table__body-wrapper tbody tr').length > 0
      && ![...root.querySelectorAll('.el-loading-mask')].some((item) => item.offsetParent !== null)
  })()`, 30000)
  if (!loaded) throw new Error(`Reference data did not load: ${test.panel}/${test.field}`)
  const dialog = JSON.parse(await cdp.evaluate(`(() => {
    const root = document.querySelector('.el-dialog')
    const headers = [...root.querySelectorAll('.el-table__header-wrapper th')].map((item) => item.innerText.trim()).filter(Boolean)
    const rows = [...root.querySelectorAll('.el-table__body-wrapper tbody tr')].map((tr) => [...tr.querySelectorAll('td')].map((td) => td.innerText.trim()))
    return JSON.stringify({ title: root.querySelector('.el-dialog__title')?.innerText || '', headers, rows })
  })()`))
  const flattened = dialog.rows.flat().join('|')
  if (!dialog.title.includes(test.title) || !dialog.headers.includes(test.column) || !flattened.includes(test.value)) {
    throw new Error(`Reference data mismatch: ${test.panel}/${test.field} ${JSON.stringify(dialog)}`)
  }
  for (const excluded of test.excludes || []) {
    if (flattened.includes(excluded)) throw new Error(`Reference filter leaked ${excluded}: ${test.panel}/${test.field}`)
  }
  const selected = await cdp.evaluate(`(() => {
    const root = document.querySelector('.el-dialog')
    const row = [...root.querySelectorAll('.el-table__body-wrapper tbody tr')].find((tr) => tr.innerText.includes(${JSON.stringify(test.value)}))
    row?.querySelector('.el-checkbox__input')?.click()
    return !!row
  })()`)
  if (!selected) throw new Error(`Reference row not selectable: ${test.panel}/${test.field}/${test.value}`)
  const confirmed = await waitFor(cdp, `!document.querySelector('.el-dialog button.is-disabled')`, 5000)
  if (!confirmed) throw new Error(`Reference confirm remained disabled: ${test.panel}/${test.field}`)
  await cdp.evaluate(`(() => {
    const root = document.querySelector('.el-dialog')
    const button = [...root.querySelectorAll('.el-dialog__footer button')].find((item) => item.innerText.includes('\u786e\u5b9a'))
    button?.click()
    return !!button
  })()`)
  const applied = await waitFor(cdp, `(() => {
    if (document.querySelector('.el-dialog')) return false
    const field = [...document.querySelectorAll('.fields .field')].find((item) => item.querySelector('label')?.innerText.trim() === ${JSON.stringify(test.field)})
    return field?.querySelector('input')?.value === ${JSON.stringify(test.value)} && ![...document.querySelectorAll('.el-loading-mask')].some((item) => item.offsetParent !== null)
  })()`, 30000)
  if (!applied) throw new Error(`Reference value was not applied: ${test.panel}/${test.field}/${test.value}`)
  const after = await cdp.evaluate(`document.querySelectorAll('.report-table .el-table__body-wrapper tbody tr').length`)
  if (test.narrows && !(after < before)) throw new Error(`Reference did not narrow rows: ${test.panel}/${test.field} ${before}->${after}`)
  return { ...test, title: dialog.title, headers: dialog.headers, rowCount: dialog.rows.length, before, after }
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true })
  await ensureBrowser()
  const cdp = await connect()
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false })
  const token = await login(cdp)
  await writeApiBaseline(token)

  const results = []
  for (const code of PANELS) {
    cdp.errors.length = 0
    await cdp.send('Page.navigate', { url: `${BASE}/#/panelx/list/${code}?e2e=${Date.now()}` })
    const ready = await waitFor(cdp, `!!document.querySelector('.report-table') && !document.querySelector('.wizard-mask') && ![...document.querySelectorAll('.el-loading-mask')].some((item) => item.offsetParent !== null)`, 40000)
    await sleep(800)
    const state = JSON.parse(await cdp.evaluate(`(() => {
      const body = document.body.innerText || ''
      const toolbar = [...document.querySelectorAll('.tools .act-name')].map((item) => item.innerText.trim()).filter(Boolean)
      const queryLabels = [...document.querySelectorAll('.fields .field label')].map((item) => item.innerText.trim()).filter(Boolean)
      const allHeaders = [...document.querySelectorAll('.report-table .el-table__header-wrapper th')].map((item) => item.innerText.trim()).filter(Boolean)
      const leafHeaders = [...document.querySelectorAll('.report-table .el-table__header-wrapper tr:last-child th')].map((item) => item.innerText.trim()).filter(Boolean)
      const rows = [...document.querySelectorAll('.report-table .el-table__body-wrapper tbody tr')]
      const first = rows[0] ? [...rows[0].querySelectorAll('td')].map((cell) => cell.innerText.trim()).slice(0, 10) : []
      const footer = [...document.querySelectorAll('.report-table .el-table__footer-wrapper td')].map((cell) => cell.innerText.trim()).slice(0, 10)
      const wizardVisible = !!document.querySelector('.wizard-mask')
      return JSON.stringify({
        title: document.title,
        heading: document.querySelector('.report-heading strong')?.innerText || '',
        period: document.querySelector('.report-heading span')?.innerText || '',
        toolbar,
        queryLabels,
        allHeaders,
        leafHeaders,
        rowCount: rows.length,
        first,
        footer,
        wizardVisible,
        errorText: body.includes('\u52a0\u8f7d\u5931\u8d25') || body.includes('\u9762\u677f\u4e0d\u5b58\u5728') || body.includes('404'),
      })
    })()`))
    const result = { code, ready, ...state, exceptions: [...cdp.errors] }
    results.push(result)
    if (SCREENSHOTS.has(code)) await screenshot(cdp, code)
    const ok = ready && state.heading && !state.wizardVisible && !state.errorText && cdp.errors.length === 0
    console.log(`[${ok ? 'OK' : 'FAIL'}] ${code} rows=${state.rowCount} headers=${state.leafHeaders.length} query=${state.queryLabels.length}`)
  }

  const referenceTests = []
  const cases = [
    { panel: 'STOCK_SUMMARY', field: '仓库', title: '仓库', column: '仓库名称', value: '原料仓', narrows: true },
    { panel: 'STOCK_SUMMARY', field: '存货', title: '存货', column: '存货名称', value: '铝棒 Φ80', narrows: true },
    { panel: 'SALES_ORDER_DETAIL', field: '客户', title: '往来单位', column: '性质', value: '华东铝业', excludes: ['华东热处理厂'], narrows: true },
    { panel: 'PURCHASE_IN_DETAIL', field: '供应商', title: '往来单位', column: '性质', value: '华东热处理厂', excludes: ['华东铝业'] },
    { panel: 'SALARY_DETAIL', field: '工人名称', title: '员工', column: '员工名称', value: '王强', narrows: true },
  ]
  for (const test of cases) {
    cdp.errors.length = 0
    const result = await verifyReference(cdp, test)
    result.exceptions = [...cdp.errors]
    referenceTests.push(result)
    console.log(`[OK] REF ${test.panel}/${test.field} rows=${result.rowCount} report=${result.before}->${result.after}`)
  }

  fs.writeFileSync(path.join(OUT, 'report-real-ui-20260821.json'), JSON.stringify(results, null, 2), 'utf8')
  fs.writeFileSync(path.join(OUT, 'report-real-ref-ui-20260821.json'), JSON.stringify(referenceTests, null, 2), 'utf8')
  cdp.ws.close()
  const failed = results.filter((item) => !item.ready || !item.heading || item.wizardVisible || item.errorText || item.exceptions.length)
  const referenceFailed = referenceTests.filter((item) => item.exceptions.length)
  console.log(`[done] panels=${results.length} failed=${failed.length} references=${referenceTests.length} refFailed=${referenceFailed.length}`)
  if (failed.length || referenceFailed.length) process.exitCode = 2
}

main().catch((error) => { console.error('[FAILED]', error.stack || error.message); process.exit(1) })