#!/usr/bin/env node
/** probe-click-add.cjs —— 探查当前销售订单页状态，点击「新增」并报告结果 */
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
  if (!wsUrl) { console.log('no BAPView target'); return }
  console.log('[target] ' + page.title + ' | ' + page.url.slice(0, 120))
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
  await send('Page.enable')

  const before = await evalMain(`(() => ({
    vch: document.querySelectorAll('.vch-ctldiv').length,
    hasTable: !!document.querySelector('tbody tr'),
    bodyHead: (document.body.innerText || '').slice(0, 120),
    addBtns: [...document.querySelectorAll('[class*="tb-text"]')].filter(e => e.innerText && e.innerText.trim() === '新增').map(e => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) } })
  }))()`)
  console.log('[before] ' + JSON.stringify(before, null, 1))

  // 选最左上可见的新增按钮
  const btns = before.addBtns.filter((b) => b.y >= 0 && b.y < 60 && b.w > 10)
  console.log('[addbtns] ' + JSON.stringify(btns))
  if (!btns.length) { console.log('无可见新增按钮'); ws.close(); return }
  const target = btns[0]
  const cx = target.x + target.w / 2
  const cy = target.y + target.h / 2
  console.log('[click] 新增 @' + Math.round(cx) + ',' + Math.round(cy))
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: Math.round(cx), y: Math.round(cy) })
  await sleep(150)
  await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: Math.round(cx), y: Math.round(cy), button: 'left', clickCount: 1 })
  await sleep(100)
  await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: Math.round(cx), y: Math.round(cy), button: 'left', clickCount: 1 })

  await sleep(6000)
  const res2 = await fetch(`http://127.0.0.1:${port}/json/list`)
  const targets2 = await res2.json()
  console.log('[targets] ' + JSON.stringify(targets2.filter((t) => t.type === 'page').map((t) => ({ title: t.title, url: t.url.slice(0, 100) })), null, 1))

  // 可能跟随新 target
  const fresh = targets2.find((t) => t.type === 'page' && t.url.includes('BAPView') && t.id !== page.id)
  const ws2 = fresh ? new WebSocket(fresh.webSocketDebuggerUrl) : ws
  if (fresh) { await new Promise((res, rej) => { ws2.onopen = res; ws2.onerror = rej }); ws2.onmessage = ws.onmessage }
  let mid = 0
  const pending2 = new Map()
  ws2.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending2.has(m.id)) { const p = pending2.get(m.id); pending2.delete(m.id); m.error ? p.reject(new Error(m.error.message)) : p.resolve(m.result) } }
  const send2 = (method, params = {}) => new Promise((resolve, reject) => { const id = ++mid; pending2.set(id, { resolve, reject }); ws2.send(JSON.stringify({ id, method, params })) })
  const eval2 = async (expression) => { const r = await send2('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true, timeout: 20000 }); return r.result ? r.result.value : undefined }
  await send2('Runtime.enable')
  await sleep(3000)
  const after = await eval2(`(() => ({
    vch: document.querySelectorAll('.vch-ctldiv').length,
    url: location.href.slice(0, 130),
    title: document.title,
    labels: [...document.querySelectorAll('label')].map(l => l.textContent.trim()).filter(t => t && t.length < 12).slice(0, 30),
    bodyHead: (document.body.innerText || '').slice(0, 150)
  }))()`)
  console.log('[after] ' + JSON.stringify(after, null, 1))
  ws.close(); if (ws2 !== ws) ws2.close()
}

main().catch((e) => { console.error('[FAILED]', e.message); process.exit(1) })
