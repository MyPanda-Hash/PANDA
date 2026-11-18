#!/usr/bin/env node
/** shot.cjs：CDP 截图小工具（无依赖，Node>=21）。用法：
 *  node tools/tplus-grab/shot.cjs --url <url> --out <dir> --name <name>
 *    [--click <文本>]  页面加载后点掉包含该文本的按钮/元素
 *    [--wait <ms>]     截图前额外等待
 *    [--port <n>]      默认 9222
 */
const fs = require('node:fs')
const path = require('node:path')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function parseArgs() {
  const argv = process.argv.slice(2)
  const args = { port: 9222, click: null, wait: 1500, name: 'shot', out: '.' }
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i]
    if (k === '--url' || k === '--name' || k === '--out' || k === '--port' || k === '--click' || k === '--wait') { args[k.slice(2)] = argv[++i]; continue }
    throw new Error('未知参数: ' + k)
  }
  if (!args.url) throw new Error('缺少 --url')
  args.port = Number(args.port); args.wait = Number(args.wait)
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
    if (r.exceptionDetails) throw new Error('eval 异常: ' + JSON.stringify(r.exceptionDetails).slice(0, 200))
    return r.result.value
  }
  return { ws, send, evalMain }
}

async function main() {
  const args = parseArgs()
  const cdp = await connectCdp(args.port)
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  console.log('[page] ' + args.url)
  await cdp.send('Page.navigate', { url: args.url })
  for (let i = 0; i < 60; i++) {
    const r = await cdp.evalMain(`!!document.querySelector('body') && document.body.innerText.length > 0`)
    if (r === true) break
    await sleep(1000)
  }
  await sleep(args.wait)
  if (args.click) {
    const hit = await cdp.evalMain(`(() => {
      const els = [...document.querySelectorAll('button,span,a,div')]
      const el = els.find(e => e.children.length === 0 && (e.innerText || '').trim() === ${JSON.stringify(args.click)})
      if (el) { el.click(); return true }
      return false
    })()`)
    console.log('[click] ' + args.click + ' -> ' + hit)
    await sleep(1200)
  }
  fs.mkdirSync(args.out, { recursive: true })
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false })
  const shot = await cdp.send('Page.captureScreenshot', { format: 'png' })
  const file = path.join(args.out, args.name + '.png')
  fs.writeFileSync(file, Buffer.from(shot.data, 'base64'))
  console.log('[shot] ' + file)
  cdp.ws.close()
}

main().catch((e) => { console.error('[FAILED]', e.message); process.exit(1) })