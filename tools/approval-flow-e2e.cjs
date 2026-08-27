const { execFileSync } = require('node:child_process')

const BASE = 'http://127.0.0.1:8080/api'
const PANEL = 'SO_ORDER'
const U = {
  code: '\u7f16\u53f7',
  status: '\u5355\u636e\u72b6\u6001',
  approval: '\u5ba1\u6279',
  submit: '\u63d0\u4ea4\u5ba1\u6279',
  approve: '\u5ba1\u6279\u901a\u8fc7',
  reject: '\u5ba1\u6279\u9a73\u56de',
  history: '\u5ba1\u6279\u60c5\u51b5',
  unaudit: '\u5f03\u5ba1',
  legacyAudit: '\u5ba1\u6838',
  draft: '\u8349\u7a3f',
  pending: '\u5ba1\u6279\u4e2d',
  approved: '\u5df2\u5ba1\u6838',
  save: '\u4fdd\u5b58',
  customer: '\u5ba2\u6237',
  opinion: '\u5ba1\u6279\u610f\u89c1',
}

let token = ''
let formNo = ''
let browserResult = null

function assert(condition, message) {
  if (!condition) throw new Error(message)
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
  if (!response.ok || payload.code !== 200) {
    throw new Error(`${path}: ${payload.message || response.statusText}`)
  }
  return payload.data
}

function button(buttonName, extra = {}) {
  return api('/px/callButton', {
    method: 'POST',
    body: JSON.stringify({
      panelCode: PANEL,
      buttonName,
      formData: { [U.code]: formNo, ...extra },
      buttonParam: {},
    }),
  })
}

function mysql(sql) {
  return execFileSync('mysql', [
    '--default-character-set=utf8mb4',
    '--ssl-mode=DISABLED',
    '--host=localhost',
    '--port=3306',
    '--user=root',
    '--skip-column-names',
    '--batch',
    '--execute', sql,
  ], { encoding: 'utf8' }).trim()
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
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
    const { resolve, reject, timer } = pending.get(message.id)
    pending.delete(message.id)
    clearTimeout(timer)
    if (message.error) reject(new Error(message.error.message))
    else resolve(message.result)
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

async function verifyBrowserToolbar(user) {
  const cdp = await connectCdp()
  try {
    await cdp.send('Page.enable')
    await cdp.send('Runtime.enable')
    await cdp.send('Page.navigate', { url: 'http://127.0.0.1:5173/' })
    await wait(1500)
    await cdp.evaluate(`localStorage.setItem('mes_token', ${JSON.stringify(token)});`
      + `localStorage.setItem('mes_user', ${JSON.stringify(JSON.stringify({ ...user, name: 'admin' }))}); true`)
    await cdp.send('Page.navigate', {
      url: `http://127.0.0.1:5173/?approval-e2e=${Date.now()}#/panelx/list/${PANEL}`,
    })

    let current = ''
    for (let attempt = 0; attempt < 30; attempt++) {
      await wait(300)
      current = await cdp.evaluate("document.querySelector('.doc-chip')?.innerText || ''")
      if (current.includes(formNo)) break
    }
    assert(current.includes(formNo), `browser did not select test document: ${current}`)

    const clickResult = await cdp.evaluate(`(() => {
      const groups = [...document.querySelectorAll('.tb-group')]
      const target = groups.find((group) => group.querySelector('.act-name')?.innerText.trim() === ${JSON.stringify(U.approval)})
      const auditGroups = groups.filter((group) => group.querySelector('.act-name')?.innerText.trim() === ${JSON.stringify(U.legacyAudit)}).length
      if (!target) return JSON.stringify({ found: false, auditGroups })
      target.querySelector('.tb-main')?.click()
      return JSON.stringify({ found: true, auditGroups })
    })()`)
    const clicked = JSON.parse(clickResult)
    assert(clicked.found && clicked.auditGroups === 0, 'browser toolbar was not normalized')
    await wait(500)
    const dialogTitle = await cdp.evaluate("document.querySelector('.el-message-box__title')?.innerText.trim() || ''")
    assert(dialogTitle.includes(U.submit), `main approval button opened wrong dialog: ${dialogTitle}`)
    await cdp.evaluate(`(() => {
      const button = [...document.querySelectorAll('.el-message-box__btns button')]
        .find((item) => item.innerText.includes('\\u53d6\\u6d88'))
      button?.click()
      return true
    })()`)
    browserResult = { currentDocument: formNo, group: U.approval, dialogTitle }
  } finally {
    cdp.socket.close()
  }
}

function cleanup() {
  if (!formNo || !/^[A-Z0-9_-]+$/.test(formNo)) return
  mysql(`DELETE FROM light_mes.form_approval WHERE panel_code='${PANEL}' AND form_no='${formNo}';`
    + ` DELETE FROM light_mes.form_data WHERE panel_code='${PANEL}' AND form_no='${formNo}';`)
}

async function main() {
  const login = await api('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ userName: 'admin', password: '123456' }),
  })
  token = login.token

  const config = await api(`/px/getPanelConfig?panelCode=${PANEL}`)
  const groups = config.metadata.buttonGroups || []
  const workflowGroups = groups.filter((group) => group.name === U.approval)
  assert(workflowGroups.length === 1, 'expected one approval toolbar group')
  assert(JSON.stringify(workflowGroups[0].actions) === JSON.stringify([
    U.submit, U.approve, U.reject, U.history, U.unaudit,
  ]), 'approval toolbar actions are not normalized')
  assert(!groups.some((group) => group.name === U.legacyAudit), 'legacy audit group is still visible')

  const created = await api('/px/callButton', {
    method: 'POST',
    body: JSON.stringify({
      panelCode: PANEL,
      buttonName: U.save,
      formData: { [U.customer]: 'APPROVAL-E2E', detail: { items: [] } },
      buttonParam: {},
    }),
  })
  formNo = created[U.code]
  assert(formNo, 'test document was not created')

  await verifyBrowserToolbar(login.user)

  const submitted = await button(U.legacyAudit, { [U.opinion]: 'legacy button compatibility' })
  assert(submitted[U.status] === U.pending, 'legacy audit did not enter pending approval')
  let history = await api(`/px/getApprovalHistory?panelCode=${PANEL}&code=${encodeURIComponent(formNo)}`)
  assert(history.length === 1 && history[0].action === 'SUBMIT' && history[0].result === 'PENDING',
    'SUBMIT/PENDING history was not written')

  const approved = await button(U.approve, { [U.opinion]: 'approved by e2e' })
  assert(approved[U.status] === U.approved, 'approval did not reach approved status')
  history = await api(`/px/getApprovalHistory?panelCode=${PANEL}&code=${encodeURIComponent(formNo)}`)
  assert(history.map((item) => item.action).join(',') === 'SUBMIT,APPROVE',
    'approval history does not contain SUBMIT,APPROVE')

  const reverted = await button(U.unaudit)
  assert(reverted[U.status] === U.draft, 'unaudit did not restore draft status')
  history = await api(`/px/getApprovalHistory?panelCode=${PANEL}&code=${encodeURIComponent(formNo)}`)
  assert(history.map((item) => item.action).join(',') === 'SUBMIT,APPROVE,UNAUDIT',
    'unaudit history was not written')

  console.log(JSON.stringify({
    panel: PANEL,
    formNo,
    toolbar: workflowGroups[0],
    browser: browserResult,
    finalStatus: reverted[U.status],
    history: history.map(({ action, result }) => ({ action, result })),
  }, null, 2))
}

main()
  .finally(cleanup)
  .catch((error) => {
    console.error(error.stack || error.message)
    process.exitCode = 1
  })
