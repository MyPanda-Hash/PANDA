#!/usr/bin/env node
/** probe-customer.cjs —— 探查客户控件结构 + 点击后反应（新标签页? 浮层?） */
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function main() {
  const port = 9222
  let wsUrl = null
  let page = null
  for (let i = 0; i < 30; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/json/list`)
      const targets = await res.json()
      page = targets.find((t) => t.type === 'page' && t.url.includes('BAPView'))
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
  const evalMain = async (expression) => { const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true, timeout: 15000 }); if (r.exceptionDetails) throw new Error('eval: ' + JSON.stringify(r.exceptionDetails).slice(0, 200)); return r.result.value }
  await send('Runtime.enable')
  await send('Page.enable')

  // 1. 客户控件 outerHTML + 可点击子元素
  const info = await evalMain(`(() => {
    const l = [...document.querySelectorAll('label')].find(l => (l.textContent || '').trim().replace(/^\\*/, '') === '客户' && l.getBoundingClientRect().height > 0)
    if (!l) return { err: 'no label' }
    let box = l
    for (let i = 0; i < 6 && box.parentElement; i++) { box = box.parentElement; if (box.querySelector('input,select,textarea')) break }
    const r = box.getBoundingClientRect()
    const clickables = [...box.querySelectorAll('input,span,button,i,img,div')].map(e => {
      const er = e.getBoundingClientRect()
      const s = getComputedStyle(e)
      return { tag: e.tagName, cls: (e.className || '').toString().slice(0, 30), type: e.getAttribute && e.getAttribute('type'), x: Math.round(er.x), y: Math.round(er.y), w: Math.round(er.width), h: Math.round(er.height), disp: s.display, vis: s.visibility, onclick: !!e.onclick || !!(e.getAttribute && e.getAttribute('onclick')) }
    }).filter(c => c.w > 0 && c.h > 0)
    return { boxRect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)], clickables, html: box.outerHTML.slice(0, 1800) }
  })()`)
  console.log('=== 客户控件 ===')
  console.log(JSON.stringify(info, null, 1))

  // 2. 点击控件（用 JS 直接触发 click 或 focus 更贴近真实）
  const before = await evalMain(`[...document.querySelectorAll('div,ul,li,span')].filter(e => { const s = getComputedStyle(e); if (s.position !== 'fixed' && s.position !== 'absolute') return false; const r = e.getBoundingClientRect(); return r.width > 40 && r.height > 20 && r.x >= 0 && r.y >= 0 && r.x < 1600 && r.y < 1000 }).length`)
  console.log('[before] overlays=' + before)
  // 鼠标点击控件中心（输入区）
  const c = await evalMain(`(() => {
    const l = [...document.querySelectorAll('label')].find(l => (l.textContent || '').trim().replace(/^\\*/, '') === '客户' && l.getBoundingClientRect().height > 0)
    if (!l) return null
    let box = l
    for (let i = 0; i < 6 && box.parentElement; i++) { box = box.parentElement; if (box.querySelector('input,select,textarea')) break }
    const r = box.getBoundingClientRect()
    return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height - 6) }
  })()`)
  console.log('[click] 客户 @' + c.x + ',' + c.y)
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: c.x, y: c.y })
  await sleep(150)
  await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: c.x, y: c.y, button: 'left', clickCount: 1 })
  await sleep(100)
  await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: c.x, y: c.y, button: 'left', clickCount: 1 })
  await sleep(4000)

  // 3. 点击后：新标签页？浮层？
  const res = await fetch(`http://127.0.0.1:${port}/json/list`)
  const targets = await res.json()
  console.log('[targets] ' + JSON.stringify(targets.filter((t) => t.type === 'page').map((t) => ({ title: t.title, url: t.url.slice(0, 110) })), null, 1))
  const after = await evalMain(`(() => {
    const overlays = [...document.querySelectorAll('div,ul,li,span')].filter(e => { const s = getComputedStyle(e); if (s.position !== 'fixed' && s.position !== 'absolute') return false; const r = e.getBoundingClientRect(); return r.width > 40 && r.height > 20 && r.x >= 0 && r.y >= 0 && r.x < 1600 && r.y < 1000 }).map(e => { const r = e.getBoundingClientRect(); return { cls: (e.className || '').toString().slice(0, 25), rect: [Math.round(r.x), Math.round(r.y), Math.round(r.width), Math.round(r.height)], head: (e.innerText || '').slice(0, 30) } })
    const dialogs = [...document.querySelectorAll('div')].filter(e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e); return r.width > 200 && r.height > 100 && (s.position === 'fixed' || s.position === 'absolute') && (e.innerText || '').includes('确定') }).map(e => ({ cls: (e.className || '').toString().slice(0, 30), head: (e.innerText || '').slice(0, 50) }))
    return { overlayCount: overlays.length, overlays: overlays.slice(0, 15), dialogs }
  })()`)
  console.log('[after] ' + JSON.stringify(after, null, 1))
  ws.close()
}
main().catch((e) => { console.error('[FAILED]', e.message); process.exit(1) })
