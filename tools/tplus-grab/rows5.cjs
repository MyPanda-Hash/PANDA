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
    const r = await evalMain('!!document.querySelector(\'.detail\')')
    if (r === true) break
    await sleep(1000)
  }
  await sleep(2500)
  const expr = "(() =\u003e {\n  const d = document.querySelector(\u0027.detail\u0027)\n  const table = d.querySelector(\u0027.el-table\u0027)\n  const R = (el) =\u003e { if (!el) return null; const r = el.getBoundingClientRect(); return { top: Math.round(r.top), bottom: Math.round(r.bottom), h: Math.round(r.height) } }\n  const headWrap = table.querySelector(\u0027.el-table__header-wrapper\u0027)\n  const bodyWrap = table.querySelector(\u0027.el-table__body-wrapper\u0027)\n  const footWrap = table.querySelector(\u0027.el-table__footer-wrapper\u0027)\n  const trs = [...table.querySelectorAll(\u0027.el-table__body tbody tr\u0027)]\n  const innerTable = table.querySelector(\u0027.el-table__body\u0027)\n  const cs = bodyWrap ? getComputedStyle(bodyWrap) : null\n  return JSON.stringify({\n    table: R(table),\n    head: R(headWrap),\n    body: R(bodyWrap),\n    bodyClientH: bodyWrap ? bodyWrap.clientHeight : 0,\n    bodyScrollH: bodyWrap ? bodyWrap.scrollHeight : 0,\n    bodyOverflowY: cs ? cs.overflowY : \u0027\u0027,\n    foot: R(footWrap),\n    footExists: !!footWrap,\n    footText: footWrap ? footWrap.innerText.slice(0, 80) : \u0027\u0027,\n    trCount: trs.length,\n    trRects: trs.map((t) =\u003e R(t)),\n    innerTableH: innerTable ? Math.round(innerTable.getBoundingClientRect().height) : 0,\n    scrollbars: [...table.querySelectorAll(\u0027.el-scrollbar__bar\u0027)].map((b) =\u003e ({ cls: b.className, dir: b.classList.contains(\u0027is-vertical\u0027) ? \u0027v\u0027 : \u0027h\u0027 })),\n  })\n})()"
  console.log(await evalMain(expr))
  ws.close()
}
main().catch((e) => { console.error('[FAILED]', e.message); process.exit(1) })