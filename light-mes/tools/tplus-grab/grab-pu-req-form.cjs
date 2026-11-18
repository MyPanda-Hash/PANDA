// grab-pu-req-form.cjs —— 抓请购单新增表单（表头控件/明细列/底部按钮/截图）
const fs = require('node:fs')
const path = require('node:path')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const OUT = 'F:/INCER/light-mes/docs/ref/tplus-live/pu_req-20260824'
const PORT = 9229
async function main() {
  let wsUrl, page
  for (let i = 0; i < 30; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${PORT}/json/list`)
      const targets = await res.json()
      page = targets.find((t) => t.type === 'page' && t.url.includes('BAPView'))
      if (page) { wsUrl = page.webSocketDebuggerUrl; break }
    } catch {}
    await sleep(1000)
  }
  if (!wsUrl) throw new Error('no BAPView target on ' + PORT)
  console.log('[target]', page.title, page.url.slice(0, 100))
  const ws = new WebSocket(wsUrl)
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
  let msgId = 0
  const pending = new Map()
  ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.reject(new Error(m.error.message)) : p.resolve(m.result) } }
  const send = (method, params = {}) => new Promise((resolve, reject) => { const id = ++msgId; pending.set(id, { resolve, reject }); ws.send(JSON.stringify({ id, method, params })) })
  const evalMain = async (expression) => { const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true, timeout: 30000 }); if (r.exceptionDetails) throw new Error('eval: ' + JSON.stringify(r.exceptionDetails).slice(0, 300)); return r.result.value }
  await send('Runtime.enable'); await send('Page.enable')

  // 点击新增（最左上可见）
  const btns = await evalMain(`(() => [...document.querySelectorAll('[class*="tb-text"]')].filter(e => e.innerText && e.innerText.trim() === '新增').map(e => { const r = e.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) } }).filter(b => b.y >= 0 && b.y < 60 && b.w > 10))()`)
  console.log('[addbtns]', JSON.stringify(btns))
  if (!btns.length) throw new Error('无新增按钮')
  const t = btns[0]
  const cx = t.x + t.w / 2, cy = t.y + t.h / 2
  await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: cx, y: cy, button: 'left', clickCount: 1 })
  await sleep(100)
  await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: cx, y: cy, button: 'left', clickCount: 1 })
  console.log('[click] 新增 @' + cx + ',' + cy)
  await sleep(7000)

  const out = await evalMain(`(() => {
    const R = (el) => { const r = el.getBoundingClientRect(); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) } }
    const cs = (el) => getComputedStyle(el)
    // 表头控件
    const ctrls = [...document.querySelectorAll('.vch-ctldiv')].map((d) => {
      const label = d.querySelector('label,span')?.textContent.trim() || ''
      const inp = d.querySelector('input,select,textarea')
      const type = inp ? (inp.tagName + ':' + (inp.type || inp.tagName)) : 'NONE'
      const r = R(d)
      return { label, type, cls: (d.className || '').toString().slice(0, 60), y: r.y, w: r.w, h: r.h, val: inp ? (inp.value || '').slice(0, 30) : '' }
    }).filter(c => c.label)
    // 明细列
    const cols = [...document.querySelectorAll('col[t],col[width]')].map(c => ({ t: c.getAttribute('t') || '', w: c.getAttribute('width') || '' }))
    const ths = [...document.querySelectorAll('th')].map(th => th.innerText.trim()).filter(Boolean).slice(0, 40)
    // 底部按钮
    const footer = [...document.querySelectorAll('[class*="tb-text"],[class*="ft-btn"]')].filter(e => e.children.length === 0 && e.innerText && e.innerText.trim()).map(e => e.innerText.trim()).slice(0, 40)
    // 页签
    const tabs = [...document.querySelectorAll('*')].filter(e => e.children.length === 0 && e.innerText && e.innerText.trim().length < 10 && cs(e).fontSize === '14px' && cs(e).fontWeight === '700' && R(e).y > 0).map(e => ({ text: e.innerText.trim(), y: R(e).y, color: cs(e).color })).filter(t => t.y < 300)
    return JSON.stringify({ url: location.href.slice(0, 160), title: document.title, ctrls, cols, ths, tabs, footer, bodyHead: (document.body.innerText || '').slice(0, 300) })
  })()`)
  const json = JSON.parse(out)
  fs.writeFileSync(path.join(OUT, 'pu_req-form.json'), JSON.stringify(json, null, 1), 'utf8')
  console.log('[form] saved pu_req-form.json ctrls=' + json.ctrls.length + ' cols=' + json.cols.length + ' ths=' + json.ths.length)
  console.log('表头字段:', json.ctrls.map(c => c.label).join(' | '))
  // 截图
  const shot = await send('Page.captureScreenshot', { format: 'png' })
  fs.writeFileSync(path.join(OUT, 'pu_req-form.png'), Buffer.from(shot.data, 'base64'))
  console.log('[shot] pu_req-form.png')
  ws.close()
}
main().catch((e) => { console.error('[FAILED]', e.message); process.exit(1) })