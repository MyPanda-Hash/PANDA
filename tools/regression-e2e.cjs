// regression-e2e.cjs: full regression - 11 panels list+groups+dialog
const fs = require('node:fs')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const PANELS = [
  ['PURCHASE_IN', '\u91c7\u8d2d\u5165\u5e93\u5355'], ['FINISH_IN', '\u4ea7\u6210\u54c1\u5165\u5e93\u5355'], ['OTHER_IN', '\u5176\u4ed6\u5165\u5e93\u5355'],
  ['SALE_OUT', '\u9500\u552e\u51fa\u5e93\u5355'], ['MATERIAL_OUT', '\u6750\u6599\u51fa\u5e93\u5355'], ['OTHER_OUT', '\u5176\u4ed6\u51fa\u5e93\u5355'],
  ['PU_ORDER', '\u91c7\u8d2d\u8ba2\u5355'], ['PU_IN', '\u8fdb\u8d27\u5355'], ['SALE_INV', '\u9500\u8d27\u5355'],
  ['PICK_ORDER', '\u914d\u8d27\u5355'], ['MATERIAL_REQ', '\u9886\u6599\u7533\u8bf7\u5355'],
  ['ARRIVAL_IN', '\u5230\u8d27\u5355'], ['FINISH_INSPECT', '\u6210\u54c1\u62a5\u68c0\u5355'], ['INSPECTION', '\u6765\u6599\u6210\u54c1\u68c0\u9a8c\u5355'], ['DISPATCH', '\u5de5\u5e8f\u6d3e\u5de5\u5355'],
]
async function main() {
  const port = 9222
  const res = await fetch(`http://127.0.0.1:${port}/json/list`)
  const targets = await res.json()
  const page = targets.find((t) => t.type === 'page')
  const ws = new WebSocket(page.webSocketDebuggerUrl)
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
  let msgId = 0
  const pending = new Map()
  const errs = []
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data)
    if (msg.id && pending.has(msg.id)) {
      const p = pending.get(msg.id); pending.delete(msg.id)
      msg.error ? p.reject(new Error(msg.error.message)) : p.resolve(msg.result)
    } else if (msg.method === 'Runtime.exceptionThrown') {
      errs.push((msg.params.exceptionDetails.exception ? msg.params.exceptionDetails.exception.description : msg.params.exceptionDetails.text).slice(0, 150))
    }
  }
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++msgId; pending.set(id, { resolve, reject })
    ws.send(JSON.stringify({ id, method, params }))
  })
  const evalMain = async (expression) => {
    const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true, timeout: 20000 })
    if (r.exceptionDetails) throw new Error('eval: ' + JSON.stringify(r.exceptionDetails).slice(0, 300))
    return r.result.value
  }
  await send('Page.enable'); await send('Runtime.enable')
  const results = []
  for (const [code, name] of PANELS) {
    errs.length = 0
    await send('Page.navigate', { url: 'http://localhost:5173/#/dashboard?e2e=' + Date.now() })
    await sleep(2200)
    await send('Page.navigate', { url: `http://localhost:5173/#/panelx/list/${code}?e2e=${Date.now()}` })
    await sleep(6000)
    const st = await evalMain(`(() => {
      const bt = document.body.innerText || ''
      const groups = [...new Set([...document.querySelectorAll('.tools .tb-group')].filter(g => g.offsetParent !== null).map(g => ((g.querySelector('.tb-main') || {}).innerText || '').trim()).filter(Boolean))]
      const doc = (() => { const i = bt.indexOf('\u5355\u636e\uff1a'); return i >= 0 ? bt.slice(i, i + 24) : '' })()
      const hasImport = groups.includes('\u5bfc\u5165')
      const hasSelect = groups.includes('\u9009\u5355')
      return JSON.stringify({ title: document.title, groups: groups.slice(0, 16), doc, hasImport, hasSelect, err: bt.includes('\u52a0\u8f7d\u5931\u8d25') || bt.includes('404') })
    })()`)
    const o = JSON.parse(st)
    results.push({ code, ...o })
    console.log(`[${code}] groups=${o.groups.length} import=${o.hasImport} select=${o.hasSelect} doc="${o.doc}" err=${o.err} ${errs.length ? 'EXC:' + errs[0] : ''}`)
  }
  fs.writeFileSync('F:/INCER/light-mes/docs/ref/mes-live/regression-e2e.json', JSON.stringify(results, null, 2))
  console.log('[saved] regression-e2e.json')
  ws.close()
}
main().catch((e) => { console.error('[FAILED]', e.message); process.exit(1) })