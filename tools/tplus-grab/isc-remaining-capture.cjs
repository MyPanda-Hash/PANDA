#!/usr/bin/env node
/**
 * isc-remaining-capture.cjs —— 智能供应链剩余页面只读补抓（菜单驱动，无第三方依赖，Node >= 21）
 *
 * 依据 tools/tplus-grab/README.md 规范：
 *   - 会话复用 tools/tplus-grab/profile（port 9222）；失效时自动走「立即体验 → 机械行业」登录；
 *   - 只点左侧主导航菜单打开页面，读取 DOM 并截图；不点任何保存/审核/生单/删除等写操作按钮；
 *   - URL 中的 x_sessionId / TaskSessionID 等会话参数统一脱敏为 [REDACTED]。
 *
 * 用法：
 *   node tools/tplus-grab/isc-remaining-capture.cjs [--list <remaining.json>] [--out <dir>] [--port 9222] [--only <code>] [--fresh-login]
 *
 * 输入 JSON：{ vouchers:[{grp,code,text}], reports:[{grp,code,text}] }
 * 输出：<out>/<CODE>-<text>.dom.json + .png；<out>/capture-summary.json（可重复运行续抓）
 */
const { spawn } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const args = {
  port: 9222,
  out: path.resolve('docs/ref/tplus-live/mech-20260901/isc'),
  list: path.resolve('docs/ref/tplus-live/mech-20260901/isc/remaining-isc.json'),
  only: '',
  freshLogin: false,
}
for (let i = 2; i < process.argv.length; i++) {
  const k = process.argv[i]
  if (k === '--list' || k === '--out' || k === '--port' || k === '--only') args[k.slice(2)] = process.argv[++i]
  else if (k === '--fresh-login') args.freshLogin = true
  else throw new Error('未知参数: ' + k)
}
args.port = Number(args.port)
fs.mkdirSync(args.out, { recursive: true })

const REDACT = (u) => String(u || '').replace(/([?&](?:x_sessionId|TaskSessionID|token|pwd|sid)=)[^&]*/gi, '$1[REDACTED]')

function findEdge() {
  return [
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  ].find((p) => fs.existsSync(p))
}

async function ensureBrowser() {
  try {
    const res = await fetch(`http://127.0.0.1:${args.port}/json/version`)
    if (res.ok) return console.log('[browser] 复用已运行 Edge (port ' + args.port + ')')
  } catch {}
  const edge = findEdge()
  if (!edge) throw new Error('未找到 msedge.exe')
  const profile = path.join(__dirname, 'profile')
  fs.mkdirSync(profile, { recursive: true })
  console.log('[browser] 启动无头 Edge (port ' + args.port + ')')
  const child = spawn(edge, [
    '--headless=new', `--remote-debugging-port=${args.port}`, `--user-data-dir=${profile}`,
    '--no-first-run', '--no-default-browser-check', '--disable-gpu',
    '--window-size=1600,1000', '--force-device-scale-factor=1', 'about:blank',
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

async function connectTarget(target) {
  const ws = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
  let msgId = 0
  const pending = new Map()
  const contexts = new Map()
  const dialogLog = []
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data)
    if (msg.id && pending.has(msg.id)) {
      const p = pending.get(msg.id); pending.delete(msg.id)
      msg.error ? p.reject(new Error(msg.error.message)) : p.resolve(msg.result)
    } else if (msg.method === 'Runtime.executionContextCreated') {
      const c = msg.params.context
      if (c.auxData && c.auxData.frameId) contexts.set(c.auxData.frameId, c.id)
    } else if (msg.method === 'Page.javascriptDialogOpening') {
      // 会话过期/提示类 alert 会阻塞渲染主线程，自动关闭（CDP 要求整型 id）
      dialogLog.push(msg.params && msg.params.message)
      console.log('[dialog] 自动关闭: ' + ((msg.params && msg.params.message) || '').slice(0, 80))
      ws.send(JSON.stringify({ id: --msgId, method: 'Page.handleJavaScriptDialog', params: { accept: false } }))
    }
  }
  const send = (method, params = {}, timeout = 20000) => new Promise((resolve, reject) => {
    const id = ++msgId
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(method + ' timeout')) }, timeout)
    pending.set(id, { resolve: (v) => { clearTimeout(timer); resolve(v) }, reject })
    ws.send(JSON.stringify({ id, method, params }))
  })
  const evalIn = async (expression, contextId, timeout = 20000) => {
    const r = await send('Runtime.evaluate', { expression, contextId, returnByValue: true, awaitPromise: true, timeout }, timeout + 5000)
    if (r.exceptionDetails) throw new Error('eval: ' + (r.exceptionDetails.exception?.description || r.exceptionDetails.text || '').slice(0, 250))
    return r.result.value
  }
  // 等渲染进程可响应（重负载门户页加载期间 evaluate 可能长时间超时）
  const waitResponsive = async (contextId, attempts = 60) => {
    for (let i = 0; i < attempts; i++) {
      try { if ((await evalIn('1+1', contextId, 4000)) === 2) return true } catch {}
      await sleep(1000)
    }
    return false
  }
  const frameTree = async () => (await send('Page.getFrameTree')).frameTree
  const allFrames = async () => {
    const out = []
    const walk = (n) => { out.push(n.frame); for (const c of n.childFrames || []) walk(c) }
    walk(await frameTree())
    return out
  }
  const ctxFor = async (frame) => {
    let cid = contexts.get(frame.id)
    if (!cid) {
      const w = await send('Page.createIsolatedWorld', { frameId: frame.id, worldName: 'isccap-' + frame.id, grantUniveralAccess: true })
      cid = w.executionContextId
      contexts.set(frame.id, cid)
    }
    return cid
  }
  return { ws, target, send, evalIn, contexts, frameTree, allFrames, ctxFor, waitResponsive }
}

async function pageTargets(port) {
  try {
    const res = await fetch(`http://127.0.0.1:${port}/json/list`)
    return (await res.json()).filter((t) => t.type === 'page')
  } catch { return [] }
}

const EXTRACT = `(() => {
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
  out.buttons = [...document.querySelectorAll('a,button,div[class*=btn],span[class*=btn],input[type=button]')].filter(e => { const r = R(e); return r.w > 0 && r.h > 0 && r.y >= 0 && e.innerText && e.innerText.trim().length > 0 && e.innerText.trim().length < 12 }).slice(0, 40).map(e => ({ text: e.innerText.trim(), cls: (e.className||'').toString().slice(0, 50) }))
  out.topText = (document.body.innerText || '').slice(0, 400)
  return JSON.stringify(out)
})()`

async function loginFlow(cdp) {
  console.log('[login] 会话失效，走「立即体验 → 机械行业」登录')
  await cdp.send('Page.navigate', { url: 'https://t.chanjet.com/tplus/view/login.html' })
  await sleep(6000)
  await cdp.waitResponsive(undefined, 30)
  const clicked = await cdp.evalIn(`(() => { const b = document.querySelector('#expBtn') || [...document.querySelectorAll('button,a,div')].find(e => e.textContent.includes('立即体验')); if (b) { b.click(); return true } return false })()`, undefined)
  console.log('[login] 立即体验: ' + clicked)
  let chosen = false
  for (let i = 0; i < 40 && !chosen; i++) {
    const frames = await cdp.allFrames()
    for (const f of frames) {
      if (!f.url.includes('selectRoles')) continue
      const cid = await cdp.ctxFor(f)
      const hit = await cdp.evalIn(`(() => { const p = document.querySelector('#machine'); if (!p) return null; p.click(); return p.textContent.trim() || '机械行业' })()`, cid).catch(() => null)
      if (hit) { chosen = true; console.log('[login] 已选「' + hit + '」'); break }
    }
    if (!chosen) await sleep(1500)
  }
  if (!chosen) throw new Error('登录流程未找到机械行业入口 #machine')
  for (let i = 0; i < 80; i++) {
    const url = (await cdp.frameTree()).frame.url
    if (url.includes('h4t.chanjet.com') && url.includes('/tplus/view/portal')) { console.log('[login] 已进入机械行业门户'); await sleep(5000); return }
    await sleep(1500)
  }
  throw new Error('登录后未进入 h4t 门户')
}

async function openPortal(cdp) {
  console.log('[portal] 打开门户')
  await cdp.send('Page.navigate', { url: 'https://h4t.chanjet.com/tplus/view/portal/portal.html?_from=exp' })
  for (let i = 0; i < 60; i++) {
    const url = (await cdp.frameTree().catch(() => ({ frame: { url: '' } }))).frame.url || ''
    if (url.includes('login.html') || url === 'about:blank#') { await loginFlow(cdp); return openPortal(cdp) }
    if (url.includes('portal/portal.html')) break
    await sleep(1500)
  }
  await cdp.waitResponsive(undefined, 90)
  await sleep(4000)
  const body = await cdp.evalIn(`(document.body && document.body.innerText) || ''`, undefined).catch(() => '')
  if (body.includes('长时间未操作') || body.includes('另外地点登录')) throw new Error('演示账号会话冲突：稍后重试或重新登录')
  if (body.includes('消息通知')) {
    await cdp.evalIn(`(() => { const b = [...document.querySelectorAll('a,span,div')].find(e => (e.textContent || '').trim() === '×'); if (b) b.click(); })()`, undefined).catch(() => {})
    await sleep(800)
  }
}

// frame 内元素坐标 → 页面坐标：逐层累加同源 iframe 偏移；若目标就是主文档则直接使用局部坐标
async function clickInFrameAt(cdp, frameUrlPrefix, localX, localY) {
  const off = JSON.parse(await cdp.evalIn(`(() => {
    const prefix = ${JSON.stringify(frameUrlPrefix)}
    const localXIn = ${JSON.stringify(localX)}, localYIn = ${JSON.stringify(localY)}
    if (location.href.slice(0, 80) === prefix.slice(0, 80)) return JSON.stringify({ x: localXIn, y: localYIn, main: true })
    let found = null
    const walk = (win, ox, oy, depth) => {
      if (found || depth > 5) return
      let doc = null
      try { doc = win.document } catch (e) { return }
      if (!doc) return
      for (const f of doc.querySelectorAll('iframe')) {
        let href = ''
        try { href = f.contentWindow.location.href } catch (e) { continue }
        const r = f.getBoundingClientRect()
        if (href.slice(0, 80) === prefix.slice(0, 80)) { found = { x: ox + r.x + localXIn, y: oy + r.y + localYIn }; return }
        walk(f.contentWindow, ox + r.x, oy + r.y, depth + 1)
        if (found) return
      }
    }
    walk(window, 0, 0, 0)
    return JSON.stringify(found || { x: -1, y: -1 })
  })()`, undefined, 15000))
  if (off.x < 0) throw new Error('未定位到 frame 偏移: ' + frameUrlPrefix.slice(0, 50))
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: Math.round(off.x), y: Math.round(off.y) })
  await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: Math.round(off.x), y: Math.round(off.y), button: 'left', clickCount: 1 })
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: Math.round(off.x), y: Math.round(off.y), button: 'left', clickCount: 1 })
}

// 在导航 frame 中定位菜单锚点（先 scrollIntoView），返回 frame 内局部坐标
async function locateAnchor(cdp, navCtx, code, text) {
  const pos = JSON.parse(await cdp.evalIn(`(() => {
    const v = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).display !== 'none' && getComputedStyle(e).visibility !== 'hidden' }
    const code = ${JSON.stringify(code)}, text = ${JSON.stringify(text)}
    let el = [...document.querySelectorAll('a')].find((a) => (a.getAttribute('code') || '').replace(/^#/, '') === code && v(a))
    if (!el) el = [...document.querySelectorAll('a,span,div,li')].find((e) => v(e) && (e.textContent || '').trim() === text)
    if (!el) return 'null'
    el.scrollIntoView({ block: 'nearest' })
    const r = el.getBoundingClientRect()
    return JSON.stringify({ x: r.x + r.width / 2, y: r.y + r.height / 2 })
  })()`, navCtx, 15000))
  return pos === 'null' ? null : pos
}

async function captureInFrame(cdp, frame, item) {
  const cid = await cdp.ctxFor(frame)
  await cdp.waitResponsive(cid, 20)
  for (let i = 0; i < 12; i++) {
    try {
      const ok = JSON.parse(await cdp.evalIn(`(() => {
        const t = (document.body && document.body.innerText) || ''
        return JSON.stringify({ dom: !!(document.querySelector('col[t]') || document.querySelector('table') || document.querySelector('label')), len: t.length, busy: /加载中|正在加载/i.test(t.slice(0, 120)) })
      })()`, cid, 8000))
      if ((ok.dom || ok.len > 150) && !ok.busy) break
    } catch {}
    await sleep(1000)
  }
  await sleep(3000)
  const dom = JSON.parse(await cdp.evalIn(EXTRACT, cid, 25000))
  const record = {
    capturedAt: new Date().toISOString(),
    readOnly: true,
    code: item.code,
    label: item.text,
    group: item.grp,
    url: REDACT(dom.url || frame.url),
    frameUrl: REDACT(frame.url),
    page: dom,
  }
  const base = path.join(args.out, item.code + '-' + item.text)
  fs.writeFileSync(base + '.dom.json', JSON.stringify(record, null, 2))
  const shot = await cdp.send('Page.captureScreenshot', { format: 'png' }, 25000)
  fs.writeFileSync(base + '.png', Buffer.from(shot.data, 'base64'))
  return base
}

// 直连 URL 兜底（规范内 Voucher.aspx 模式）：新开 tab 抓取后即关闭，不影响门户
async function captureViaDirectUrl(cdp, item, url) {
  const created = await fetch(`http://127.0.0.1:${args.port}/json/new?${encodeURIComponent(url)}`, { method: 'PUT' }).then((r) => r.json()).catch(() => null)
  if (!created || !created.id) throw new Error('兜底新开 tab 失败')
  const tabCdp = await connectTarget(created)
  await tabCdp.send('Page.enable'); await tabCdp.send('Runtime.enable')
  try {
    for (let i = 0; i < 30; i++) {
      const u = (await tabCdp.frameTree().catch(() => ({ frame: { url: '' } }))).frame.url || ''
      if (u.includes('Voucher.aspx') && !u.includes('login.html')) break
      await sleep(1000)
    }
    await tabCdp.waitResponsive(undefined, 30)
    const frame = (await tabCdp.frameTree()).frame
    const base = await captureInFrame(tabCdp, frame, item)
    // 校验产物不是过期/错误页
    const record = JSON.parse(fs.readFileSync(base + '.dom.json', 'utf8'))
    const top = (record.page && record.page.topText) || ''
    if (/会话已过期|请重新登录|HTTP 404|无法显示|错误/.test(top) && !top.includes(item.text)) {
      fs.rmSync(base + '.dom.json', { force: true }); fs.rmSync(base + '.png', { force: true })
      throw new Error('直连页面无效: ' + top.slice(0, 60))
    }
  } finally {
    try { tabCdp.ws.close() } catch {}
    await fetch(`http://127.0.0.1:${args.port}/json/close/${created.id}`).catch(() => {})
  }
}

// 修剪门户内较早打开的内容 iframe（保留最新 3 个与导航 frame），防止上百页签拖垮内存
async function prunePortalFrames(cdp, navFrameUrlPrefix) {
  try {
    await cdp.evalIn(`(() => {
      const navPrefix = ${JSON.stringify(navFrameUrlPrefix)}
      const collect = (win, out, depth) => {
        if (depth > 5) return out
        let doc = null
        try { doc = win.document } catch (e) { return out }
        if (!doc) return out
        for (const f of [...doc.querySelectorAll('iframe')]) {
          let href = ''
          try { href = f.contentWindow.location.href } catch (e) { continue }
          if (href.includes('/tplus/') && !href.includes('portal/portal.html') && !navPrefix.split('?')[0].split('/').some(() => false) && !href.startsWith(navPrefix.slice(0, 60))) out.push({ f, href })
          collect(f.contentWindow, out, depth + 1)
        }
        return out
      }
      const frames = collect(window, [], 0)
      const content = frames.filter((x) => !x.href.startsWith(navPrefix.slice(0, 60)))
      const kill = content.slice(0, Math.max(0, content.length - 3))
      for (const { f } of kill) { try { f.remove() } catch (e) {} }
      return JSON.stringify({ total: content.length, removed: kill.length })
    })()`, undefined, 12000).catch(() => {})
  } catch {}
}

async function main() {
  const list = JSON.parse(fs.readFileSync(args.list, 'utf8'))
  const items = [...(list.vouchers || []), ...(list.reports || [])].filter((it) => !args.only || it.code === args.only)
  console.log('[list] 待抓取 ' + items.length + ' 页')
  const summaryPath = path.join(args.out, 'capture-summary.json')
  // 汇总跨轮合并：以已存在产物为准恢复 ok 基线，避免单页重试覆盖历史
  const summary = { ok: [], fail: [], startedAt: new Date().toISOString() }
  try {
    const prev = JSON.parse(fs.readFileSync(summaryPath, 'utf8'))
    summary.fail = prev.fail || []
    for (const it of items) {
      if (fs.existsSync(path.join(args.out, it.code + '-' + it.text + '.dom.json'))) summary.ok.push({ ...it, skip: true })
    }
  } catch {}

  await ensureBrowser()
  let targets = await pageTargets(args.port)
  let target = targets.find((t) => t.url.includes('portal') || t.url.includes('chanjet')) || targets[0]
  if (!target) throw new Error('无页面 target')
  let cdp
  for (let i = 0; i < 3; i++) {
    try {
      cdp = await connectTarget(target)
      await cdp.send('Page.enable'); await cdp.send('Runtime.enable')
      break
    } catch (e) {
      console.log('[warn] 目标 tab 不可用(' + e.message + ')，改用新空白 tab')
      try { cdp && cdp.ws.close() } catch {}
      const created = await fetch(`http://127.0.0.1:${args.port}/json/new?about:blank`, { method: 'PUT' }).then((r) => r.json())
      target = created
    }
  }
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false })

  await openPortal(cdp)
  if (args.freshLogin) { await loginFlow(cdp); await openPortal(cdp) }

  // 展开智能供应链
  const clicked = await cdp.evalIn(`(() => {
    const el = document.querySelector('li.menufist_ISC') || [...document.querySelectorAll('li,div,span,a')].find((e) => (e.textContent || '').trim() === '智能供应链' && e.getBoundingClientRect().y >= 0 && e.getBoundingClientRect().y < 120)
    if (!el) return false
    el.click(); return true
  })()`, undefined, 15000).catch(() => false)
  if (!clicked) throw new Error('未找到智能供应链一级菜单')
  await sleep(3000)

  // 找包含菜单锚点(code 属性)的导航 frame
  let navFrame = null, navCtx = null
  for (let round = 0; round < 3 && !navFrame; round++) {
    const frames = await cdp.allFrames()
    for (const f of frames) {
      if (!f.url) continue
      try {
        const cid = await cdp.ctxFor(f)
        const has = await cdp.evalIn(`!![...document.querySelectorAll('a')].find((a) => a.getAttribute('code'))`, cid, 8000).catch(() => false)
        if (has) { navFrame = f; navCtx = cid; break }
      } catch {}
    }
    if (!navFrame) {
      await cdp.evalIn(`(() => { const el = [...document.querySelectorAll('li,div,span')].find((e) => (e.textContent || '').trim().startsWith('智能供应链')); if (el) el.click(); })()`, undefined, 10000).catch(() => {})
      await sleep(2500)
    }
  }
  if (!navFrame) throw new Error('未找到包含菜单锚点的导航 frame')
  console.log('[nav] 菜单 frame: ' + navFrame.url.slice(0, 110))

  for (const item of items) {
    const base = path.join(args.out, item.code + '-' + item.text)
    if (fs.existsSync(base + '.dom.json')) { console.log('[skip] ' + item.code + ' ' + item.text); if (!summary.ok.some((o) => o.code === item.code)) summary.ok.push({ ...item, skip: true }); continue }
    const t0 = Date.now()
    // 会话被踢 / 导航 frame 丢失时重获
    if (!navFrame) {
      try {
        const u = (await cdp.frameTree()).frame.url || ''
        if (u.includes('login.html')) { await loginFlow(cdp); await openPortal(cdp) }
        await cdp.evalIn(`(() => { const el = document.querySelector('li.menufist_ISC') || [...document.querySelectorAll('li,div,span,a')].find((e) => (e.textContent || '').trim() === '智能供应链' && e.getBoundingClientRect().y < 120); if (el) { el.click(); return true } return false })()`, undefined, 15000)
        await sleep(2500)
        const frames = await cdp.allFrames()
        for (const f of frames) {
          try {
            const cid = await cdp.ctxFor(f)
            if (await cdp.evalIn(`!![...document.querySelectorAll('a')].find((a) => a.getAttribute('code'))`, cid, 8000).catch(() => false)) { navFrame = f; navCtx = cid; break }
          } catch {}
        }
      } catch (e) { console.log('[warn] 重获导航 frame 失败: ' + e.message) }
      if (!navFrame) { summary.fail.push({ ...item, reason: '导航 frame 不可用' }); continue }
    }
    try {
      const framesBefore = new Set((await cdp.allFrames()).map((f) => f.url))
      const targetsBefore = new Set((await pageTargets(args.port)).map((t) => t.id))

      // 1) 直点菜单锚点：门户主文档预载全部模块菜单（隐藏但可编程点击）
      let menuErr = null
      let captured = false
      try {
        await cdp.evalIn(`(() => { const el = document.querySelector('li.menufist_ISC'); if (el) el.click(); })()`, navCtx, 10000).catch(() => {})
        await sleep(600)
        const clicked = await cdp.evalIn(`(() => {
          const a = [...document.querySelectorAll('a')].find((x) => (x.getAttribute('code') || '').replace(/^#/, '') === ${JSON.stringify(item.code)})
          if (!a) return 'no'
          a.click()
          return 'ok'
        })()`, navCtx, 15000)
        if (clicked !== 'ok') throw new Error('菜单锚点缺失: ' + item.code)

        // 2) 等待新页面出现（frame 或新 tab）；标记匹配不到时退回“任意新 frame”
        for (let i = 0; i < 15 && !captured; i++) {
          const newFrames = (await cdp.allFrames()).filter((f) => f.url && !framesBefore.has(f.url) && !f.url.includes('portal.html'))
          const marked = newFrames.find((f) => f.url.includes('mId=' + item.code) || f.url.includes('taskID=' + encodeURIComponent(item.text)) || f.url.includes(encodeURIComponent(item.text)) || f.url.includes('ReportName='))
          if (i >= 10 && newFrames.length) { await captureInFrame(cdp, newFrames[0], item); captured = true; break }
          if (marked) { await captureInFrame(cdp, marked, item); captured = true; break }
          const pageTargetsNow = await pageTargets(args.port)
          const newTab = pageTargetsNow.find((t) => !targetsBefore.has(t.id))
          if (newTab) {
            const tabCdp = await connectTarget(newTab)
            await tabCdp.send('Page.enable'); await tabCdp.send('Runtime.enable')
            try {
              const cid = await tabCdp.ctxFor((await tabCdp.frameTree()).frame)
              await tabCdp.waitResponsive(cid, 30)
              await sleep(3000)
              const dom = JSON.parse(await tabCdp.evalIn(EXTRACT, cid, 25000))
              const record = { capturedAt: new Date().toISOString(), readOnly: true, code: item.code, label: item.text, group: item.grp, url: REDACT(dom.url || newTab.url), frameUrl: REDACT(newTab.url), page: dom }
              fs.writeFileSync(base + '.dom.json', JSON.stringify(record, null, 2))
              await tabCdp.send('Emulation.setDeviceMetricsOverride', { width: 1600, height: 1000, deviceScaleFactor: 1, mobile: false })
              const shot = await tabCdp.send('Page.captureScreenshot', { format: 'png' }, 25000)
              fs.writeFileSync(base + '.png', Buffer.from(shot.data, 'base64'))
              tabCdp.ws.close()
              await fetch(`http://127.0.0.1:${args.port}/json/close/${newTab.id}`).catch(() => {})
              captured = true
              break
            } catch (e) { try { tabCdp.ws.close() } catch {} throw new Error('新 tab 抓取失败: ' + e.message) }
          }
          await sleep(1000)
        }
      } catch (e) { menuErr = e }

      if (!captured) {
        // 规范内直连 URL 兜底（仅单据类编码）；报表类保持失败原因
        if ((list.vouchers || []).some((v) => v.code === item.code)) {
          const sysId = (item.code.match(/^[A-Za-z]+/) || [item.code.slice(0, 2)])[0]
          const url = `https://h4t.chanjet.com/tplus/BAPView/Voucher.aspx?sysId=${sysId}&mId=${item.code}&pId=voucherView`
          console.log('[fallback] 直连: ' + url + (menuErr ? ' (菜单路径: ' + menuErr.message + ')' : ''))
          await captureViaDirectUrl(cdp, item, url)
          captured = true
        } else throw menuErr || new Error('点击后未出现目标页面 frame')
      }
      if (captured) {
        console.log('[ok] ' + item.code + ' ' + item.text + ' (' + Math.round((Date.now() - t0) / 1000) + 's)')
        if (!summary.ok.some((o) => o.code === item.code)) summary.ok.push(item)
        summary.fail = summary.fail.filter((f) => f.code !== item.code)
        await prunePortalFrames(cdp, navFrame.url)
      }
    } catch (e) {
      console.log('[fail] ' + item.code + ' ' + item.text + ' :: ' + e.message)
      summary.fail.push({ ...item, reason: e.message })
      // 会话被踢时中止
      try {
        const u = (await cdp.frameTree()).frame.url || ''
        if (u.includes('login.html')) { await loginFlow(cdp); await openPortal(cdp); navFrame = null }
      } catch {}
    }
    fs.writeFileSync(path.join(args.out, 'capture-summary.json'), JSON.stringify({ ...summary, finishedAt: new Date().toISOString() }, null, 2))
  }
  console.log('[done] 成功 ' + summary.ok.length + ' / 失败 ' + summary.fail.length)
  try { cdp.ws.close() } catch {}
  process.exit(0)
}

main().catch((e) => { console.error('[FAILED]', e.message); process.exit(1) })
