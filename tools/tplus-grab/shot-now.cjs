#!/usr/bin/env node
/** shot-now.cjs —— 对当前 BAPView 标签页截图 */
const fs = require('node:fs')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function main() {
  const port = 9222
  const out = process.argv[2] || 'docs/ref/mes-live/so-e2e-now.png'
  let wsUrl = null
  for (let i = 0; i < 30; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/json/list`)
      const targets = await res.json()
      const page = targets.find((t) => t.type === 'page' && t.url.includes('BAPView'))
      if (page) { wsUrl = page.webSocketDebuggerUrl; console.log('[target] ' + page.title); break }
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
  await send('Page.enable')
  await send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false })
  const shot = await send('Page.captureScreenshot', { format: 'png' })
  fs.writeFileSync(out, Buffer.from(shot.data, 'base64'))
  console.log('[shot] ' + out + ' bytes=' + shot.data.length)
  ws.close()
}
main().catch((e) => { console.error('[FAILED]', e.message); process.exit(1) })
