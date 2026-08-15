#!/usr/bin/env node
/**
 * tplus-grab：真实 T+ 演示环境单据页 DOM 抓取器（无第三方依赖，Node >= 21）
 *
 * 用法：
 *   node tools/tplus-grab/grab.cjs --url "<voucherUrl>" [选项]
 *
 * 选项：
 *   --url      必填。单据页 URL，如
 *              https://h2t.chanjet.com/tplus/BAPView/Voucher.aspx?sysId=mp&mId=mp05&pId=voucherView
 *   --name     输出前缀（默认 dom）
 *   --out      输出目录（默认 ./.tplus-grab-out）
 *   --login    走「立即体验 → 轻MES」演示账号登录流程（首次或会话失效时用）
 *   --port     CDP 端口（默认 9222）
 *   --profile  Edge 会话目录（默认本目录下 ./profile，登录态跨次保留）
 *   --width/--height  视口（默认 1600x1000）
 *   --no-gen   只抓取，不生成骨架 HTML
 *
 * 输出：<out>/<name>.dom.json、<name>.png、<name>.html（骨架）
 * 后续步骤：用 DSH Vision Toolkit 的 vision_html_screenshot 渲染骨架，
 *           再 vision_pixel_diff 与 <name>.png 逐像素对比迭代。
 */
const { spawn } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function parseArgs() {
  const argv = process.argv.slice(2)
  const args = { port: 9222, name: 'dom', out: path.resolve('.tplus-grab-out'), width: 1600, height: 1000, login: false, noGen: false }
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i]
    if (k === '--url' || k === '--name' || k === '--out' || k === '--port' || k === '--profile' || k === '--width' || k === '--height') { args[k.slice(2)] = argv[++i]; continue }
    if (k === '--login') { args.login = true; continue }
    if (k === '--no-gen') { args.noGen = true; continue }
    throw new Error('未知参数: ' + k)
  }
  if (!args.url) throw new Error('缺少 --url')
  args.width = Number(args.width); args.height = Number(args.height); args.port = Number(args.port)
  if (!args.profile) args.profile = path.join(__dirname, 'profile')
  return args
}

function findEdge() {
  const candidates = [
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  ]
  return candidates.find((p) => fs.existsSync(p))
}

async function ensureBrowser(args) {
  for (let i = 0; i < 3; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${args.port}/json/version`)
      if (res.ok) return console.log('[browser] 复用已在运行的 Edge (port ' + args.port + ')')
    } catch {}
    break
  }
  const edge = findEdge()
  if (!edge) throw new Error('未找到 msedge.exe')
  fs.mkdirSync(args.profile, { recursive: true })
  console.log('[browser] 启动无头 Edge (port ' + args.port + ')')
  const child = spawn(edge, [
    '--headless=new', `--remote-debugging-port=${args.port}`, `--user-data-dir=${args.profile}`,
    '--no-first-run', '--no-default-browser-check', '--disable-gpu',
    `--window-size=${args.width},${args.height}`, '--force-device-scale-factor=1', 'about:blank',
  ], { detached: true, stdio: 'ignore' })
  child.unref()
  for (let i = 0; i < 60; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${args.port}/json/version`)
      if (res.ok) return console.log('[browser] Edge 已就绪')
    } catch {}
    await sleep(1000)
  }
  throw new Error('Edge 启动超时')
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
  const contexts = new Map()
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data)
    if (msg.id && pending.has(msg.id)) {
      const p = pending.get(msg.id); pending.delete(msg.id)
      msg.error ? p.reject(new Error(msg.error.message)) : p.resolve(msg.result)
    } else if (msg.method === 'Runtime.executionContextCreated') {
      const c = msg.params.context
      if (c.auxData && c.auxData.frameId) contexts.set(c.auxData.frameId, c.id)
    }
  }
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++msgId; pending.set(id, { resolve, reject })
    ws.send(JSON.stringify({ id, method, params }))
  })
  const evalIn = async (expression, contextId) => {
    const r = await send('Runtime.evaluate', { expression, contextId, returnByValue: true, awaitPromise: true, timeout: 20000 })
    if (r.exceptionDetails) throw new Error('eval 异常: ' + JSON.stringify(r.exceptionDetails).slice(0, 200))
    return r.result.value
  }
  const frameWith = async (urlPart) => {
    const tree = await send('Page.getFrameTree')
    const walk = (n) => {
      if (n.frame.url.includes(urlPart)) return n.frame
      for (const c of n.childFrames || []) { const f = walk(c); if (f) return f }
      return null
    }
    return walk(tree.frameTree)
  }
  return { ws, send, evalIn, evalMain: (e) => evalIn(e, undefined), frameWith, contexts }
}

async function loginFlow(cdp, args) {
  console.log('[login] 打开登录页')
  await cdp.send('Page.navigate', { url: 'https://t.chanjet.com/tplus/view/login.html' })
  await sleep(6000)
  const clicked = await cdp.evalMain(`(() => { const b = document.querySelector('#expBtn') || [...document.querySelectorAll('button,a,div')].find(e => e.textContent.includes('立即体验')); if (b) { b.click(); return true; } return false; })()`)
  console.log('[login] 立即体验: ' + clicked)
  let mes = false
  for (let i = 0; i < 40; i++) {
    const fr = await cdp.frameWith('selectRoles')
    if (fr) {
      let ctxId = cdp.contexts.get(fr.id)
      if (!ctxId) {
        const w = await cdp.send('Page.createIsolatedWorld', { frameId: fr.id, worldName: 'tplus-probe', grantUniveralAccess: true })
        ctxId = w.executionContextId
      }
      const hit = await cdp.evalIn(`(() => { const el = document.querySelector('#mes') || [...document.querySelectorAll('*')].find(e => e.textContent.trim() === '轻MES'); if (el) { el.click(); return true; } return null; })()`, ctxId)
      if (hit === true) { mes = true; break }
    }
    await sleep(1500)
  }
  if (!mes) throw new Error('登录流程未点到「轻MES」')
  console.log('[login] 已选轻MES，等待门户')
  for (let i = 0; i < 80; i++) {
    const tree = await cdp.send('Page.getFrameTree')
    if (tree.frameTree.frame.url.includes('h2t.chanjet.com')) break
    await sleep(1500)
  }
  await sleep(4000)
}

async function gotoVoucher(cdp, url) {
  console.log('[page] 打开 ' + url)
  await cdp.send('Page.navigate', { url })
  let ready = false
  for (let i = 0; i < 90; i++) {
    const r = await cdp.evalMain(`!!(document.querySelector('col[t]') || document.querySelector('table'))`)
    if (r === true) { ready = true; break }
    await sleep(1500)
  }
  if (!ready) console.log('[page] 警告：未等到表格 DOM')
  await sleep(4000)
  return ready
}

async function extract(cdp) {
  const expr = `(() => {
    const cs = (el) => getComputedStyle(el)
    const R = (el) => { const r = el.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) } }
    const out = { url: location.href, title: document.title }
    out.body = { bg: cs(document.body).backgroundColor, font: cs(document.body).fontFamily, fs: cs(document.body).fontSize }
    out.cols = [...document.querySelectorAll('col[t]')].map(c => ({ t: c.getAttribute('t'), a: c.getAttribute('a'), hidden: (c.getAttribute('flag')||'')==='myhidden', w: c.getAttribute('width')||'' }))
    out.ths = [...document.querySelectorAll('th')].slice(0,40).map(th => { const s = cs(th); return { text: th.innerText.trim(), bg: s.backgroundColor, color: s.color, fs: s.fontSize, h: th.getBoundingClientRect().height, w: th.getBoundingClientRect().width } })
    const firstTd = document.querySelector('tbody td, tr td')
    out.rowH = firstTd ? firstTd.getBoundingClientRect().height : 0
    out.rowCount = document.querySelectorAll('tbody tr').length
    out.toolbar = [...document.querySelectorAll('*')].filter(e => e.children.length === 0 && (e.className||'').toString().includes('tb-text') && R(e).y >= 0 && R(e).y < 40 && e.innerText && e.innerText.trim().length > 0 && e.innerText.trim().length < 20).map(e => e.innerText.trim())
    out.tabs = [...document.querySelectorAll('*')].filter(e => e.children.length === 0 && R(e).y >= 70 && R(e).y < 130 && R(e).w < 200 && cs(e).fontWeight === '700' && cs(e).fontSize === '14px' && e.innerText && e.innerText.trim().length > 0 && e.innerText.trim().length < 20).map(e => ({ text: e.innerText.trim(), color: cs(e).color }))
    out.query = [...document.querySelectorAll('label')].slice(0,20).map(l => { const inp = l.parentElement ? l.parentElement.querySelector('input') : null; const lr = R(l); if (lr.y < 0 || lr.h === 0) return null; return { label: l.innerText.trim(), x: lr.x, y: lr.y, w: lr.w, h: lr.h, inW: inp ? R(inp).w : 0, inH: inp ? R(inp).h : 0 } }).filter(Boolean)
    out.strips = [...document.querySelectorAll('div,span,table,td')].filter(e => R(e).y >= 0 && R(e).y < 140 && R(e).h >= 20 && R(e).w > 200).slice(0,12).map(e => ({ rect: R(e), bg: cs(e).backgroundColor, tag: e.tagName, cls: (e.className||'').toString().slice(0,40) }))
    out.topText = (document.body.innerText || '').slice(0, 400)
    return JSON.stringify(out)
  })()`
  return JSON.parse(await cdp.evalMain(expr))
}

async function screenshot(cdp, args, file) {
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: args.width, height: args.height, deviceScaleFactor: 1, mobile: false })
  const shot = await cdp.send('Page.captureScreenshot', { format: 'png' })
  fs.writeFileSync(file, Buffer.from(shot.data, 'base64'))
  console.log('[shot] ' + file)
}

async function main() {
  const args = parseArgs()
  await ensureBrowser(args)
  const cdp = await connectCdp(args.port)
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  if (args.login) await loginFlow(cdp, args)
  await gotoVoucher(cdp, args.url)
  fs.mkdirSync(args.out, { recursive: true })
  const base = path.join(args.out, args.name)
  const dom = await extract(cdp)
  fs.writeFileSync(base + '.dom.json', JSON.stringify(dom, null, 2))
  console.log('[dom] ' + base + '.dom.json')
  await screenshot(cdp, args, base + '.png')
  if (!args.noGen) {
    const { genHtml } = require('./gen.cjs')
    const html = genHtml(dom)
    fs.writeFileSync(base + '.html', html)
    console.log('[gen] ' + base + '.html')
  }
  console.log('[done] 后续：vision_html_screenshot 渲染 ' + base + '.html  → vision_pixel_diff 对比 ' + base + '.png')
  cdp.ws.close()
}

main().catch((e) => { console.error('[FAILED]', e.message); process.exit(1) })