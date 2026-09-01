const { spawn } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const PORT = 9262
const BASE = 'http://127.0.0.1:4173'
const API = 'http://127.0.0.1:3308/api'
const PROFILE = path.join(require('node:os').tmpdir(), 'ov35-' + Date.now())
function edgePath() { return ['C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe', 'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe'].find(fs.existsSync) }
async function waitJson(url, attempts = 60) { for (let i = 0; i < attempts; i++) { try { const r = await fetch(url); if (r.ok) return r.json() } catch {} await sleep(500) } throw new Error('wait failed ' + url) }
async function connect(page) {
  const ws = new WebSocket(page.webSocketDebuggerUrl)
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
  let id = 0; const pending = new Map()
  ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.reject(new Error(m.error.message)) : p.resolve(m.result) } }
  const send = (method, params = {}, timeout = 25000) => new Promise((resolve, reject) => { const mid = ++id; pending.set(mid, { resolve, reject }); setTimeout(() => { if (pending.has(mid)) { pending.delete(mid); reject(new Error(method + ' timeout')) } }, timeout); ws.send(JSON.stringify({ id: mid, method, params })) })
  const evalMain = async (expression) => { const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }); if (r.exceptionDetails) throw new Error('eval: ' + JSON.stringify(r.exceptionDetails).slice(0, 300)); return r.result.value }
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
    await cdp.evalMain("(() => { localStorage.setItem('mes_token', " + JSON.stringify(login.data.token) + "); localStorage.setItem('mes_user', JSON.stringify({ name: 'admin', roleId: 1, roleName: '管理员', isAdmin: true })); localStorage.setItem('mes_login_date', '2026-08-25'); return true })()")
    await cdp.send('Page.navigate', { url: BASE + '/#/panelx/list/BOM?t=' + Date.now() })
    await sleep(9000)
    const res = await cdp.evalMain(`(async () => {
      const sleep2 = (ms) => new Promise((r) => setTimeout(r, ms))
      const out = {}
      out.当前单据 = (document.body.innerText.match(/单据：([^|\\n]+)/) || [])[1]?.trim() || ''
      out.状态 = (document.body.innerText.match(/单据：[^|]+\\|\\s*([^|\\n]+)/) || [])[1]?.trim() || ''
      const addBtn = [...document.querySelectorAll('.tb-main')].find((b) => b.innerText.trim() === '新增')
      if (addBtn) { addBtn.click(); await sleep2(3500) }
      out.新增后单据 = (document.body.innerText.match(/单据：([^|\\n]+)/) || [])[1]?.trim() || ''
      out.新增后状态 = (document.body.innerText.match(/单据：[^|]+\\|\\s*([^|\\n]+)/) || [])[1]?.trim() || ''
      const tables = [...document.querySelectorAll('.main-grid .el-table, .dt-grid, table')]
      out.表格数 = tables.length
      out.明细标题 = (document.body.innerText.match(/物料清单明细[^\\n]*/) || [''])[0]
      out.含新增数据按钮 = [...document.querySelectorAll('button')].some((b) => b.innerText.trim() === '新增数据')
      out.表格行数 = [...document.querySelectorAll('.main-grid .el-table__body-wrapper tbody tr')].length
      out.占位行 = [...document.querySelectorAll('.main-grid .ph-row')].length
      out.含表头 = (document.body.innerText.match(/物料清单编码[^\\n]*/) || [''])[0]
      out.bodySample = document.body.innerText.slice(0, 600).replace(/\\n/g, ' | ')
      return out
    })()`)
    console.log(JSON.stringify(res, null, 1))
  } finally {
    child.kill()
    try { fs.rmSync(PROFILE, { recursive: true, force: true }) } catch {}
  }
}
main().catch((e) => { console.error('FATAL', e.message); process.exit(1) })
