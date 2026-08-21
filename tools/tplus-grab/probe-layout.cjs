#!/usr/bin/env node
/** probe-layout.cjs —— 检查销售订单页 列表 vs 表单 的可见性/层级真相 */
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function main() {
  const port = 9222
  let wsUrl = null
  for (let i = 0; i < 30; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/json/list`)
      const targets = await res.json()
      const page = targets.find((t) => t.type === 'page' && t.url.includes('BAPView'))
      if (page) { wsUrl = page.webSocketDebuggerUrl; break }
    } catch {}
    await sleep(1000)
  }
  if (!wsUrl) { console.log('no target'); return }
  const ws = new WebSocket(wsUrl)
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
  let id = 0
  const pending = new Map()
  ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.reject(new Error(m.error.message)) : p.resolve(m.result) } }
  const send = (method, params = {}) => new Promise((resolve, reject) => { const i = ++id; pending.set(i, { resolve, reject }); ws.send(JSON.stringify({ id: i, method, params })) })
  const evalMain = async (expression) => { const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true, timeout: 20000 }); if (r.exceptionDetails) throw new Error('eval: ' + JSON.stringify(r.exceptionDetails).slice(0, 200)); return r.result.value }
  await send('Runtime.enable')

  const info = await evalMain(`(() => {
    const cs = (el) => getComputedStyle(el)
    const chain = (el) => {
      const out = []
      let cur = el
      for (let i = 0; i < 8 && cur && cur !== document.body; i++) {
        const s = cs(cur)
        const r = cur.getBoundingClientRect()
        out.push({ tag: cur.tagName, cls: (cur.className || '').toString().slice(0, 40), display: s.display, visibility: s.visibility, position: s.position, opacity: s.opacity, z: s.zIndex, rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)] })
        cur = cur.parentElement
      }
      return out
    }
    const vch = document.querySelector('.vch-ctldiv')
    const listGrid = [...document.querySelectorAll('table')].find(t => t.querySelector('tbody tr') && (t.className || '').toString().includes('List') === false && t.getBoundingClientRect().width > 800)
    return {
      scrollH: document.body.scrollHeight,
      winH: window.innerHeight,
      iframes: [...document.querySelectorAll('iframe')].map(f => f.src.slice(0, 80)),
      vchChain: vch ? chain(vch) : null,
      vchCount: document.querySelectorAll('.vch-ctldiv').length,
      listTableChain: listGrid ? chain(listGrid) : null,
      visibleTables: [...document.querySelectorAll('table')].filter(t => { const r = t.getBoundingClientRect(); const s = cs(t); return r.width > 200 && r.height > 40 && s.display !== 'none' && s.visibility !== 'hidden' }).map(t => ({ cls: (t.className || '').toString().slice(0, 30), rect: [Math.round(t.getBoundingClientRect().x), Math.round(t.getBoundingClientRect().y), Math.round(t.getBoundingClientRect().width), Math.round(t.getBoundingClientRect().height)] })).slice(0, 10)
    }
  })()`)
  console.log(JSON.stringify(info, null, 1))
  ws.close()
}
main().catch((e) => { console.error('[FAILED]', e.message); process.exit(1) })
