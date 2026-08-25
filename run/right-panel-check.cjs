const { spawn } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const PORT = 9237
const BASE = 'http://127.0.0.1:4173'
const API = 'http://127.0.0.1:8080/api'
const PROFILE = path.join(require('node:os').tmpdir(), 'ov11-' + Date.now())
function edgePath() { return ['C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe', 'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe'].find(fs.existsSync) }
async function waitJson(url, attempts = 60) { for (let i = 0; i < attempts; i++) { try { const r = await fetch(url); if (r.ok) return r.json() } catch {} await sleep(500) } throw new Error('wait failed ' + url) }
async function connect(page) {
  const ws = new WebSocket(page.webSocketDebuggerUrl)
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
  let id = 0; const pending = new Map()
  ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.reject(new Error(m.error.message)) : p.resolve(m.result) } }
  const send = (method, params = {}, timeout = 25000) => new Promise((resolve, reject) => { const mid = ++id; pending.set(mid, { resolve, reject }); setTimeout(() => { if (pending.has(mid)) { pending.delete(mid); reject(new Error(method + ' timeout')) } }, timeout); ws.send(JSON.stringify({ id: mid, method, params })) })
  const evalMain = async (expression) => { const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }); if (r.exceptionDetails) throw new Error('eval: ' + JSON.stringify(r.exceptionDetails).slice(0, 200)); return r.result.value }
  return { ws, send, evalMain }
}
async function main() {
  fs.mkdirSync(PROFILE, { recursive: true })
  const child = spawn(edgePath(), ['--headless=new', '--remote-debugging-port=' + PORT, '--user-data-dir=' + PROFILE, '--no-first-run', '--disable-gpu', '--window-size=1500,1000', '--force-device-scale-factor=1', 'about:blank'], { stdio: 'ignore' })
  try {
    await waitJson('http://127.0.0.1:' + PORT + '/json/version')
    const targets = await waitJson('http://127.0.0.1:' + PORT + '/json/list')
    const cdp = await connect(targets.find((t) => t.type === 'page'))
    await cdp.send('Page.enable'); await cdp.send('Runtime.enable')
    const login = await fetch(API + '/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ userName: 'admin', password: '123456' }) }).then((r) => r.json())
    await cdp.send('Page.navigate', { url: BASE + '/?t=' + Date.now() })
    await sleep(4000)
    await cdp.evalMain(`(() => { localStorage.setItem('mes_token', ${JSON.stringify(login.data.token)}); localStorage.setItem('mes_user', JSON.stringify({ name: 'admin', roleId: 1, roleName: '管理员', isAdmin: true })); localStorage.setItem('mes_login_date', '2026-08-25'); return true })()`)
    await cdp.send('Page.navigate', { url: BASE + '/#/scm/businessOverview?t=' + Date.now() })
    await sleep(9000)
    const res = await cdp.evalMain(`(() => {
      const body = document.querySelector('.bo-body'); const mods = document.querySelector('.bo-modules'); const main = document.querySelector('.bo-main'); const secs = document.querySelector('.bo-sections')
      const br = (el) => { const r = el.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) } }
      const secTitles = [...document.querySelectorAll('.bo-sec-title')].map((t) => t.innerText.trim())
      const btnCount = document.querySelectorAll('.bo-sections .bo-btn').length
      const modBtnCount = document.querySelectorAll('.bo-mod').length
      const canvasW = document.querySelector('.bo-canvas') ? Math.round(document.querySelector('.bo-canvas').getBoundingClientRect().width) : 0
      return { body: br(body), mods: br(mods), main: br(main), secs: br(secs), secTitles, btnCount, modBtnCount, canvasW }
    })()`)
    console.log(JSON.stringify(res, null, 1))
    const s = await cdp.send('Page.captureScreenshot', { format: 'png' })
    fs.mkdirSync('F:/INCER/light-mes/docs/ref/mes-live/overview-vueflow', { recursive: true })
    fs.writeFileSync('F:/INCER/light-mes/docs/ref/mes-live/overview-vueflow/10-right-panel.png', Buffer.from(s.data, 'base64'))
    console.log('screenshot saved')
  } finally {
    child.kill()
    try { fs.rmSync(PROFILE, { recursive: true, force: true }) } catch {}
  }
}
main().catch((e) => { console.error('FATAL', e.message); process.exit(1) })