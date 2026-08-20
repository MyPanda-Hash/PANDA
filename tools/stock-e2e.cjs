// stock-e2e.cjs: verify 6 stock panels (list toolbar/query/header fields in New dialog)
const fs = require('node:fs')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const PANELS = [
  ['PURCHASE_IN', '\u91c7\u8d2d\u5165\u5e93\u5355'],
  ['FINISH_IN', '\u4ea7\u6210\u54c1\u5165\u5e93\u5355'],
  ['OTHER_IN', '\u5176\u4ed6\u5165\u5e93\u5355'],
  ['SALE_OUT', '\u9500\u552e\u51fa\u5e93\u5355'],
  ['MATERIAL_OUT', '\u6750\u6599\u51fa\u5e93\u5355'],
  ['OTHER_OUT', '\u5176\u4ed6\u51fa\u5e93\u5355'],
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
      errs.push((msg.params.exceptionDetails.exception ? msg.params.exceptionDetails.exception.description : msg.params.exceptionDetails.text).slice(0, 200))
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
  // login first if needed
  await send('Page.navigate', { url: 'http://localhost:5173/#/dashboard' })
  await sleep(5000)
  const isLogin = await evalMain(`(() => (document.body.innerText || '').includes('\u767b\u5f55\u5de5\u5382'))()`)
  if (isLogin) {
    await evalMain(`(() => {
      const inputs = [...document.querySelectorAll('input')]
      const setVal = (el, v) => { const d = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value'); d.set.call(el, v); el.dispatchEvent(new Event('input', { bubbles: true })) }
      setVal(inputs[0], 'admin'); setVal(inputs[1], '123456')
      const btn = [...document.querySelectorAll('button')].find(b => (b.innerText || '').includes('\u767b'))
      btn.click(); return true
    })()`)
    await sleep(5000)
  }
  const results = []
  for (const [code, name] of PANELS) {
    errs.length = 0
    await send('Page.navigate', { url: 'http://localhost:5173/#/dashboard' })
    await sleep(2500)
    await send('Page.navigate', { url: `http://localhost:5173/#/panelx/list/${code}` })
    await sleep(7000)
    const list = await evalMain(`(() => {
      const bt = document.body.innerText || ''
      const groups = [...document.querySelectorAll('.tb-group .act-name')].map(e => e.innerText.trim()).filter(Boolean)
      const qLabels = [...document.querySelectorAll('.fields .field label')].map(e => (e.innerText || '').trim()).filter(Boolean)
      const detailThs = [...document.querySelectorAll('.detail .el-table__header-wrapper th')].map(th => th.innerText.trim()).filter(Boolean).slice(0, 15)
      const err = bt.includes('\u52a0\u8f7d\u5931\u8d25') || bt.includes('404')
      return JSON.stringify({ groups, qLabels, detailThs: detailThs.slice(0, 12), err, title: document.title })
    })()`)
    // open New dialog
    const dlg = await evalMain(`(() => {
      const btn = [...document.querySelectorAll('.tb-group')].find(g => (g.querySelector('.act-name') || {}).innerText === '\u65b0\u589e')
      if (!btn) return 'no-new-btn'
      btn.querySelector('.tb-main').click()
      return 'clicked'
    })()`)
    await sleep(2500)
    const dlgInfo = await evalMain(`(() => {
      const d = [...document.querySelectorAll('.el-dialog')]
      if (!d.length) return 'no-dialog'
      const dlg = d.find(x => (x.innerText || '').includes('\u65b0\u589e')) || d[0]
      const labels = [...dlg.querySelectorAll('.field label, label')].map(e => (e.innerText || '').trim()).filter(Boolean).slice(0, 20)
      return JSON.stringify({ title: (dlg.querySelector('.el-dialog__title') || {}).innerText || '', labels })
    })()`)
    results.push({ code, name, list: JSON.parse(list), dlg: dlgInfo, errs: errs.slice(0, 2) })
    console.log(`[${code}] groups=${results[results.length - 1].list.groups.length} q=${results[results.length - 1].list.qLabels.length} dlg=${JSON.stringify(results[results.length - 1].dlg).slice(0, 200)} err=${results[results.length - 1].list.err}`)
    // close dialog if open
    await evalMain(`(() => { const b = [...document.querySelectorAll('.el-dialog__footer button')].find(x => (x.innerText || '').includes('\u53d6\u6d88')); if (b) b.click(); return true })()`)
    await sleep(800)
  }
  fs.writeFileSync('F:/INCER/light-mes/docs/ref/mes-live/stock-e2e.json', JSON.stringify(results, null, 2))
  console.log('[saved] stock-e2e.json')
  ws.close()
}
main().catch((e) => { console.error('[FAILED]', e.message); process.exit(1) })