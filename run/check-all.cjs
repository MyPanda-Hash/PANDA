const fs = require('fs');
const { spawn } = require('node:child_process');
const path = require('node:path');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// 从 vue 文件提取 modules（JS 字面量）
const vue = fs.readFileSync('F:/INCER/light-mes/frontend/src/views/scm/BusinessOverview.vue', 'utf8');
const start = vue.indexOf('const modules = [');
const end = vue.indexOf('const active = ref', start);
const modulesSrc = vue.slice(start + 'const modules = '.length, end).replace(/const active = ref\('prod'\)[\s\S]*$/, '');
let modules;
try {
  modules = eval('(' + modulesSrc.replace(/^\s*const modules = /, '') + ')');
} catch (e) {
  // 直接 eval 数组字面量
  modules = eval(modulesSrc.split('\n').filter((l) => !l.startsWith('const')).join('\n'));
}
console.log('modules:', modules.map((m) => m.code).join(','));
const PORT = 9233;
const BASE = 'http://127.0.0.1:4173';
const API = 'http://127.0.0.1:8080/api';
const PROFILE = path.join(require('node:os').tmpdir(), 'ov7-' + Date.now());
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
    const token = login.data.token
    await cdp.send('Page.navigate', { url: BASE + '/?t=' + Date.now() })
    await sleep(4000)
    await cdp.evalMain(`(() => { localStorage.setItem('mes_token', ${JSON.stringify(token)}); localStorage.setItem('mes_user', JSON.stringify({ name: 'admin', roleId: 1, roleName: '管理员', isAdmin: true })); localStorage.setItem('mes_login_date', '2026-08-25'); return true })()`)
    await cdp.send('Page.navigate', { url: BASE + '/#/scm/businessOverview?t=' + Date.now() })
    await sleep(9000)
    let allClean = true
    for (const mod of modules) {
      await cdp.evalMain(`(() => { const m = [...document.querySelectorAll('.bo-mod')].find((x) => x.innerText.includes(${JSON.stringify(mod.name)})); if (m) m.click(); return !!m })()`)
      await sleep(2000)
      const posJson = JSON.stringify(mod.pos || {})
      const hits = JSON.parse(await cdp.evalMain(`(() => {
        const POS = ${posJson}
        const nodeRects = Object.entries(POS).map(([t, p]) => ({ t, x: p[0], y: p[1], w: 200, h: 80 }))
        const edges = [...document.querySelectorAll('.vue-flow__edge')]
        const hits = []
        for (const el of edges) {
          const id = el.getAttribute('data-id') || ''
          const m = id.match(/^e\\d+-(.+?)-([A-Z_]+)$/)
          if (!m) continue
          const source = m[1], target = m[2]
          const pathEl = el.querySelector('path')
          if (!pathEl) continue
          const len = pathEl.getTotalLength()
          let hit = null
          for (let i = 1; i < 40; i++) {
            const pt = pathEl.getPointAtLength(len * i / 40)
            for (const n of nodeRects) {
              if (n.t === source || n.t === target) continue
              if (pt.x > n.x + 12 && pt.x < n.x + n.w - 12 && pt.y > n.y + 12 && pt.y < n.y + n.h - 12) {
                hit = source + '->' + target + ' 穿 ' + n.t
                break
              }
            }
            if (hit) break
          }
          if (hit) hits.push(hit)
        }
        return JSON.stringify(hits)
      })()`))
      if (hits.length) { allClean = false; console.log('[' + mod.name + '] 穿心:', hits) }
      else console.log('[' + mod.name + '] ✅ 无穿心')
    }
    console.log(allClean ? '\n=== 全部模块无穿心 ===' : '\n=== 存在穿心，需修复 ===')
  } finally {
    child.kill()
    try { fs.rmSync(PROFILE, { recursive: true, force: true }) } catch {}
  }
}
main().catch((e) => { console.error('FATAL', e.message); process.exit(1) })