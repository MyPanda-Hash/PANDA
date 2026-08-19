#!/usr/bin/env node
/**
 * probe-doublelist.cjs: deep probe for DoubleList archive pages (route AA1055 / BOM AA1041).
 * Usage:
 *   node tools/tplus-grab/probe-doublelist.cjs --url "<url>" [--port 9222] [--out <file>] [--click-x N --click-y N]
 * Output: JSON with toolbar / left+right table column defs / sample rows / after-click detail.
 * Reuses the Edge started by grab.cjs (port 9222, profile keeps login session).
 */
const fs = require('node:fs')
const path = require('node:path')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function parseArgs() {
  const argv = process.argv.slice(2)
  const args = { port: 9222, out: null, clickX: null, clickY: null }
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i]
    if (k === '--url' || k === '--port' || k === '--out') { args[k.slice(2)] = argv[++i]; continue }
    if (k === '--click-x') { args.clickX = Number(argv[++i]); continue }
    if (k === '--click-y') { args.clickY = Number(argv[++i]); continue }
    throw new Error('unknown arg: ' + k)
  }
  if (!args.url) throw new Error('missing --url')
  args.port = Number(args.port)
  if (args.clickX !== null) args.clickX = Number(args.clickX)
  if (args.clickY !== null) args.clickY = Number(args.clickY)
  return args
}

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
  if (!wsUrl) throw new Error('CDP no page target')
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
    if (r.exceptionDetails) throw new Error('eval exception: ' + JSON.stringify(r.exceptionDetails).slice(0, 300))
    return r.result.value
  }
  return { ws, send, evalMain }
}

const EXTRACT = `(() => {
  const cs = (el) => getComputedStyle(el)
  const R = (el) => { const r = el.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) } }
  const out = { url: location.href, title: document.title }

  out.tables = [...document.querySelectorAll('table')].map((t, i) => {
    const cols = [...t.querySelectorAll('col')].map(c => ({ t: c.getAttribute('t') || '', a: c.getAttribute('a') || '', flag: c.getAttribute('flag') || '', w: c.getAttribute('width') || '' }))
    const rows = [...t.querySelectorAll('tbody tr')]
    const sample = rows.slice(0, 3).map(tr => [...tr.querySelectorAll('td')].map(td => td.innerText.trim()))
    const ths = [...t.querySelectorAll('th')].map(th => th.innerText.trim()).filter(Boolean)
    const rect = t.getBoundingClientRect()
    return { idx: i, cls: (t.className || '').toString().slice(0, 60), rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) }, cols, ths, rowCount: rows.length, sample }
  }).filter(t => t.cols.length > 0 || t.ths.length > 0)

  out.toolbar = [...document.querySelectorAll('.tb-text, [class*="tb-text"]')].map(e => e.innerText.trim()).filter(Boolean)
  const btns = [...document.querySelectorAll('a,button,span,div')].filter(e => e.children.length === 0 && e.innerText && e.innerText.trim().length < 20)
  out.toolbarAll = btns.map(e => ({ t: e.innerText.trim(), cls: (e.className || '').toString().slice(0, 40), id: e.id || '' })).filter(b => b.t)

  out.query = [...document.querySelectorAll('label')].slice(0, 30).map(l => {
    const p = l.parentElement
    const inp = p ? p.querySelector('input,select') : null
    const r = R(l)
    if (r.y < 0 || r.h === 0) return null
    return { label: l.innerText.trim(), x: r.x, y: r.y, inW: inp ? R(inp).w : 0 }
  }).filter(Boolean)

  out.tabs = [...document.querySelectorAll('[id^="tab_"], [class*="tab"]')].map(t => ({ id: t.id, text: (t.textContent || '').trim().slice(0, 20) })).filter(t => t.text && t.text.length < 20).slice(0, 15)

  out.containers = [...document.querySelectorAll('div')].filter(e => { const r = e.getBoundingClientRect(); return r.w > 300 && r.h > 80 }).slice(0, 15).map(e => ({ cls: (e.className || '').toString().slice(0, 50), rect: R(e) }))

  const bt = document.body.innerText || ''
  out.bodyHead = bt.slice(0, 900)
  out.bodyTail = bt.slice(-900)
  return JSON.stringify(out)
})()`

async function main() {
  const args = parseArgs()
  const cdp = await connectCdp(args.port)
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  console.log('[page] open ' + args.url)
  await cdp.send('Page.navigate', { url: args.url })
  let ready = false
  for (let i = 0; i < 60; i++) {
    const r = await cdp.evalMain(`!!(document.querySelector('table')) && (document.body.innerText || '').includes('\\u5de5\\u827a\\u8def\\u7ebf')`)
    if (r === true) { ready = true; break }
    await sleep(1500)
  }
  if (!ready) console.log('[page] warn: target table not ready')
  await sleep(3000)
  const dom = JSON.parse(await cdp.evalMain(EXTRACT))

  if (args.clickX !== null && args.clickY !== null) {
    console.log('[click] real mouse at ' + args.clickX + ',' + args.clickY)
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: args.clickX, y: args.clickY })
    await sleep(200)
    await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: args.clickX, y: args.clickY, button: 'left', clickCount: 1 })
    await sleep(120)
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: args.clickX, y: args.clickY, button: 'left', clickCount: 1 })
    await sleep(2500)
    dom.afterClick = JSON.parse(await cdp.evalMain(EXTRACT))
  }

  const json = JSON.stringify(dom, null, 2)
  if (args.out) {
    fs.mkdirSync(path.dirname(args.out), { recursive: true })
    fs.writeFileSync(args.out, json)
    console.log('[out] ' + args.out)
  } else {
    console.log(json)
  }
  cdp.ws.close()
}

main().catch((e) => { console.error('[FAILED]', e.message); process.exit(1) })