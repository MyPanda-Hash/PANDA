#!/usr/bin/env node
/**
 * sale-order-e2e.cjs —— 机械行业「销售订单」真实交互 E2E
 *
 * 流程（全部真实浏览器操作，CDP 无第三方依赖，Node >= 21）：
 *   用户登录（立即体验 → 机械行业 → h4t 门户）
 *   → 打开销售订单（BAPView sysId=SA&mId=SA03）
 *   → 列表截图 + 结构提取
 *   → 点击「新增」→ 表单截图
 *   → 填写表头（客户参照弹窗选第一行、订单日期、业务类型、预计交货日期）
 *   → 填写明细首行（存货名称参照弹窗选第一行、数量、单价）
 *   → 点击「保存」→ 结果截图
 *
 * 用法：
 *   node tools/tplus-grab/sale-order-e2e.cjs [--port 9222] [--out docs/ref/mes-live/so-e2e]
 *
 * 产物：<out>-00-portal.png / 01-list.png / 02-form.png / 03-dialog.png / 04-filled.png / 05-saved.png + <out>.json
 */
const { spawn } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function parseArgs() {
  const argv = process.argv.slice(2)
  const args = { port: 9222, out: path.resolve('docs/ref/mes-live/so-e2e'), width: 1600, height: 1000, profile: path.join(__dirname, 'profile') }
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i]
    if (k === '--port' || k === '--out' || k === '--profile' || k === '--width' || k === '--height') { args[k.slice(2)] = argv[++i]; continue }
    throw new Error('未知参数: ' + k)
  }
  args.port = Number(args.port); args.width = Number(args.width); args.height = Number(args.height)
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
  try {
    const res = await fetch(`http://127.0.0.1:${args.port}/json/version`)
    if (res.ok) { console.log('[browser] 复用已在运行的 Edge (port ' + args.port + ')'); return }
  } catch {}
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
      if (res.ok) { console.log('[browser] Edge 已就绪'); return }
    } catch {}
    await sleep(1000)
  }
  throw new Error('Edge 启动超时')
}

async function listPageTargets(port) {
  const res = await fetch(`http://127.0.0.1:${port}/json/list`)
  const targets = await res.json()
  return targets.filter((t) => t.type === 'page')
}

async function connectCdp(port, targetId) {
  let wsUrl
  for (let i = 0; i < 30; i++) {
    try {
      const pages = await listPageTargets(port)
      const page = targetId ? pages.find((t) => t.id === targetId) : pages[0]
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
  ws.onclose = () => {
    for (const p of pending.values()) p.reject(new Error('CDP WebSocket 已关闭（目标页可能被替换）'))
    pending.clear()
  }
  ws.onerror = () => {
    for (const p of pending.values()) p.reject(new Error('CDP WebSocket 错误'))
    pending.clear()
  }
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++msgId; pending.set(id, { resolve, reject })
    ws.send(JSON.stringify({ id, method, params }))
  })
  const evalIn = async (expression, contextId) => {
    const r = await send('Runtime.evaluate', { expression, contextId, returnByValue: true, awaitPromise: true, timeout: 30000 })
    if (r.exceptionDetails) throw new Error('eval 异常: ' + JSON.stringify(r.exceptionDetails).slice(0, 200))
    return r.result ? r.result.value : undefined
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
  const allFrames = async () => {
    const tree = await send('Page.getFrameTree')
    const out = []
    const walk = (n) => { out.push(n.frame); for (const c of n.childFrames || []) walk(c) }
    walk(tree.frameTree)
    return out
  }
  return { ws, send, evalIn, evalMain: (e) => evalIn(e, undefined), frameWith, allFrames, contexts }
}

// ---------- 登录（用户方式：立即体验 → 机械行业） ----------
async function loginFlow(cdp) {
  console.log('[login] 打开登录页 t.chanjet.com')
  await cdp.send('Page.navigate', { url: 'https://t.chanjet.com/tplus/view/login.html' })
  await sleep(7000)
  const clicked = await cdp.evalMain(`(() => { const b = document.querySelector('#expBtn') || [...document.querySelectorAll('button,a,div')].find(e => e.textContent.includes('立即体验')); if (b) { b.click(); return true; } return false; })()`)
  console.log('[login] 点击「立即体验」: ' + clicked)
  let chosen = false
  for (let i = 0; i < 40; i++) {
    const fr = await cdp.frameWith('selectRoles')
    if (fr) {
      let ctxId = cdp.contexts.get(fr.id)
      if (!ctxId) {
        const w = await cdp.send('Page.createIsolatedWorld', { frameId: fr.id, worldName: 'tplus-probe', grantUniveralAccess: true })
        ctxId = w.executionContextId
      }
      const hit = await cdp.evalIn(`(() => {
        const pick = ['机械行业', '轻MES'].map(t => [...document.querySelectorAll('*')].find(e => e.textContent.trim() === t)).find(Boolean)
        if (pick) { pick.click(); return pick.textContent.trim(); }
        return null;
      })()`, ctxId)
      if (hit) { chosen = true; console.log('[login] 已选「' + hit + '」，等待门户'); break }
    }
    await sleep(1500)
  }
  if (!chosen) throw new Error('登录流程未点到目标行业（机械行业/轻MES）')
  for (let i = 0; i < 90; i++) {
    const tree = await cdp.send('Page.getFrameTree')
    if (tree.frameTree.frame.url.includes('chanjet.com/tplus/view/portal')) break
    await sleep(1500)
  }
  await sleep(5000)
  const tree = await cdp.send('Page.getFrameTree')
  console.log('[login] 门户: ' + tree.frameTree.frame.url)
}

// ---------- 通用 DOM 工具 ----------
async function rectOf(cdp, expr) {
  return cdp.evalMain(`(() => { const el = (${expr}); if (!el) return null; const r = el.getBoundingClientRect(); return { x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), x1: Math.round(r.x), y1: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height), text: (el.innerText||el.value||'').trim().slice(0, 40) }; })()`)
}

async function mouseClick(cdp, x, y, label) {
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y })
  await sleep(120)
  await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 })
  await sleep(100)
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 })
  console.log('[click] ' + label + ' @' + x + ',' + y)
}

async function typeText(cdp, text) {
  await cdp.send('Input.insertText', { text })
  console.log('[type] "' + text + '"')
}

async function pressKey(cdp, key) {
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key })
  await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key })
}

async function screenshot(cdp, args, file) {
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: args.width, height: args.height, deviceScaleFactor: 1, mobile: false })
  const shot = await cdp.send('Page.captureScreenshot', { format: 'png' })
  fs.mkdirSync(path.dirname(file), { recursive: true })
  fs.writeFileSync(file, Buffer.from(shot.data, 'base64'))
  console.log('[shot] ' + file)
}

async function dumpState(cdp, tag) {
  const expr = `(() => {
    const cs = (el) => getComputedStyle(el)
    const R = (el) => { const r = el.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) } }
    const vis = (el) => { const r = el.getBoundingClientRect(); const s = cs(el); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' }
    const out = { url: location.href, title: document.title }
    out.toolbar = [...document.querySelectorAll('[class*="tb-text"]')].filter(e => vis(e) && e.innerText && e.innerText.trim().length > 0 && e.innerText.trim().length < 20).map(e => { const r = R(e); return { t: e.innerText.trim(), x: r.x, y: r.y } })
    out.ctrls = [...document.querySelectorAll('label')].map(l => {
      if (!vis(l)) return null
      const txt = l.textContent.trim()
      if (!txt) return null
      let box = l
      for (let i = 0; i < 5 && box.parentElement; i++) { box = box.parentElement; if (box.querySelector('input,select,textarea,button')) break }
      const inp = box.querySelector('input,select,textarea')
      const r = R(box)
      if (r.y < 0 || r.h === 0) return null
      return { label: txt, val: inp ? inp.value : '', type: inp ? inp.tagName + (inp.getAttribute('type') ? ':' + inp.getAttribute('type') : '') : '', y: r.y, x: r.x, w: r.w, h: r.h }
    }).filter(Boolean).slice(0, 40)
    out.dialogs = [...document.querySelectorAll('div')].filter(e => {
      if (!vis(e)) return false
      const r = R(e)
      if (r.w < 300 || r.h < 120) return false
      const s = cs(e)
      if (s.position !== 'fixed' && s.position !== 'absolute') return false
      const txt = e.innerText || ''
      return txt.includes('确定') && e.querySelector('table')
    }).slice(0, 3).map(e => { const r = R(e); return { x: r.x, y: r.y, w: r.w, h: r.h, head: (e.innerText||'').slice(0, 60) } })
    const bt = document.body.innerText || ''
    out.bodyHead = bt.slice(0, 200)
    out.bodyTail = bt.slice(-200)
    return JSON.stringify(out)
  })()`
  const dom = JSON.parse(await cdp.evalMain(expr))
  console.log('=== [' + tag + '] ' + dom.title + ' | ' + dom.url.slice(0, 110))
  console.log('  工具栏: ' + dom.toolbar.map(t => t.t).join(' | ').slice(0, 260))
  console.log('  表头控件: ' + dom.ctrls.map(c => c.label + (c.val ? '=' + c.val : '') + '[' + c.type + ']').join(' | ').slice(0, 400))
  if (dom.dialogs.length) console.log('  弹窗: ' + dom.dialogs.map(d => d.head).join(' || '))
  return dom
}

async function toolbarBtn(cdp, text) {
  return rectOf(cdp, `[...document.querySelectorAll('[class*="tb-text"]')].find(e => e.innerText && e.innerText.trim() === '${text}' && e.getBoundingClientRect().width > 0 && e.getBoundingClientRect().y < 60)`)
}

// 表头控件容器（vch-ctldiv），label 文本容忍 * 前缀；返回输入区点击点
async function ctrlBox(cdp, label) {
  return cdp.evalMain(`(() => {
    const labs = [...document.querySelectorAll('label')].filter(l => { const t = (l.textContent || '').trim().replace(/^\\*/, ''); return t === '${label}' && l.getBoundingClientRect().width > 0 })
    if (!labs.length) return null
    const l = labs[0]
    let box = l
    for (let i = 0; i < 6 && box.parentElement; i++) { box = box.parentElement; if (box.querySelector('input,select,textarea')) break }
    const r = box.getBoundingClientRect()
    const inp = box.querySelector('input:not([type=hidden]),input[type=hidden],select,textarea')
    const inRect = inp ? inp.getBoundingClientRect() : null
    const clickX = inRect && inRect.width > 0 ? Math.round(inRect.x + inRect.width / 2) : Math.round(r.x + r.width / 2)
    const clickY = inRect && inRect.height > 0 ? Math.round(inRect.y + inRect.height / 2) : Math.round(r.y + r.height - 6)
    return { x: clickX, y: clickY, x1: Math.round(r.x), y1: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height), val: inp ? inp.value : '', label: l.textContent.trim() }
  })()`)
}

// 等待可见参照/选择弹窗（含 确定 + 表格的网格弹窗，或固定定位下拉列表）
async function waitDialog(cdp, timeoutMs = 15000) {
  const t0 = Date.now()
  while (Date.now() - t0 < timeoutMs) {
    const d = await cdp.evalMain(`(() => {
      const cs = (el) => getComputedStyle(el)
      const vis = (el) => { const r = el.getBoundingClientRect(); const s = cs(el); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' }
      const el = [...document.querySelectorAll('div')].find(e => { if (!vis(e)) return false; const r = e.getBoundingClientRect(); if (r.w < 300 || r.h < 120) return false; const s = cs(e); if (s.position !== 'fixed' && s.position !== 'absolute') return false; const txt = e.innerText || ''; return txt.includes('确定') && e.querySelector('table') })
      if (el) { const r = el.getBoundingClientRect(); return { kind: 'grid', x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), head: (el.innerText||'').slice(0, 80) } }
      const items = [...document.querySelectorAll('div,li,span')].filter(e => { if (!vis(e)) return false; const r = e.getBoundingClientRect(); if (r.w < 60 || r.h < 20 || r.h > 40) return false; const s = cs(e); if (s.position !== 'fixed' && s.position !== 'absolute') return false; const t = (e.innerText||'').trim(); return t && t.length < 20 && e.children.length <= 1 })
      if (items.length >= 2) { const f = items[0]; const r = f.getBoundingClientRect(); return { kind: 'dropdown', x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2), head: (f.innerText||'').trim().slice(0, 40) } }
      return null
    })()`)
    if (d) return d
    await sleep(800)
  }
  return null
}

async function pickFirstRow(cdp) {
  const r = await cdp.evalMain(`(() => {
    const rows = [...document.querySelectorAll('tbody tr')].filter(tr => { const rr = tr.getBoundingClientRect(); return rr.width > 0 && rr.height > 0 && rr.y > 100 })
    const tr = rows[0]
    if (!tr) return null
    const rr = tr.getBoundingClientRect()
    return { x: Math.round(rr.x + 120), y: Math.round(rr.y + rr.height/2) }
  })()`)
  if (!r) { console.log('[dialog] 未找到可点数据行'); return false }
  await mouseClick(cdp, r.x, r.y, '弹窗首行')
  return true
}

async function clickDialogOk(cdp) {
  const r = await rectOf(cdp, `[...document.querySelectorAll('span,button,a,div')].find(e => e.children.length === 0 && e.innerText && e.innerText.trim() === '确定' && e.getBoundingClientRect().width > 0)`)
  if (!r) { console.log('[dialog] 未找到「确定」按钮'); return false }
  await mouseClick(cdp, r.x, r.y, '确定')
  return true
}

async function waitDialogGone(cdp, timeoutMs = 10000) {
  const t0 = Date.now()
  while (Date.now() - t0 < timeoutMs) {
    const d = await cdp.evalMain(`(() => {
      const cs = (el) => getComputedStyle(el)
      const vis = (el) => { const r = el.getBoundingClientRect(); const s = cs(el); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' }
      return [...document.querySelectorAll('div')].some(e => { if (!vis(e)) return false; const r = e.getBoundingClientRect(); if (r.w < 300 || r.h < 120) return false; const s = cs(e); if (s.position !== 'fixed' && s.position !== 'absolute') return false; const txt = e.innerText || ''; return txt.includes('确定') && e.querySelector('table') })
    })()`)
    if (!d) return true
    await sleep(600)
  }
  return false
}

// 参照字段：点控件 → 等弹窗 → 选首行/下拉首项 → 确定 → 等消失
async function fillRef(cdp, label) {
  console.log('[fillRef] ' + label)
  const c = await ctrlBox(cdp, label)
  if (!c) { console.log('[fillRef] 未找到控件 ' + label); return false }
  await mouseClick(cdp, c.x, c.y, label + ' 控件')
  await sleep(1500)
  const dlg = await waitDialog(cdp)
  if (!dlg) { console.log('[fillRef] 未等到弹窗（' + label + '）'); return false }
  console.log('[fillRef] 弹窗类型=' + dlg.kind + ' 内容: ' + dlg.head)
  if (dlg.kind === 'dropdown') {
    await mouseClick(cdp, dlg.x, dlg.y, label + ' 下拉首项')
    await pressKey(cdp, 'Enter')
  } else {
    await pickFirstRow(cdp)
    await sleep(800)
    await clickDialogOk(cdp)
    await waitDialogGone(cdp)
  }
  await sleep(1000)
  return true
}

// 日期文本字段填写
async function fillDate(cdp, label, value) {
  const c = await ctrlBox(cdp, label)
  if (!c) { console.log('[fillDate] 未找到控件 ' + label); return false }
  if (c.val) { console.log('[fillDate] ' + label + ' 已有值=' + c.val + '，跳过'); return true }
  await mouseClick(cdp, c.x, c.y, label)
  await sleep(400)
  await typeText(cdp, value)
  await pressKey(cdp, 'Enter')
  await sleep(600)
  return true
}

// 明细单元格（列名匹配，容忍 * 前缀与 <font> 标签）：返回首行该列单元格中心
async function detailCell(cdp, colName) {
  return cdp.evalMain(`(() => {
    const grid = [...document.querySelectorAll('table')].find(t => {
      const r = t.getBoundingClientRect()
      if (r.width < 800 || r.height < 100) return false
      const s = getComputedStyle(t)
      if (s.visibility === 'hidden' || s.display === 'none') return false
      return t.querySelectorAll('tbody tr').length > 0
    })
    if (!grid) return null
    const cols = [...grid.querySelectorAll('col')]
    const idx = cols.findIndex(c => (c.getAttribute('t') || '').replace(/<[^>]+>/g, '').replace(/^\\*/, '').trim() === '${colName}')
    if (idx < 0) return null
    const tr = [...grid.querySelectorAll('tbody tr')].find(tr => { const r = tr.getBoundingClientRect(); return r.width > 0 && r.height > 0 && r.y > 100 })
    if (!tr) return null
    const tds = [...tr.querySelectorAll('td')]
    const td = tds[idx]
    if (!td) return null
    const r = td.getBoundingClientRect()
    if (r.width === 0 || r.height === 0) return null
    return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), w: Math.round(r.width) }
  })()`)
}

// ---------- 主流程 ----------
async function main() {
  const args = parseArgs()
  const log = { steps: [], artifacts: [] }
  const step = (name) => { console.log('\n──── [' + name + '] ────'); log.steps.push({ t: new Date().toISOString(), name }) }
  const shot = async (tag, file) => { await screenshot(cdp, args, file); log.artifacts.push(file) }

  await ensureBrowser(args)
  let originId = null
  {
    const pages = await listPageTargets(args.port)
    originId = pages[0] ? pages[0].id : null
  }
  let cdp = await connectCdp(args.port, originId)
  await cdp.send('Page.enable')
  await cdp.send('Runtime.enable')
  {
    const pages = await listPageTargets(args.port)
    for (const p of pages) {
      if (p.id !== originId && !p.url.includes('devtools')) {
        try { await cdp.send('Target.closeTarget', { targetId: p.id }) } catch {}
      }
    }
  }

  // 1. 用户登录（立即体验 → 机械行业）
  step('login')
  await loginFlow(cdp)
  await shot('portal', args.out + '-00-portal.png')

  // 2. 打开销售订单（BAPView SA03）
  step('open-sale-order')
  const soUrl = 'https://h4t.chanjet.com/tplus/BAPView/Voucher.aspx?sysId=SA&mId=SA03&pId=voucherView'
  console.log('[page] 打开 ' + soUrl)
  await cdp.send('Page.navigate', { url: soUrl })
  let ready = false
  for (let i = 0; i < 90; i++) {
    const r = await cdp.evalMain(`!!(document.querySelector('col[t]') || document.querySelector('table'))`)
    if (r === true) { ready = true; break }
    await sleep(1500)
  }
  await sleep(4000)
  if (!ready) console.log('[page] 警告：未等到表格 DOM')
  await dumpState(cdp, '销售订单-列表')
  await shot('list', args.out + '-01-list.png')

  // 3. 点击「新增」
  step('click-add')
  const addBtn = await toolbarBtn(cdp, '新增')
  if (!addBtn) { console.error('[page] 未找到「新增」按钮，退出'); process.exit(1) }
  await mouseClick(cdp, addBtn.x, addBtn.y, '新增')
  await sleep(5000)
  {
    const pages = await listPageTargets(args.port)
    for (const p of pages) {
      if (p.id !== originId && p.url.startsWith('edge://')) {
        try { await cdp.send('Target.closeTarget', { targetId: p.id }); console.log('[page] 关闭内部页: ' + p.url) } catch {}
      }
    }
  }
  let newId = null
  for (let i = 0; i < 20; i++) {
    const pages = await listPageTargets(args.port)
    const fresh = pages.find((t) => t.id !== originId && t.url.includes('BAPView') && !t.url.includes('login'))
    if (fresh) { newId = fresh.id; console.log('[page] 跟随新 BAPView 标签页'); break }
    await sleep(1000)
  }
  if (newId) {
    cdp.ws.close()
    cdp = await connectCdp(args.port, newId)
    await cdp.send('Page.enable')
    await cdp.send('Runtime.enable')
    originId = newId
  } else {
    console.log('[page] 表单在原标签页（同页切换）')
  }
  for (let i = 0; i < 30; i++) {
    const r = await cdp.evalMain(`document.querySelectorAll('label').length`)
    if (r > 10) break
    await sleep(1000)
  }
  await sleep(3000)
  await dumpState(cdp, '新增-表单')
  await shot('form', args.out + '-02-form.png')

  // 4. 填写表头
  step('fill-header')
  const headerLog = {}
  headerLog['客户'] = (await fillRef(cdp, '客户')) ? '参照弹窗选首行' : '失败'
  await shot('dialog', args.out + '-03-dialog.png')
  headerLog['订单日期'] = (await fillDate(cdp, '订单日期', '2026-08-20')) ? '2026-08-20' : '失败'
  headerLog['业务类型'] = (await fillRef(cdp, '业务类型')) ? '下拉/弹窗选首项' : '跳过/失败'
  headerLog['预计交货日期'] = (await fillDate(cdp, '预计交货日期', '2026-08-30')) ? '2026-08-30' : '失败'
  log.header = headerLog
  await dumpState(cdp, '表头填写后')
  await shot('filled', args.out + '-04-filled.png')

  // 5. 填写明细首行
  step('fill-detail')
  const detailLog = {}
  const invCell = await detailCell(cdp, '存货名称')
  if (invCell) {
    await mouseClick(cdp, invCell.x, invCell.y, '明细-存货名称')
    await sleep(1500)
    const dlg = await waitDialog(cdp)
    if (dlg) {
      console.log('[detail] 存货参照弹窗: ' + dlg.head)
      if (dlg.kind === 'dropdown') {
        await mouseClick(cdp, dlg.x, dlg.y, '存货下拉首项')
        await pressKey(cdp, 'Enter')
      } else {
        await pickFirstRow(cdp)
        await sleep(800)
        await clickDialogOk(cdp)
        await waitDialogGone(cdp)
      }
      detailLog['存货名称'] = '参照选首行'
    } else {
      console.log('[detail] 存货弹窗未出现，尝试直接输入')
      await typeText(cdp, '铝')
      detailLog['存货名称'] = '直接输入 铝'
    }
    await sleep(1000)
  } else {
    detailLog['存货名称'] = '未找到明细列'
  }
  const qtyCell = await detailCell(cdp, '数量')
  if (qtyCell) {
    await mouseClick(cdp, qtyCell.x, qtyCell.y, '明细-数量')
    await sleep(500)
    await typeText(cdp, '10')
    await pressKey(cdp, 'Enter')
    detailLog['数量'] = '10'
    await sleep(500)
  }
  const priceCell = await detailCell(cdp, '单价')
  if (priceCell) {
    await mouseClick(cdp, priceCell.x, priceCell.y, '明细-单价')
    await sleep(500)
    await typeText(cdp, '100')
    await pressKey(cdp, 'Enter')
    detailLog['单价'] = '100'
    await sleep(500)
  }
  log.detail = detailLog
  await shot('detail-filled', args.out + '-04b-detail.png')

  // 6. 保存
  step('save')
  const saveBtn = await toolbarBtn(cdp, '保存')
  if (saveBtn) {
    await mouseClick(cdp, saveBtn.x, saveBtn.y, '保存')
    await sleep(4000)
  } else {
    console.log('[save] 未找到「保存」按钮')
  }
  const afterSave = await dumpState(cdp, '保存后')
  await shot('saved', args.out + '-05-saved.png')

  log.final = { title: afterSave.title, url: afterSave.url, toolbar: afterSave.toolbar.map(t => t.t) }
  fs.mkdirSync(path.dirname(args.out), { recursive: true })
  fs.writeFileSync(args.out + '.json', JSON.stringify(log, null, 2))
  console.log('\n[log] ' + args.out + '.json')
  console.log('[done] 交互完成')
  cdp.ws.close()
}

main().catch((e) => { console.error('[FAILED]', e.message); process.exit(1) })
