#!/usr/bin/env node
/**
 * probe-action.cjs: click a toolbar button by text, follow new page target, dump structure.
 * Usage:
 *   node tools/tplus-grab/probe-action.cjs --url "<url>" --btn <text> [--port 9222] [--out <file>]
 */
const fs = require('node:fs')
const path = require('node:path')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function parseArgs() {
  const argv = process.argv.slice(2)
  const args = { port: 9222, out: null }
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i]
    if (k === '--url' || k === '--port' || k === '--out') { args[k.slice(2)] = argv[++i]; continue }
    if (k === '--btn') { args.btn = argv[++i]; continue }
    throw new Error('unknown arg: ' + k)
  }
  if (!args.url) throw new Error('missing --url')
  if (!args.btn) throw new Error('missing --btn')
  args.port = Number(args.port)
  return args
}

async function connectPage(port, wantUrlPart) {
  for (let i = 0; i < 30; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/json/list`)
      const targets = await res.json()
      const pages = targets.filter((t) => t.type === 'page')
      const pick = wantUrlPart ? pages.find((p) => p.url.includes(wantUrlPart)) || pages[pages.length - 1] : pages[0]
      if (pick) return { wsUrl: pick.webSocketDebuggerUrl, target: pick }
    } catch {}
    await sleep(1000)
  }
  throw new Error('no page target')
}

async function attach(wsUrl) {
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

const FORM_EXTRACT = `(() => {
  const cs = (el) => getComputedStyle(el)
  const R = (el) => { const r = el.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) } }
  const out = { url: location.href, title: document.title }
  // header controls: label + input/select type
  out.ctrls = [...document.querySelectorAll('div')].filter(e => {
    const l = e.querySelector(':scope > label')
    return l && l.textContent.trim()
  }).slice(0, 80).map(e => {
    const l = e.querySelector(':scope > label')
    const inp = e.querySelector('input,select,textarea')
    const r = R(e)
    return { label: l.textContent.trim(), type: inp ? inp.tagName + (inp.getAttribute('type') ? ':' + inp.getAttribute('type') : '') : '', cls: (e.className || '').toString().slice(0, 40), y: r.y, x: r.x }
  }).filter(c => c.y >= 0)
  // grids
  out.tables = [...document.querySelectorAll('table')].map((t, i) => {
    const cols = [...t.querySelectorAll('col')].map(c => ({ t: c.getAttribute('t') || '', a: c.getAttribute('a') || '', flag: c.getAttribute('flag') || '', w: c.getAttribute('width') || '' }))
    const rows = [...t.querySelectorAll('tbody tr')]
    const sample = rows.slice(0, 2).map(tr => [...tr.querySelectorAll('td')].map(td => td.innerText.trim()))
    return { idx: i, cls: (t.className || '').toString().slice(0, 50), cols, rowCount: rows.length, sample }
  }).filter(t => t.cols.length > 0)
  out.toolbar = [...document.querySelectorAll('.tb-text, [class*="tb-text"]')].map(e => e.innerText.trim()).filter(Boolean)
  const bt = document.body.innerText || ''
  out.bodyHead = bt.slice(0, 700)
  out.bodyTail = bt.slice(-700)
  return JSON.stringify(out)
})()`

async function main() {
  const args = parseArgs()
  const { wsUrl, target } = await connectPage(args.port)
  const cdp = await attach(wsUrl)
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  console.log('[page] current: ' + target.title + ' | ' + target.url.slice(0, 100))
  if (!target.url.includes('BAPView')) {
    await cdp.send('Page.navigate', { url: args.url })
    for (let i = 0; i < 60; i++) {
      const r = await cdp.evalMain(`!!(document.querySelector('table'))`)
      if (r === true) break
      await sleep(1500)
    }
    await sleep(2500)
  }
  // locate button by text, get its rect
  const btn = await cdp.evalMain(`(() => {
    const es = [...document.querySelectorAll('.tb-text, a, button, span, div')].filter(e => e.children.length === 0 && e.innerText && e.innerText.trim() === '${args.btn}')
    if (!es.length) return null
    const el = es[0]
    const r = el.getBoundingClientRect()
    return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), w: Math.round(r.width), h: Math.round(r.height), n: es.length }
  })()`)
  if (!btn) throw new Error('button not found: ' + args.btn)
  console.log('[btn] ' + args.btn + ' at ' + btn.x + ',' + btn.y + ' size ' + btn.w + 'x' + btn.h + ' matches=' + btn.n)
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: btn.x, y: btn.y })
  await sleep(150)
  await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: btn.x, y: btn.y, button: 'left', clickCount: 1 })
  await sleep(120)
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: btn.x, y: btn.y, button: 'left', clickCount: 1 })
  await sleep(3500)
  // list targets: may open new tab
  const res = await fetch(`http://127.0.0.1:${args.port}/json/list`)
  const targets = await res.json()
  out_targets = targets.filter((t) => t.type === 'page').map((t) => ({ title: t.title, url: t.url.slice(0, 140) }))
  console.log('[targets] ' + JSON.stringify(out_targets, null, 1))
  // follow the newest page that is not the list page
  const listUrl = target.url
  const fresh = targets.filter((t) => t.type === 'page' && t.url !== listUrl)
  const follow = fresh[fresh.length - 1] || targets.find((t) => t.type === 'page')
  const cdp2 = await attach(follow.webSocketDebuggerUrl)
  await cdp2.send('Page.enable')
  await cdp2.send('Runtime.enable')
  await sleep(2500)
  const dom = JSON.parse(await cdp2.evalMain(FORM_EXTRACT))
  dom.sourceTargets = out_targets
  const json = JSON.stringify(dom, null, 2)
  if (args.out) {
    fs.mkdirSync(path.dirname(args.out), { recursive: true })
    fs.writeFileSync(args.out, json)
    console.log('[out] ' + args.out)
  } else {
    console.log(json)
  }
  cdp.ws.close(); cdp2.ws.close()
}

main().catch((e) => { console.error('[FAILED]', e.message); process.exit(1) })