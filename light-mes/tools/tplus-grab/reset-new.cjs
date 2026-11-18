#!/usr/bin/env node
/** reset-new.cjs —— 刷新销售订单页并点击新增，得到全新表单 */
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function main() {
  const port = 9222
  const soUrl = 'https://h4t.chanjet.com/tplus/BAPView/Voucher.aspx?sysId=SA&mId=SA03&pId=voucherView'
  let wsUrl = null
  for (let i = 0; i < 30; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/json/list`)
      const targets = await res.json()
      const page = targets.find((t) => t.type === 'page' && t.url.includes('BAPView'))
      if (page) { wsUrl = page.webSocketDebuggerUrl; break }
    } catch {}
    await sleep(1000)
  }
  if (!wsUrl) { console.log('no target'); return }
  const ws = new WebSocket(wsUrl)
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
  let id = 0
  const pending = new Map()
  ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.reject(new Error(m.error.message)) : p.resolve(m.result) } }
  const send = (method, params = {}) => new Promise((resolve, reject) => { const i = ++id; pending.set(i, { resolve, reject }); ws.send(JSON.stringify({ id: i, method, params })) })
  const evalMain = async (expression) => { const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true, timeout: 20000 }); return r.result ? r.result.value : undefined }
  await send('Page.enable')
  await send('Runtime.enable')

  console.log('[reset] 重新加载销售订单页')
  await send('Page.navigate', { url: soUrl })
  for (let i = 0; i < 60; i++) {
    const ok = await evalMain(`!!document.querySelector('[class*="tb-text"]')`)
    if (ok) break
    await sleep(1500)
  }
  await sleep(4000)

  // 点新增（最左上可见）
  const btn = await evalMain(`(() => {
    const el = [...document.querySelectorAll('[class*="tb-text"]')].find(e => e.innerText && e.innerText.trim() === '新增' && e.getBoundingClientRect().width > 0 && e.getBoundingClientRect().y >= 0 && e.getBoundingClientRect().y < 60)
    if (!el) return null
    const r = el.getBoundingClientRect()
    return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }
  })()`)
  if (!btn) { console.log('[reset] 未找到新增'); ws.close(); return }
  console.log('[reset] 点击新增 @' + btn.x + ',' + btn.y)
  await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: btn.x, y: btn.y })
  await sleep(150)
  await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: btn.x, y: btn.y, button: 'left', clickCount: 1 })
  await sleep(100)
  await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: btn.x, y: btn.y, button: 'left', clickCount: 1 })
  await sleep(6000)
  // 关闭可能的 edge:// 新标签
  const res = await fetch(`http://127.0.0.1:${port}/json/list`)
  const targets = await res.json()
  for (const t of targets.filter((x) => x.type === 'page' && x.url.startsWith('edge://'))) {
    try { await send('Target.closeTarget', { targetId: t.id }) } catch {}
    console.log('[reset] 关闭内部页 ' + t.url)
  }
  // 若新增开了新 BAPView 标签，后续 fill 脚本会自己找；这里停在当前页
  const c = await evalMain(`(() => {
    const l = [...document.querySelectorAll('label')].find(l => (l.textContent || '').trim().replace(/^\\*/, '') === '客户' && l.getBoundingClientRect().height > 0)
    return l ? '客户控件存在' : '无客户控件'
  })()`)
  console.log('[reset] ' + c)
  ws.close()
}
main().catch((e) => { console.error('[FAILED]', e.message); process.exit(1) })
