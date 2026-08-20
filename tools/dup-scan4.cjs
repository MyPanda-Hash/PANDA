const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const PANELS = ['PURCHASE_IN','FINISH_IN','OTHER_IN','SALE_OUT','MATERIAL_OUT','OTHER_OUT','PU_ORDER','PU_IN','SALE_INV','PICK_ORDER','MATERIAL_REQ','ARRIVAL_IN','FINISH_INSPECT','INSPECTION','DISPATCH']
async function main() {
  const res = await fetch('http://127.0.0.1:9222/json/list')
  const targets = await res.json()
  const page = targets.find((t) => t.type === 'page')
  const ws = new WebSocket(page.webSocketDebuggerUrl)
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
    if (r.exceptionDetails) return 'EVAL_ERR'
    return r.result.value
  }
  await send('Page.enable'); await send('Runtime.enable')
  let allOk = true
  for (const code of PANELS) {
    await send('Page.navigate', { url: 'http://localhost:5173/#/dashboard?e2e=' + Date.now() })
    await sleep(1800)
    await send('Page.navigate', { url: `http://localhost:5173/#/panelx/list/${code}?e2e=${Date.now()}` })
    await sleep(5500)
    const r = await evalMain(`(() => {
      const groups = [...document.querySelectorAll('.tools .tb-group')].filter(g => g.offsetParent !== null);
      const mains = groups.map(g => ((g.querySelector('.tb-main') || {}).textContent || '').trim()).filter(Boolean);
      const dup = [...new Set(mains.filter((m, i) => mains.indexOf(m) !== i))];
      return JSON.stringify({ count: mains.length, dup, hasAudit: mains.includes('\u5ba1\u6838'), hasApprove: mains.includes('\u5ba1\u6279') });
    })()`)
    let o = {}
    try { o = JSON.parse(r) } catch (e) { o = { count: 0, dup: ['err'], hasAudit: false, hasApprove: false } }
    if (o.dup.length) allOk = false
    console.log(`[${code}] 按钮=${o.count} 审核=${o.hasAudit} 审批=${o.hasApprove} ${o.dup.length ? '!!重复:' + o.dup.join(',') : 'OK'}`)
  }
  console.log(allOk ? '=== 全部无重复 ===' : '=== 仍有重复 ===')
  ws.close()
}
main().catch((e) => { console.log('ERR', e.message); process.exit(1) })