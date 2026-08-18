#!/usr/bin/env node
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
async function main() {
  let wsUrl
  for (let i = 0; i < 30; i++) {
    try {
      const res = await fetch('http://127.0.0.1:9222/json/list')
      const targets = await res.json()
      const page = targets.find((t) => t.type === 'page')
      if (page) { wsUrl = page.webSocketDebuggerUrl; break }
    } catch {}
    await sleep(1000)
  }
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
    if (r.exceptionDetails) throw new Error('eval 异常: ' + JSON.stringify(r.exceptionDetails).slice(0, 200))
    return r.result.value
  }
  await send('Page.enable'); await send('Runtime.enable')
  await send('Page.navigate', { url: 'http://localhost:5173/#/panelx/list/MANU_ORDER' })
  for (let i = 0; i < 60; i++) {
    const r = await evalMain('!!document.querySelector(\'.tools\')')
    if (r === true) break
    await sleep(1000)
  }
  await sleep(2500)
  const expr = "(() =\u003e {\n  const tools = document.querySelector(\u0027.tools\u0027)\n  const groups = tools ? [...tools.querySelectorAll(\u0027.tb-group\u0027)] : []\n  const btns = tools ? [...tools.querySelectorAll(\u0027.tb-main\u0027)].map((b) =\u003e b.innerText.trim()) : []\n  const carets = tools ? [...tools.querySelectorAll(\u0027.tb-caret\u0027)].length : 0\n  const footerBtns = document.querySelector(\u0027.footer-btns\u0027) ? true : false\n  // 点开第一个 ▼ 看下拉\n  if (carets \u003e 0) { const c = tools.querySelector(\u0027.tb-caret\u0027); c.click() }\n  return new Promise((resolve) =\u003e setTimeout(() =\u003e {\n    const menu = tools ? [...tools.querySelectorAll(\u0027.tb-menu .ctx-item\u0027)].map((m) =\u003e m.innerText.trim()) : []\n    const footerText = document.querySelector(\u0027.footer\u0027)?.innerText.replace(/\\s+/g, \u0027 \u0027).trim().slice(0, 120) || \u0027\u0027\n    resolve(JSON.stringify({ groupCount: groups.length, btns, carets, footerHasBtnBar: footerBtns, menuItems: menu.slice(0, 12), footerText }))\n  }, 200))\n})()"
  console.log(await evalMain(expr))
  ws.close()
}
main().catch((e) => { console.error('[FAILED]', e.message); process.exit(1) })