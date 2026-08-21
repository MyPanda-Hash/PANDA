#!/usr/bin/env node
/** 一次性本地 E2E：无头渲染登录页/桌面/生产加工单列表并截图，验证真实后端链路。 */
const fs = require('node:fs')
const path = require('node:path')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const OUT = path.join(__dirname, '..', 'docs', 'screenshots', 'e2e-local')
const BASE = 'http://localhost:5173'

async function connectCdp(port) {
  let wsUrl
  for (let i = 0; i < 30; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/json/list`)
      const targets = await res.json()
      const page = targets.find((t) => t.type === 'page')
      if (page) { wsUrl = page.webSocketDebuggerUrl; break }
    } catch {}
    await sleep(1000)
  }
  if (!wsUrl) throw new Error('CDP 无页面 target')
  const ws = new WebSocket(wsUrl)
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
  let msgId = 0
  const pending = new Map()
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data)
    if (msg.id && pending.has(msg.id)) {
      const p = pending.get(msg.id); pending.delete(msg.id)
      msg.error ? p.reject(new Error(msg.error.message)) : p.resolve(msg.result)
    }
  }
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++msgId; pending.set(id, { resolve, reject })
    ws.send(JSON.stringify({ id, method, params }))
  })
  const evalMain = async (expression) => {
    const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true, timeout: 20000 })
    if (r.exceptionDetails) throw new Error('eval 异常: ' + JSON.stringify(r.exceptionDetails).slice(0, 300))
    return r.result.value
  }
  return { ws, send, evalMain }
}

async function shot(cdp, name) {
  const r = await cdp.send('Page.captureScreenshot', { format: 'png' })
  fs.writeFileSync(path.join(OUT, name + '.png'), Buffer.from(r.data, 'base64'))
  return name + '.png'
}

async function waitFor(cdp, expr, label, tries = 40) {
  for (let i = 0; i < tries; i++) {
    try { if (await cdp.evalMain(expr) === true) return true } catch {}
    await sleep(500)
  }
  console.log('  [超时] ' + label)
  return false
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true })

  // 1. 通过 vite 代理登录，拿真实 token + user
  const loginRes = await fetch(`${BASE}/api/auth/login`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ userName: 'admin', password: '123456' }),
  })
  const login = await loginRes.json()
  if (login.code !== 200) throw new Error('登录失败: ' + JSON.stringify(login).slice(0, 200))
  const { token, user } = login.data
  console.log('[OK] 登录接口返回 code=200，user=' + user.userName + '，可见面板 ' + user.visiblePanels.length + ' 个')

  const cdp = await connectCdp(9223)
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')

  // 2. 登录页
  console.log('[1/3] 渲染登录页...')
  await cdp.send('Page.navigate', { url: `${BASE}/#/login` })
  await waitFor(cdp, `!!document.querySelector('#app') && document.querySelector('#app').innerText.length > 0`, '登录页挂载')
  await sleep(1500)
  const loginTitle = await cdp.evalMain(`document.title`)
  const loginText = await cdp.evalMain(`document.querySelector('#app').innerText.replace(/\\s+/g,' ').slice(0,120)`)
  const f1 = await shot(cdp, '01-login')
  console.log('  标题=' + loginTitle + ' | 内容片段=' + loginText)

  // 3. 注入登录态 → 桌面
  console.log('[2/3] 注入登录态，渲染桌面...')
  await cdp.evalMain(`localStorage.setItem('mes_token', ${JSON.stringify(token)});
    localStorage.setItem('mes_user', ${JSON.stringify(JSON.stringify(user))});
    localStorage.setItem('mes_login_date', '2026-08-20');`)
  await cdp.send('Page.navigate', { url: `${BASE}/?e2e=1#/dashboard` })
  await waitFor(cdp, `document.querySelector('#app') && document.querySelector('#app').innerText.length > 50`, '桌面挂载')
  await sleep(2000)
  const dashText = await cdp.evalMain(`document.querySelector('#app').innerText.replace(/\\s+/g,' ').slice(0,200)`)
  const f2 = await shot(cdp, '02-dashboard')
  console.log('  桌面内容片段=' + dashText)

  // 4. 生产加工单列表（真实后端 panel_config + form_data）
  console.log('[3/3] 渲染生产加工单列表（真实后端数据）...')
  await cdp.send('Page.navigate', { url: `${BASE}/?e2e=1#/panelx/list/MANU_ORDER` })
  await waitFor(cdp, `!!document.querySelector('.el-table, .detail, table')`, '面板列表挂载')
  await sleep(2500)
  const listText = await cdp.evalMain(`document.querySelector('#app').innerText.replace(/\\s+/g,' ').slice(0,260)`)
  const f3 = await shot(cdp, '03-manu-order')
  console.log('  列表内容片段=' + listText)

  cdp.ws.close()
  console.log('\\n[DONE] 截图输出目录: ' + OUT)
}

main().catch((e) => { console.error('[FAILED]', e.message); process.exit(1) })
