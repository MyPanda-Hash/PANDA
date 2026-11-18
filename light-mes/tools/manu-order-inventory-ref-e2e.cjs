// List-page inventory references for production and sales orders.
const { spawn } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')

const PORT = 9225
const BASE = 'http://127.0.0.1:4173'
const API_BASE = 'http://127.0.0.1:3308'
const PROFILE = path.join(process.env.TEMP || 'C:/Windows/Temp', 'light-mes-order-ref-e2e')
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

const PANELS = [
  { code: 'MANU_ORDER', tabKey: 'products', codeField: '产品编码', nameField: '产品名称' },
  { code: 'SO_ORDER', tabKey: 'items', codeField: '存货编码', nameField: '存货名称' },
]

function edgePath() {
  return [
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  ].find(fs.existsSync)
}

async function ensureBrowser() {
  try { if ((await fetch(`http://127.0.0.1:${PORT}/json/version`)).ok) return } catch (_) {}
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
    try { if ((await fetch(`http://127.0.0.1:${PORT}/json/version`)).ok) return } catch (_) {}
    await sleep(500)
  }
  throw new Error('Edge CDP startup timeout')
}

async function connect() {
  const targets = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()
  const page = targets.find((target) => target.type === 'page' && !target.url.startsWith('edge://'))
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
      errors.push(message.params.exceptionDetails.exception?.description || message.params.exceptionDetails.text)
    }
  }
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const messageId = ++id
    pending.set(messageId, { resolve, reject })
    ws.send(JSON.stringify({ id: messageId, method, params }))
  })
  const evaluate = async (expression) => {
    const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text)
    return result.result.value
  }
  await send('Page.enable')
  await send('Runtime.enable')
  return { ws, send, evaluate, errors }
}

async function waitFor(cdp, expression, timeout = 20000) {
  const start = Date.now()
  while (Date.now() - start < timeout) {
    if (await cdp.evaluate(expression)) return true
    await sleep(400)
  }
  return false
}

async function api(token, url, body) {
  const response = await fetch(`${API_BASE}${url}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { Authorization: `Bearer ${token}`, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const payload = await response.json()
  if (!response.ok || payload.code !== 200) throw new Error(`${url}: ${payload.message || response.status}`)
  return payload.data
}

async function login(cdp) {
  const response = await fetch(`${API_BASE}/api/auth/login`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ userName: 'admin', password: '123456' }),
  })
  const payload = await response.json()
  if (!payload.data?.token) throw new Error('Login failed')
  await cdp.send('Page.navigate', { url: `${BASE}/#/login?e2e=${Date.now()}` })
  await waitFor(cdp, `location.origin === ${JSON.stringify(BASE)}`)
  await cdp.evaluate(`(() => {
    localStorage.setItem('mes_token', ${JSON.stringify(payload.data.token)})
    localStorage.setItem('mes_user', ${JSON.stringify(JSON.stringify(payload.data.user))})
    localStorage.setItem('mes_login_date', new Date().toISOString().slice(0, 10))
    localStorage.setItem('mes_init_done', '1')
    return true
  })()`)
  return payload.data.token
}

async function queryPanel(token, code) {
  return api(token, '/api/px/queryFormDataList', { panelCode: code, condition: {}, pageNo: 1, pageSize: 100 })
}

function formDataOf(order) {
  const head = JSON.parse(JSON.stringify(order))
  const detail = head.detail || {}
  delete head.detail
  delete head['编号']
  delete head['单据状态']
  delete head['创建时间']
  delete head['更新时间']
  delete head['发起人编号']
  return { ...head, 编号: order['编号'], detail }
}

async function saveOrder(token, code, order) {
  return api(token, '/api/px/callButton', {
    panelCode: code, buttonName: '提交', formData: formDataOf(order), buttonParam: {},
  })
}

async function createSalesDraft(token) {
  const created = await api(token, '/api/px/callButton', {
    panelCode: 'SO_ORDER', buttonName: '提交', buttonParam: {},
    formData: {
      单据日期: '2026-08-21', 客户: 'E2E临时客户', 结算客户: 'E2E临时客户',
      detail: { items: [{ 存货编码: 'CP003', 存货名称: '铝型材-散热片', 规格型号: 'XD-6063-T5', 数量: 2, 销售单位: '件', 单价: 22.6, '税率%': 13 }] },
    },
  })
  return created['编号']
}

async function openList(cdp, panel) {
  const prefix = panel.code === 'SO_ORDER' ? 'SO-' : 'MO-'
  await cdp.send('Page.navigate', { url: `${BASE}/?e2e=${Date.now()}#/panelx/list/${panel.code}` })
  const ready = await waitFor(cdp, `(() => {
    if (!location.hash.includes('/panelx/list/${panel.code}')) return false
    if (!(document.querySelector('.doc-chip')?.innerText || '').includes(${JSON.stringify(prefix)})) return false
    const headers = [...document.querySelectorAll('.detail .el-table__header th')].map((th) => th.innerText.trim())
    return headers.includes(${JSON.stringify(panel.codeField)}) && !!document.querySelector('.toolbar-query-btn')
  })()`, 25000)
  if (!ready) throw new Error(`${panel.code} list did not load`)
}

async function gotoOrder(cdp, documentNo) {
  await cdp.evaluate(`document.querySelector('.page-btn[title="首页"]')?.click(); true`)
  await sleep(300)
  for (let i = 0; i < 120; i++) {
    const current = await cdp.evaluate(`document.querySelector('.doc-chip')?.innerText || ''`)
    if (current.includes(documentNo)) return
    const before = current
    await cdp.evaluate(`document.querySelector('.page-btn[title="下一张"]')?.click(); true`)
    if (!await waitFor(cdp, `(document.querySelector('.doc-chip')?.innerText || '') !== ${JSON.stringify(before)}`, 5000)) break
  }
  throw new Error(`Could not navigate to ${documentNo}`)
}

async function selectTwo(cdp) {
  if (!await waitFor(cdp, `!!document.querySelector('.el-dialog:has(.rpd) .el-table__body tbody tr')`)) {
    throw new Error('Inventory reference dialog did not open')
  }
  const expected = JSON.parse(await cdp.evaluate(`(() => {
    const dialog = document.querySelector('.el-dialog:has(.rpd)')
    const headers = [...dialog.querySelectorAll('.el-table__header th')].map((th) => th.innerText.trim())
    const codeIndex = headers.indexOf('存货编码')
    const nameIndex = headers.indexOf('存货名称')
    const rows = [...dialog.querySelectorAll('.el-table__body tbody tr')].slice(0, 2)
    const selected = rows.map((row) => ({ code: row.children[codeIndex]?.innerText.trim(), name: row.children[nameIndex]?.innerText.trim() }))
    rows.forEach((row) => row.querySelector('.el-checkbox__input')?.click())
    return JSON.stringify(selected)
  })()`))
  await cdp.evaluate(`document.querySelector('.el-dialog:has(.rpd) .el-dialog__footer button:last-child')?.click(); true`)
  return expected
}

async function readRows(cdp, panel) {
  return JSON.parse(await cdp.evaluate(`(() => {
    const table = document.querySelector('.detail')
    const headers = [...table.querySelectorAll('.el-table__header th')].map((th) => th.innerText.trim())
    const codeIndex = headers.indexOf(${JSON.stringify(panel.codeField)})
    const nameIndex = headers.indexOf(${JSON.stringify(panel.nameField)})
    return JSON.stringify([...table.querySelectorAll('.el-table__body tbody tr')]
      .map((row) => {
        const value = (cell) => cell?.querySelector('input')?.value || cell?.innerText.trim() || ''
        return { code: value(row.children[codeIndex]), name: value(row.children[nameIndex]) }
      })
      .filter((row) => row.code))
  })()`))
}

async function doubleClickCodeCell(cdp, panel) {
  return cdp.evaluate(`(() => {
    const table = document.querySelector('.detail')
    const headers = [...table.querySelectorAll('.el-table__header th')].map((th) => th.innerText.trim())
    const index = headers.indexOf(${JSON.stringify(panel.codeField)})
    const row = [...table.querySelectorAll('.el-table__body tbody tr')].find((tr) => !tr.classList.contains('ph-row'))
    const cell = row?.children[index]
    if (!cell) return false
    cell.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))
    return true
  })()`)
}

async function verifyManuDraftPresentation(cdp, token, panel, draft) {
  const style = JSON.parse(await cdp.evaluate(`(() => {
    const selected = document.querySelector('.panelx-list .detail .prod-selected td')
    const selectedBg = selected ? getComputedStyle(selected).backgroundColor : ''
    const disabledEditors = [...document.querySelectorAll([
      '.panelx-list .detail tbody .el-input.is-disabled .el-input__wrapper',
      '.panelx-list .detail tbody .el-input-number.is-disabled .el-input__wrapper',
      '.panelx-list .detail tbody .el-select__wrapper.is-disabled',
      '.panelx-list .detail tbody .el-textarea.is-disabled .el-textarea__inner',
    ].join(','))]
    const disabledBackgrounds = [...new Set(disabledEditors.map((editor) => getComputedStyle(editor).backgroundColor))]
    const disabledDetails = disabledEditors.slice(0, 5).map((editor) => ({
      className: editor.className,
      parentClass: editor.parentElement?.className || '',
      cellClass: editor.closest('td')?.className || '',
      background: getComputedStyle(editor).backgroundColor,
      matchesDraftRule: editor.matches('.panelx-list .draft-body .detail .el-select__wrapper.is-disabled, .panelx-list .draft-body .detail .el-input.is-disabled .el-input__wrapper'),
      detailClass: editor.closest('.detail')?.className || '',
      bodyClass: editor.closest('.body')?.className || '',
      panelClass: editor.closest('.panelx-list')?.className || '',
    }))
    const stylesheets = [...document.styleSheets].map((sheet) => sheet.href).filter(Boolean).filter((href) => href.includes('PanelxList'))
    return JSON.stringify({ selectedBg, disabledEditors: disabledEditors.length, disabledBackgrounds, disabledDetails, stylesheets })
  })()`))
  if (style.selectedBg && !['rgb(255, 255, 255)', 'rgba(0, 0, 0, 0)'].includes(style.selectedBg)) {
    throw new Error(`MANU_ORDER: selected row color pollution ${JSON.stringify(style)}`)
  }
  if (style.disabledBackgrounds.some((color) => !['rgb(255, 255, 255)', 'rgba(0, 0, 0, 0)'].includes(color))) {
    throw new Error(`MANU_ORDER: disabled editor color pollution ${JSON.stringify(style)}`)
  }

  const beforeCount = draft.detail?.[panel.tabKey]?.length || 0
  const clicked = await cdp.evaluate(`(() => {
    const row = document.querySelector('.detail .el-table__body tbody tr.ph-row')
    if (!row) return false
    row.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    return true
  })()`)
  if (!clicked || !await waitFor(cdp, `!!document.querySelector('.rpd')`)) throw new Error('MANU_ORDER: blank row did not open inventory reference')
  const activeIcons = await cdp.evaluate(`document.querySelectorAll('.detail .list-ref-icon').length`)
  if (activeIcons !== 1) throw new Error(`MANU_ORDER: blank row active icon count ${activeIcons}`)
  await cdp.evaluate(`document.querySelector('.el-dialog:has(.rpd) .el-dialog__footer button:first-child')?.click(); true`)
  if (!await waitFor(cdp, `!document.querySelector('.rpd') && !document.querySelector('.detail .list-ref-icon')`)) {
    throw new Error('MANU_ORDER: blank row dialog did not clean up')
  }
  const afterRows = await readRows(cdp, panel)
  const persisted = (await queryPanel(token, panel.code)).list.find((row) => row['编号'] === draft['编号'])
  if (afterRows.length !== beforeCount || persisted?.detail?.[panel.tabKey]?.length !== beforeCount) {
    throw new Error('MANU_ORDER: cancelling blank row left dirty detail data')
  }
  return style
}

async function verifyHeaderQuery(cdp, panel, draft) {
  await cdp.evaluate(`document.querySelector('.toolbar-query-btn')?.click(); true`)
  if (!await waitFor(cdp, `!!document.querySelector('.header-query-dialog')`)) throw new Error(`${panel.code}: query dialog did not open`)
  const shape = JSON.parse(await cdp.evaluate(`(() => {
    const dialog = document.querySelector('.header-query-dialog')
    const labels = [...dialog.querySelectorAll('.query-dialog-field > label')].map((label) => label.innerText.trim())
    return JSON.stringify({ labels, hasDetailCode: labels.includes(${JSON.stringify(panel.codeField)}) })
  })()`))
  const queryField = panel.code === 'SO_ORDER' ? '单据编号' : '锭号'
  const queryValue = draft[queryField] || draft['编号']
  if (shape.hasDetailCode || !shape.labels.includes(queryField)) throw new Error(`${panel.code}: query fields are not header-only ${JSON.stringify(shape)}`)
  const entered = await cdp.evaluate(`(() => {
    const field = [...document.querySelectorAll('.header-query-dialog .query-dialog-field')]
      .find((item) => item.querySelector('label')?.innerText.trim() === ${JSON.stringify(queryField)})
    const input = field?.querySelector('input')
    if (!input) return false
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
    setter.call(input, ${JSON.stringify(queryValue)})
    input.dispatchEvent(new Event('input', { bubbles: true }))
    return true
  })()`)
  if (!entered) throw new Error(`${panel.code}: ${queryField} query input missing`)
  await cdp.evaluate(`document.querySelector('.header-query-dialog .el-dialog__footer button:last-child')?.click(); true`)
  if (!await waitFor(cdp, `(document.querySelector('.doc-chip')?.innerText || '').includes(${JSON.stringify(draft['编号'])})`)) {
    throw new Error(`${panel.code}: header query did not locate draft`)
  }
  await cdp.evaluate(`document.querySelector('.toolbar-query-btn')?.click(); true`)
  await waitFor(cdp, `!!document.querySelector('.header-query-dialog')`)
  await cdp.evaluate(`document.querySelector('.header-query-dialog .el-dialog__footer button:first-child')?.click(); true`)
  await waitFor(cdp, `!document.querySelector('.header-query-dialog')`)
  return shape.labels.length
}

async function verifyInlineEditSave(cdp, token, panel, draft) {
  const headerField = panel.code === 'SO_ORDER' ? '联系人' : '客户'
  const originalHeader = draft[headerField] || ''
  const editedHeader = `${originalHeader || 'E2E'}-编辑`
  const originalQty = Number(draft.detail?.[panel.tabKey]?.[0]?.['数量'] || 0)
  const editedQty = originalQty + 1
  const edited = await cdp.evaluate(`(() => {
    const setValue = (input, value) => {
      if (!input) return false
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
      setter.call(input, String(value))
      input.dispatchEvent(new Event('input', { bubbles: true }))
      input.dispatchEvent(new Event('change', { bubbles: true }))
      return true
    }
    const header = [...document.querySelectorAll('.header-fields.is-draft .field')]
      .find((item) => item.querySelector('label')?.innerText.trim() === ${JSON.stringify(headerField)})
    const table = document.querySelector('.detail')
    const headers = [...table.querySelectorAll('.el-table__header th')].map((th) => th.innerText.trim())
    const qtyIndex = headers.indexOf('数量')
    const row = [...table.querySelectorAll('.el-table__body tbody tr')].find((tr) => !tr.classList.contains('ph-row'))
    return setValue(header?.querySelector('input:not([disabled]):not([readonly])'), ${JSON.stringify(editedHeader)})
      && setValue(row?.children[qtyIndex]?.querySelector('input:not([disabled])'), ${JSON.stringify(editedQty)})
  })()`)
  if (!edited) throw new Error(`${panel.code}: editable header/detail inputs not found`)
  const saveClicked = await cdp.evaluate(`(() => {
    const button = [...document.querySelectorAll('.tb-main')]
      .find((item) => item.querySelector('.act-name')?.innerText.trim() === '保存')
    button?.click()
    return !!button
  })()`)
  if (!saveClicked) throw new Error(`${panel.code}: save toolbar button missing`)
  const start = Date.now()
  let saved
  while (Date.now() - start < 20000) {
    saved = (await queryPanel(token, panel.code)).list.find((row) => row['编号'] === draft['编号'])
    if (saved?.[headerField] === editedHeader && Number(saved?.detail?.[panel.tabKey]?.[0]?.['数量']) === editedQty) break
    await sleep(400)
  }
  if (saved?.[headerField] !== editedHeader || Number(saved?.detail?.[panel.tabKey]?.[0]?.['数量']) !== editedQty) {
    throw new Error(`${panel.code}: inline edit was not persisted`)
  }
  if (panel.code === 'SO_ORDER') {
    const row = saved.detail.items[0]
    if (row['金额'] !== Math.round(editedQty * Number(row['单价']) * 100) / 100) throw new Error('SO_ORDER: inline amount not recalculated')
  }
  return { headerField, editedHeader, editedQty }
}

async function testPanel(cdp, token, panel, draft, nonDraft) {
  await openList(cdp, panel)
  await gotoOrder(cdp, nonDraft['编号'])
  if (await cdp.evaluate(`!!document.querySelector('.detail .list-ref-icon')`)) throw new Error(`${panel.code}: non-draft icon visible before double-click`)
  await doubleClickCodeCell(cdp, panel)
  await sleep(700)
  if (await cdp.evaluate(`!!document.querySelector('.rpd')`)) throw new Error(`${panel.code}: non-draft dialog opened`)
  if (await cdp.evaluate(`!!document.querySelector('.detail .list-ref-icon')`)) throw new Error(`${panel.code}: non-draft icon visible after double-click`)

  await gotoOrder(cdp, draft['编号'])
  const controls = JSON.parse(await cdp.evaluate(`JSON.stringify({ header: document.querySelectorAll('.header-fields.is-draft input').length, detail: document.querySelectorAll('.detail tbody input').length })`))
  if (!controls.header || !controls.detail) throw new Error(`${panel.code}: draft edit controls missing ${JSON.stringify(controls)}`)
  const presentation = panel.code === 'MANU_ORDER' ? await verifyManuDraftPresentation(cdp, token, panel, draft) : null
  if (await cdp.evaluate(`!!document.querySelector('.detail .list-ref-icon')`)) throw new Error(`${panel.code}: draft icon visible before double-click`)
  const before = await readRows(cdp, panel)
  await doubleClickCodeCell(cdp, panel)
  if (!await waitFor(cdp, `!!document.querySelector('.rpd')`)) throw new Error(`${panel.code}: reference dialog did not open`)
  const icon = JSON.parse(await cdp.evaluate(`(() => { const icons = [...document.querySelectorAll('.detail .list-ref-icon')]; const r = icons[0]?.getBoundingClientRect(); return JSON.stringify({ count: icons.length, width: r?.width, height: r?.height }) })()`))
  if (icon.count !== 1 || icon.width > 14 || icon.height > 14) throw new Error(`${panel.code}: active row magnifier invalid ${JSON.stringify(icon)}`)
  await cdp.evaluate(`document.querySelector('.el-dialog:has(.rpd) .el-dialog__footer button:first-child')?.click(); true`)
  if (!await waitFor(cdp, `!document.querySelector('.rpd') && !document.querySelector('.detail .list-ref-icon')`)) throw new Error(`${panel.code}: magnifier remained after dialog close`)
  await doubleClickCodeCell(cdp, panel)
  const selected = await selectTwo(cdp)
  if (!await waitFor(cdp, `!document.querySelector('.rpd') && (document.body.innerText || '').includes('存货并保存')`, 25000)) {
    throw new Error(`${panel.code}: import was not saved`)
  }
  const after = await readRows(cdp, panel)
  const keys = new Set(after.map((row) => `${row.code}\u0000${row.name}`))
  if (after.length !== before.length + 1 || selected.some((row) => !keys.has(`${row.code}\u0000${row.name}`))) {
    throw new Error(`${panel.code}: imported rows mismatch ${JSON.stringify(after)}`)
  }
  const persisted = (await queryPanel(token, panel.code)).list.find((row) => row['编号'] === draft['编号'])
  if (persisted?.detail?.[panel.tabKey]?.length !== after.length) throw new Error(`${panel.code}: rows not persisted`)
  if (panel.code === 'SO_ORDER') {
    const row = persisted.detail.items[0]
    if (row['含税单价'] !== Math.round(Number(row['单价']) * 1.13 * 100) / 100) throw new Error('SO_ORDER: tax price not recalculated')
  }
  const inlineEdit = await verifyInlineEditSave(cdp, token, panel, persisted)
  const queryFieldCount = await verifyHeaderQuery(cdp, panel, draft)
  return { no: draft['编号'], icon, controls, presentation, inlineEdit, queryFieldCount, before, selected, after }
}

async function waitForCodes(token, panel, documentNo, expected, timeout = 15000) {
  const start = Date.now()
  while (Date.now() - start < timeout) {
    const row = (await queryPanel(token, panel.code)).list.find((item) => item['编号'] === documentNo)
    const codes = (row?.detail?.[panel.tabKey] || []).map((item) => item[panel.codeField])
    if (JSON.stringify(codes) === JSON.stringify(expected)) return true
    await sleep(400)
  }
  return false
}

async function main() {
  await ensureBrowser()
  const cdp = await connect()
  const token = await login(cdp)
  const manuRows = (await queryPanel(token, 'MANU_ORDER')).list
  const manuDraft = manuRows.find((row) => row['单据状态'] === '草稿' && row.detail?.products?.length)
  const manuNonDraft = manuRows.find((row) => row['单据状态'] !== '草稿' && row.detail?.products?.length)
  if (!manuDraft || !manuNonDraft) throw new Error('MANU_ORDER fixtures missing')
  const manuOriginal = JSON.parse(JSON.stringify(manuDraft))
  const salesDraftNo = await createSalesDraft(token)
  let manuRestored = false
  let salesDeleted = false
  try {
    const salesRows = (await queryPanel(token, 'SO_ORDER')).list
    const salesDraft = salesRows.find((row) => row['编号'] === salesDraftNo)
    const salesNonDraft = salesRows.find((row) => row['单据状态'] !== '草稿' && row.detail?.items?.length)
    const results = []
    results.push(await testPanel(cdp, token, PANELS[0], manuDraft, manuNonDraft))
    await saveOrder(token, 'MANU_ORDER', manuOriginal)
    const originalCodes = manuOriginal.detail.products.map((item) => item['产品编码'])
    if (!await waitForCodes(token, PANELS[0], manuOriginal['编号'], originalCodes)) throw new Error('MANU_ORDER restore did not settle')
    manuRestored = true
    results.push(await testPanel(cdp, token, PANELS[1], salesDraft, salesNonDraft))
    await api(token, '/api/px/deleteForms', { panelCode: 'SO_ORDER', rowCodes: [salesDraftNo] })
    salesDeleted = true
    if (cdp.errors.length) throw new Error('Browser exceptions: ' + cdp.errors.join(' | '))
    console.log(JSON.stringify({ results, manuRestored, salesDraftDeleted: salesDeleted }, null, 2))
  } finally {
    if (!manuRestored) {
      await saveOrder(token, 'MANU_ORDER', manuOriginal)
      await waitForCodes(token, PANELS[0], manuOriginal['编号'], manuOriginal.detail.products.map((item) => item['产品编码']))
    }
    if (!salesDeleted) await api(token, '/api/px/deleteForms', { panelCode: 'SO_ORDER', rowCodes: [salesDraftNo] })
    cdp.ws.close()
  }
}

main().catch((error) => { console.error('[FAILED]', error.message); process.exit(1) })
