/**
 * flow-shots.cjs —— 真实 T+ 业务总览各模块流程图区域高清截图（CDP clip）
 * 登录机械行业 → 智能供应链 → 业务总览 → 逐模块定位流程图画布 → clip 截图（2x 清晰度）
 */
const { spawn } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')
const os = require('node:os')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const PORT = 9334
const OUT = path.resolve(__dirname, '../../docs/ref/tplus-live/business-overview-20260825/flow-shots')
const MODULES = [
  ['production', '生产管理'],
  ['outsource', '委外管理'],
  ['sales', '销售管理'],
  ['purchase', '采购管理'],
  ['distribution', '配货管理'],
  ['inventory', '库存核算'],
  ['mobile-warehouse', '移动仓管'],
  ['serial-number', '序列号管理'],
]
function edgePath() { return ['C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe', 'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe'].find(fs.existsSync) }
async function waitJson(url, attempts = 80) { for (let i = 0; i < attempts; i++) { try { const r = await fetch(url); if (r.ok) return r.json() } catch {} await sleep(500) } throw new Error('wait failed: ' + url) }
async function connect(page) {
  const ws = new WebSocket(page.webSocketDebuggerUrl)
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
  let id = 0; const pending = new Map(); const contexts = new Map()
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data)
    if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.reject(new Error(m.error.message)) : p.resolve(m.result) }
    else if (m.method === 'Runtime.executionContextCreated' && m.params.context.auxData?.frameId) contexts.set(m.params.context.auxData.frameId, m.params.context.id)
  }
  const send = (method, params = {}, timeout = 25000) => new Promise((resolve, reject) => { const mid = ++id; pending.set(mid, { resolve, reject }); setTimeout(() => { if (pending.has(mid)) { pending.delete(mid); reject(new Error(method + ' timeout')) } }, timeout); ws.send(JSON.stringify({ id: mid, method, params })) })
  const evaluate = async (expression, contextId) => { const r = await send('Runtime.evaluate', { expression, contextId, returnByValue: true, awaitPromise: true }); if (r.exceptionDetails) throw new Error('eval: ' + (r.exceptionDetails.exception?.description || r.exceptionDetails.text)); return r.result.value }
  const frameTree = async () => (await send('Page.getFrameTree')).frameTree
  const findFrame = (node, text) => { if (node.frame.url.includes(text)) return node.frame; for (const c of node.childFrames || []) { const f = findFrame(c, text); if (f) return f } return null }
  const ctxFor = async (frame) => { let cid = contexts.get(frame.id); if (!cid) { const w = await send('Page.createIsolatedWorld', { frameId: frame.id, worldName: 'flow-' + frame.id, grantUniveralAccess: true }); cid = w.executionContextId } return cid }
  return { ws, send, evaluate, frameTree, findFrame, ctxFor, contexts }
}
async function clickByText(cdp, text) {
  const pos = JSON.parse(await cdp.evaluate(`(() => {
    const v = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).display !== 'none' && getComputedStyle(e).visibility !== 'hidden' }
    const el = [...document.querySelectorAll('button,a,span,div,li')].find((e) => v(e) && (e.textContent || '').trim() === ${JSON.stringify(text)})
    if (!el) return null
    const r = el.getBoundingClientRect(); return JSON.stringify({ x: r.x + r.width / 2, y: r.y + r.height / 2 })
  })()`))
  if (!pos) return false
  await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: pos.x, y: pos.y, button: 'left', clickCount: 1 })
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: pos.x, y: pos.y, button: 'left', clickCount: 1 })
  return true
}
async function login(cdp) {
  await cdp.send('Page.navigate', { url: 'https://t.chanjet.com/tplus/view/login.html' })
  await sleep(6500)
  if (!await clickByText(cdp, '立即体验')) throw new Error('未找到立即体验')
  for (let i = 0; i < 50; i++) {
    const frame = cdp.findFrame(await cdp.frameTree(), 'selectRoles')
    if (frame) {
      const cid = await cdp.ctxFor(frame)
      const hit = await cdp.evaluate(`(() => { const it = [...document.querySelectorAll('*')].find((n) => (n.textContent || '').trim() === '机械行业'); if (!it) return null; it.click(); return it.textContent.trim() })()`, cid)
      if (hit) break
    }
    await sleep(1000)
  }
  for (let i = 0; i < 120; i++) { if ((await cdp.frameTree()).frame.url.includes('/portal/portal.html')) break; await sleep(1000) }
  await sleep(6000)
}
async function main() {
  fs.mkdirSync(OUT, { recursive: true })
  const profile = path.join(os.tmpdir(), 'flow-shots-' + Date.now())
  fs.mkdirSync(profile, { recursive: true })
  const child = spawn(edgePath(), ['--headless=new', '--remote-debugging-port=' + PORT, '--user-data-dir=' + profile, '--no-first-run', '--disable-gpu', '--window-size=1600,1000', '--force-device-scale-factor=1', 'about:blank'], { stdio: 'ignore' })
  try {
    await waitJson('http://127.0.0.1:' + PORT + '/json/version')
    const targets = await waitJson('http://127.0.0.1:' + PORT + '/json/list')
    const cdp = await connect(targets.find((t) => t.type === 'page'))
    await cdp.send('Page.enable'); await cdp.send('Runtime.enable'); await cdp.send('Network.enable')
    await login(cdp)
    if (!await cdp.evaluate(`document.body.innerText.includes('智能供应链')`, await cdp.ctxFor((await cdp.frameTree()).frame))) {
      // 主 frame 找智能供应链
      await sleep(3000)
    }
    // 点智能供应链一级菜单
    let clicked = await cdp.evaluate(`(() => { const el = [...document.querySelectorAll('li,div,span')].find((e) => (e.textContent || '').trim() === '智能供应链'); if (el) { el.click(); return true } return false })()`)
    if (!clicked) await clickByText(cdp, '智能供应链')
    await sleep(2500)
    // 业务总览（ISC iframe 内）
    let iscFrame = cdp.findFrame(await cdp.frameTree(), 'menuCode=ISC')
    if (iscFrame) {
      const cid = await cdp.ctxFor(iscFrame)
      await cdp.evaluate(`(() => { const el = [...document.querySelectorAll('*')].find((e) => (e.textContent || '').trim() === '业务总览'); if (el) { el.click(); return true } return false })()`, cid)
    } else {
      await clickByText(cdp, '业务总览')
    }
    await sleep(2000)
    for (const [code, name] of MODULES) {
      // 回到智能供应链 tab 并点模块
      await cdp.evaluate(`(() => { const el = [...document.querySelectorAll('span,div,li')].find((e) => (e.textContent || '').trim() === '智能供应链' && e.getBoundingClientRect().y < 90); if (el) { el.click(); return true } return false })()`).catch(() => {})
      await sleep(500)
      const modFrame = cdp.findFrame(await cdp.frameTree(), 'ISC_iframe')
      if (!modFrame) { console.log('[skip]', name, 'no ISC_iframe'); continue }
      const cid = await cdp.ctxFor(modFrame)
      const clickedMod = await cdp.evaluate(`(() => { const el = [...document.querySelectorAll('*')].find((e) => (e.textContent || '').trim() === ${JSON.stringify(name)}); if (!el) return false; const r = el.getBoundingClientRect(); if (!r.width || !r.height) return false; el.click(); return true })()`, cid)
      if (!clickedMod) { console.log('[skip]', name, 'module not found'); continue }
      await sleep(2500)
      // 在模块 iframe（MP_iframe 等）里定位流程图画布
      const modIscFrame = cdp.findFrame(await cdp.frameTree(), 'ISC_iframe')
      let flowFrame = cdp.findFrame(await cdp.frameTree(), 'menuCode=')
      // 找包含"业务流程图"的面板容器，取其画布区域
      const info = await cdp.evaluate(`(() => {
        const v = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).display !== 'none' && getComputedStyle(e).visibility !== 'hidden' }
        const panels = [...document.querySelectorAll('*')].filter((e) => v(e) && (e.textContent || '').trim().includes('业务流程图') && e.children.length < 6)
        let canvas = null
        for (const p of panels) {
          const box = p.closest('.t-ideskpanel, [class*=ideskpanel]') || p.parentElement
          if (!box) continue
          const c = box.querySelector('canvas, svg, [class*=flow], [class*=canvas], [class*=relation]')
          if (c && v(c)) { canvas = c; break }
        }
        if (!canvas) return JSON.stringify({ ok: false, text: document.body.innerText.slice(0, 100) })
        const r = canvas.getBoundingClientRect()
        return JSON.stringify({ ok: true, rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }, tag: canvas.tagName, cls: (canvas.className || '').toString().slice(0, 80) })
      })()`, cid)
      const infoObj = JSON.parse(info)
      if (!infoObj.ok) { console.log('[skip]', name, 'flow canvas not found:', infoObj.text?.slice(0, 60)); continue }
      console.log('[shot]', name, 'rect:', JSON.stringify(infoObj.rect), 'tag:', infoObj.tag)
      // 2x 清晰度裁剪截图
      const shot = await cdp.send('Page.captureScreenshot', {
        format: 'png',
        clip: { x: infoObj.rect.x, y: infoObj.rect.y, width: infoObj.rect.w, height: infoObj.rect.h, scale: 2 },
      })
      fs.writeFileSync(path.join(OUT, `${code}-flow.png`), Buffer.from(shot.data, 'base64'))
    }
    console.log('截图目录:', OUT)
  } finally {
    child.kill()
    try { fs.rmSync(profile, { recursive: true, force: true }) } catch {}
  }
}
main().catch((e) => { console.error('FATAL', e.message); process.exit(1) })