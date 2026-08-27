const { execFileSync } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')

const BASE = 'http://127.0.0.1:8080/api'
const PANEL = 'QUOTE_ORDER'
const U = {
  code: '\u7f16\u53f7',
  customer: '\u5ba2\u6237',
  itemCode: '\u5b58\u8d27\u7f16\u7801',
  itemName: '\u5b58\u8d27\u540d\u79f0',
  quantity: '\u6570\u91cf',
  quotePrice: '\u62a5\u4ef7\u5355\u4ef7',
  taxRate: '\u7a0e\u7387%',
  taxPrice: '\u542b\u7a0e\u5355\u4ef7',
  amount: '\u91d1\u989d',
  taxAmount: '\u542b\u7a0e\u91d1\u989d',
  stock: '\u73b0\u5b58\u91cf',
  unit: '\u9500\u552e\u5355\u4f4d',
  specification: '\u89c4\u683c\u578b\u53f7',
  save: '\u4fdd\u5b58',
}

let token = ''
let formNo = ''

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function api(path, options = {}) {
  const response = await fetch(BASE + path, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {}),
    },
  })
  const payload = await response.json()
  if (!response.ok || payload.code !== 200) throw new Error(`${path}: ${payload.message || response.statusText}`)
  return payload.data
}

function mysql(sql) {
  return execFileSync('mysql', [
    '--default-character-set=utf8mb4', '--ssl-mode=DISABLED', '--host=localhost', '--port=3306',
    '--user=root', '--database=light_mes', '--skip-column-names', '--batch', '--execute', sql,
  ], { encoding: 'utf8' }).trim()
}

async function connectCdp() {
  const targets = await fetch('http://127.0.0.1:9223/json/list').then((response) => response.json())
  const target = targets.find((item) => item.type === 'page')
  assert(target, 'no browser page found on CDP port 9223')
  const socket = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((resolve, reject) => {
    socket.onopen = resolve
    socket.onerror = reject
  })
  let requestId = 0
  const pending = new Map()
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data)
    if (!message.id || !pending.has(message.id)) return
    const request = pending.get(message.id)
    pending.delete(message.id)
    clearTimeout(request.timer)
    if (message.error) request.reject(new Error(message.error.message))
    else request.resolve(message.result)
  }
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++requestId
    const timer = setTimeout(() => {
      pending.delete(id)
      reject(new Error(`CDP timeout: ${method}`))
    }, 20000)
    pending.set(id, { resolve, reject, timer })
    socket.send(JSON.stringify({ id, method, params }))
  })
  const evaluate = async (expression) => {
    const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
    if (result.exceptionDetails) throw new Error(`browser evaluation failed: ${expression}`)
    return result.result.value
  }
  return { socket, send, evaluate }
}

async function setCell(cdp, label, value) {
  const changed = await cdp.evaluate(`(() => {
    const label = ${JSON.stringify(label)}
    const table = [...document.querySelectorAll('.el-table')].find((item) =>
      [...item.querySelectorAll('.el-table__header-wrapper th .cell')].some((cell) => cell.innerText.trim() === label))
    if (!table) return false
    const headers = [...table.querySelectorAll('.el-table__header-wrapper th .cell')].map((cell) => cell.innerText.trim())
    const index = headers.indexOf(label)
    const row = table.querySelector('.el-table__body-wrapper tbody tr:not(.ph-row)')
    const input = row?.children[index]?.querySelector('input')
    if (!input) return false
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, ${JSON.stringify(String(value))})
    input.dispatchEvent(new Event('input', { bubbles: true }))
    input.dispatchEvent(new Event('change', { bubbles: true }))
    input.blur()
    return true
  })()`)
  assert(changed, `cannot edit browser column: ${label}`)
  await wait(250)
}

async function readCells(cdp, labels) {
  return cdp.evaluate(`(() => {
    const labels = ${JSON.stringify(labels)}
    const table = [...document.querySelectorAll('.el-table')].find((item) =>
      [...item.querySelectorAll('.el-table__header-wrapper th .cell')].some((cell) => cell.innerText.trim() === ${JSON.stringify(U.quotePrice)}))
    if (!table) return null
    const headers = [...table.querySelectorAll('.el-table__header-wrapper th .cell')].map((cell) => cell.innerText.trim())
    const row = table.querySelector('.el-table__body-wrapper tbody tr:not(.ph-row)')
    return Object.fromEntries(labels.map((label) => {
      const cell = row?.children[headers.indexOf(label)]
      return [label, cell?.querySelector('input')?.value || cell?.innerText.trim() || '']
    }))
  })()`)
}

async function main() {
  const login = await api('/auth/login', {
    method: 'POST', body: JSON.stringify({ userName: 'admin', password: '123456' }),
  })
  token = login.token
  const stock = await api('/px/queryFormDataList', {
    method: 'POST',
    body: JSON.stringify({ panelCode: 'STOCK_STATUS', condition: {}, pageNo: 1, pageSize: 500 }),
  })
  const expectedStock = (stock.list || [])
    .filter((row) => row[U.itemCode] === 'CP001')
    .reduce((sum, row) => sum + Number(row['\u73b0\u5b58\u91cf(\u4e3b)'] || 0), 0)
  assert(expectedStock > 0, 'stock status has no CP001 quantity')

  const created = await api('/px/callButton', {
    method: 'POST',
    body: JSON.stringify({
      panelCode: PANEL,
      buttonName: U.save,
      formData: {
        [U.customer]: 'QUOTE-E2E',
        detail: { items: [{
          [U.itemCode]: 'CP001', [U.itemName]: '\u94dd\u68d2 \u03a680', [U.specification]: '\u03a680x3000',
          [U.quantity]: 1, [U.unit]: '\u4ef6', [U.quotePrice]: 0, [U.taxRate]: 13,
          [U.taxPrice]: 0, [U.amount]: 0, [U.taxAmount]: 0, [U.stock]: 0,
        }] },
      },
      buttonParam: {},
    }),
  })
  formNo = created[U.code]
  assert(formNo, 'quote document was not created')

  const cdp = await connectCdp()
  try {
    await cdp.send('Page.enable')
    await cdp.send('Runtime.enable')
    await cdp.send('Page.navigate', { url: 'http://127.0.0.1:5173/' })
    await wait(1000)
    await cdp.evaluate(`localStorage.setItem('mes_token', ${JSON.stringify(token)});`
      + `localStorage.setItem('mes_user', ${JSON.stringify(JSON.stringify({ ...login.user, name: 'admin' }))}); true`)
    await cdp.send('Page.navigate', {
      url: `http://127.0.0.1:5173/?quote-e2e=${Date.now()}#/panelx/list/${PANEL}`,
    })
    let current = ''
    for (let attempt = 0; attempt < 40; attempt++) {
      await wait(250)
      current = await cdp.evaluate("document.querySelector('.doc-chip')?.innerText || ''")
      if (current.includes(formNo)) break
    }
    assert(current.includes(formNo), `browser did not select quote: ${current}`)

    await setCell(cdp, U.quantity, 3)
    await setCell(cdp, U.quotePrice, 15.5)
    await setCell(cdp, U.taxRate, 13)
    const values = await readCells(cdp, [U.taxPrice, U.amount, U.taxAmount, U.stock])
    assert(values, 'quote detail table not found')
    assert(Number(values[U.taxPrice]) === 17.52, `unexpected tax price: ${values[U.taxPrice]}`)
    assert(Number(values[U.amount]) === 46.5, `unexpected amount: ${values[U.amount]}`)
    assert(Number(values[U.taxAmount]) === 52.56, `unexpected tax amount: ${values[U.taxAmount]}`)
    assert(Number(values[U.stock]) === expectedStock, `stock mismatch: ${values[U.stock]} != ${expectedStock}`)
    const screenshot = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false })
    const screenshotPath = path.join(__dirname, '..', 'docs', 'ref', 'mes-live', 'quote-order-calculation-20260826.png')
    fs.mkdirSync(path.dirname(screenshotPath), { recursive: true })
    fs.writeFileSync(screenshotPath, Buffer.from(screenshot.data, 'base64'))
    console.log(JSON.stringify({ formNo, expectedStock, values }, null, 2))
  } finally {
    cdp.socket.close()
  }
}

function cleanup() {
  if (!formNo || !/^[A-Z0-9_-]+$/.test(formNo)) return
  mysql(`DELETE FROM form_approval WHERE panel_code='${PANEL}' AND form_no='${formNo}';`
    + ` DELETE FROM form_data WHERE panel_code='${PANEL}' AND form_no='${formNo}';`)
}

main().catch((error) => {
  console.error(error.stack || error.message)
  process.exitCode = 1
}).finally(cleanup)
