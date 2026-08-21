#!/usr/bin/env node
/** probe-form.cjs —— 连接运行中的 Edge，探查销售订单新增表单的控件结构 */
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function main() {
  const port = 9222
  let wsUrl = null
  for (let i = 0; i < 30; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/json/list`)
      const targets = await res.json()
      const page = targets.find((t) => t.type === 'page' && t.url.includes('BAPView'))
      if (page) { wsUrl = page.webSocketDebuggerUrl; console.log('[target] ' + page.title + ' | ' + page.url.slice(0, 130)); break }
    } catch {}
    await sleep(1000)
  }
  if (!wsUrl) { console.log('未找到 BAPView target'); return }
  const ws = new WebSocket(wsUrl)
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
  let msgId = 0
  const pending = new Map()
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data)
    if (msg.id && pending.has(msg.id)) { const p = pending.get(msg.id); pending.delete(msg.id); msg.error ? p.reject(new Error(msg.error.message)) : p.resolve(msg.result) }
  }
  const send = (method, params = {}) => new Promise((resolve, reject) => { const id = ++msgId; pending.set(id, { resolve, reject }); ws.send(JSON.stringify({ id, method, params })) })
  const evalMain = async (expression) => {
    const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true, timeout: 20000 })
    if (r.exceptionDetails) throw new Error('eval: ' + JSON.stringify(r.exceptionDetails).slice(0, 300))
    return r.result.value
  }
  await send('Runtime.enable')

  const dom = JSON.parse(await evalMain(`(() => {
    const cs = (el) => getComputedStyle(el)
    const R = (el) => { const r = el.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) } }
    const vis = (el) => { const r = el.getBoundingClientRect(); const s = cs(el); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' }
    const out = { url: location.href, title: document.title }
    // 表单头控件：任意层级 label + 同容器 input/button
    out.ctrls = [...document.querySelectorAll('label')].map(l => {
      if (!vis(l)) return null
      const txt = l.textContent.trim()
      if (!txt) return null
      // 向上找最近的容器（包含 input 或 button 的祖先）
      let box = l
      for (let i = 0; i < 5 && box.parentElement; i++) {
        box = box.parentElement
        if (box.querySelector('input,select,textarea,button')) break
      }
      const inp = box.querySelector('input,select,textarea')
      const btns = [...box.querySelectorAll('span,button,i,img')].filter(b => vis(b)).slice(0, 4).map(b => ({ cls: (b.className || '').toString().slice(0, 40), tag: b.tagName, onclick: !!b.onclick }))
      const r = R(box)
      if (r.y < 0 || r.h === 0) return null
      return { label: txt, lx: R(l).x, ly: R(l).y, val: inp ? inp.value : '', type: inp ? inp.tagName + (inp.getAttribute('type') ? ':' + inp.getAttribute('type') : '') : '', ro: inp ? inp.readOnly : null, cls: (box.className || '').toString().slice(0, 50), x: r.x, y: r.y, w: r.w, h: r.h, btns: btns.map(b => b.cls + '/' + b.tag + (b.onclick ? '!onclick' : '')) }
    }).filter(Boolean).slice(0, 60)
    // 可见明细表：找第一个行数>0 且宽度大且可见的 table
    out.tables = [...document.querySelectorAll('table')].map((t, i) => {
      const r = R(t)
      if (!vis(t) || r.w < 300 || r.h < 80) return null
      const cols = [...t.querySelectorAll('col')].map(c => ({ t: c.getAttribute('t') || '', flag: c.getAttribute('flag') || '', w: c.getAttribute('width') || '' }))
      return { idx: i, cls: (t.className || '').toString().slice(0, 40), x: r.x, y: r.y, w: r.w, h: r.h, cols: cols.slice(0, 40), rowN: t.querySelectorAll('tbody tr').length }
    }).filter(Boolean).slice(0, 8)
    // 状态位
    out.voucherState = (location.search.match(/voucherStateControl=(\w+)/) || [])[1] || ''
    out.bodyHead = (document.body.innerText || '').slice(0, 250)
    return JSON.stringify(out)
  })()`))
  console.log(JSON.stringify(dom, null, 1))
  ws.close()
}

main().catch((e) => { console.error('[FAILED]', e.message); process.exit(1) })
