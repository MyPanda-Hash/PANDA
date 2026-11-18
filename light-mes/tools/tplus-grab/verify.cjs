#!/usr/bin/env node
/** verify.cjs：核验生产加工单列表页各明细页签（无依赖）。用法：
 *  node tools/tplus-grab/verify.cjs --url <url> [--port 9222] [--dismiss <文本>]
 */
const fs = require('node:fs')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function parseArgs() {
  const argv = process.argv.slice(2)
  const args = { port: 9222, dismiss: null }
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i]
    if (k === '--url' || k === '--port' || k === '--dismiss') { args[k.slice(2)] = argv[++i]; continue }
    throw new Error('未知参数: ' + k)
  }
  if (!args.url) throw new Error('缺少 --url')
  args.port = Number(args.port)
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
  if (!wsUrl) throw new Error('CDP 无页面 target')
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
    if (r.exceptionDetails) throw new Error('eval 异常: ' + JSON.stringify(r.exceptionDetails).slice(0, 300))
    return r.result.value
  }
  return { ws, send, evalMain }
}

const dumpTables = `(() => {
  const out = []
  document.querySelectorAll('.detail').forEach((d, i) => {
    const tabs = [...d.querySelectorAll('.dt-tab')].map((e) => e.innerText.trim() + (e.classList.contains('on') ? '*' : ''))
    const table = d.querySelector('.el-table')
    const rows = table ? [...table.querySelectorAll('.el-table__body tbody tr')].map((tr) => tr.innerText.replace(/\\s+/g, ' ').trim().slice(0, 140)) : []
    const foot = table ? (table.querySelector('.el-table__footer-wrapper')?.innerText.replace(/\\s+/g, ' ').trim().slice(0, 140) || '') : ''
    const cols = table ? [...table.querySelectorAll('.el-table__header th')].map((th) => th.innerText.trim()).slice(0, 16) : []
    out.push({ idx: i, tabs, cols, rows, foot })
  })
  return JSON.stringify({ doc: document.querySelector('.doc-chip')?.innerText, page: document.querySelector('.page-no')?.innerText, tables: out })
})()`

async function main() {
  const args = parseArgs()
  const cdp = await connectCdp(args.port)
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  await cdp.send('Page.navigate', { url: args.url })
  for (let i = 0; i < 60; i++) {
    const r = await cdp.evalMain(`!!document.querySelector('.detail')`)
    if (r === true) break
    await sleep(1000)
  }
  await sleep(2000)
  if (args.dismiss) {
    await cdp.evalMain(`(() => { const el = [...document.querySelectorAll('button,span,a,div')].find(e => e.children.length === 0 && (e.innerText || '').trim() === ${JSON.stringify(args.dismiss)}); if (el) { el.click(); return true } return false })()`)
    await sleep(1000)
  }
  const states = ['默认', '产成品明细汇总', '材料明细汇总', '工序明细']
  for (const s of states) {
    if (s !== '默认') {
      await cdp.evalMain(`(() => { const el = [...document.querySelectorAll('.dt-tab')].find(e => e.innerText.trim() === ${JSON.stringify(s)}); if (el) { el.click(); return true } return false })()`)
      await sleep(600)
    }
    const j = await cdp.evalMain(dumpTables)
    console.log('==== 状态: ' + s)
    console.log(j)
  }
  cdp.ws.close()
}

main().catch((e) => { console.error('[FAILED]', e.message); process.exit(1) })