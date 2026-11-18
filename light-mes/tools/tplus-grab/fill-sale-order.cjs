#!/usr/bin/env node
/**
 * fill-sale-order.cjs —— 对已打开（新增态）的机械行业销售订单表单执行填写 + 保存
 * 假设：Edge 9222 上已有登录且表单打开（vch-ctldiv 存在）
 * 用法：node tools/tplus-grab/fill-sale-order.cjs [--out docs/ref/mes-live/so-e2e]
 */
const fs = require('node:fs')
const path = require('node:path')

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function parseArgs() {
  const argv = process.argv.slice(2)
  const args = { port: 9222, out: path.resolve('docs/ref/mes-live/so-e2e'), width: 1600, height: 1000 }
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i]
    if (k === '--port' || k === '--out') { args[k.slice(2)] = argv[++i]; continue }
    throw new Error('未知参数: ' + k)
  }
  args.port = Number(args.port); args.width = Number(args.width); args.height = Number(args.height)
  return args
}

async function connect(port) {
  let wsUrl = null
  for (let i = 0; i < 30; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/json/list`)
      const targets = await res.json()
      const page = targets.find((t) => t.type === 'page' && t.url.includes('BAPView'))
      if (page) { wsUrl = page.webSocketDebuggerUrl; console.log('[target] ' + page.title); break }
    } catch {}
    await sleep(1000)
  }
  if (!wsUrl) throw new Error('未找到 BAPView target（请先登录并打开销售订单）')
  const ws = new WebSocket(wsUrl)
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
  let id = 0
  const pending = new Map()
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data)
    if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.reject(new Error(m.error.message)) : p.resolve(m.result) }
  }
  ws.onclose = () => { for (const p of pending.values()) p.reject(new Error('ws closed')); pending.clear() }
  const send = (method, params = {}) => new Promise((resolve, reject) => { const i = ++id; pending.set(i, { resolve, reject }); ws.send(JSON.stringify({ id: i, method, params })) })
  const evalMain = async (expression) => {
    const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true, timeout: 30000 })
    if (r.exceptionDetails) throw new Error('eval: ' + JSON.stringify(r.exceptionDetails).slice(0, 250))
    return r.result ? r.result.value : undefined
  }
  await send('Page.enable')
  await send('Runtime.enable')
  return { ws, send, evalMain }
}

async function clickAt(cdp, x, y, label) {
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

// 表头控件：返回 { 输入框中心, 参照按钮中心, 当前值 }
async function ctrlInfo(cdp, label) {
  return cdp.evalMain(`(() => {
    const labs = [...document.querySelectorAll('label')].filter(l => { const t = (l.textContent || '').trim().replace(/^\\*/, ''); return t === '${label}' && l.getBoundingClientRect().width > 0 && l.getBoundingClientRect().height > 0 })
    if (!labs.length) return null
    const l = labs[0]
    let box = l
    for (let i = 0; i < 6 && box.parentElement; i++) { box = box.parentElement; if (box.querySelector('input,select,textarea')) break }
    const r = box.getBoundingClientRect()
    const inp = box.querySelector('input.v-input, input:not([type=hidden]), select, textarea')
    const inRect = inp ? inp.getBoundingClientRect() : null
    const refBtn = box.querySelector('.v-refbtn, [class*="refbtn"], [class*="pencil"]')
    const refRect = refBtn ? refBtn.getBoundingClientRect() : null
    return {
      label: l.textContent.trim(),
      val: inp ? inp.value : '',
      input: inRect && inRect.width > 0 ? { x: Math.round(inRect.x + inRect.width / 2), y: Math.round(inRect.y + inRect.height / 2) } : null,
      refBtn: refRect && refRect.width > 0 ? { x: Math.round(refRect.x + refRect.width / 2), y: Math.round(refRect.y + refRect.height / 2) } : null,
      boxCenter: { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) }
    }
  })()`)
}

// 等待参照弹窗（grid：确定+表格）或下拉（多项小元素）
async function waitRefPopup(cdp, timeoutMs = 10000) {
  const t0 = Date.now()
  while (Date.now() - t0 < timeoutMs) {
    const d = await cdp.evalMain(`(() => {
      const cs = (el) => getComputedStyle(el)
      const vis = (el) => { const r = el.getBoundingClientRect(); const s = cs(el); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' }
      const inView = (r) => r.x >= 0 && r.y >= 0 && r.x < 1600 && r.y < 1000
      // grid 弹窗
      const grid = [...document.querySelectorAll('div')].find(e => { if (!vis(e)) return false; const r = e.getBoundingClientRect(); if (r.width < 300 || r.height < 120 || !inView(r)) return false; const s = cs(e); if (s.position !== 'fixed' && s.position !== 'absolute') return false; const txt = e.innerText || ''; return txt.includes('确定') && e.querySelector('table') })
      if (grid) { const r = grid.getBoundingClientRect(); return { kind: 'grid', x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), head: (grid.innerText || '').slice(0, 60) } }
      // 下拉：fixed/absolute 且视口内、含多个小项的容器
      const dd = [...document.querySelectorAll('div')].find(e => {
        if (!vis(e)) return false
        const r = e.getBoundingClientRect()
        if (r.width < 120 || r.height < 40 || r.height > 600 || !inView(r)) return false
        const s = cs(e)
        if (s.position !== 'fixed' && s.position !== 'absolute') return false
        const items = [...e.querySelectorAll('div,li,span')].filter(c => { const cr = c.getBoundingClientRect(); return cr.width > 0 && cr.height > 0 && cr.y >= r.y && cr.y < r.y + r.height })
        return items.length >= 2 && (e.innerText || '').trim().length < 200
      })
      if (dd) { const r = dd.getBoundingClientRect(); return { kind: 'dropdown', x: Math.round(r.x + r.width / 2), y: Math.round(r.y + 14), head: (dd.innerText || '').slice(0, 50) } }
      return null
    })()`)
    if (d) return d
    await sleep(700)
  }
  return null
}

async function pickFirstRow(cdp) {
  const r = await cdp.evalMain(`(() => {
    const rows = [...document.querySelectorAll('tbody tr')].filter(tr => { const rr = tr.getBoundingClientRect(); return rr.width > 0 && rr.height > 0 && rr.y > 100 })
    const tr = rows[0]
    if (!tr) return null
    const rr = tr.getBoundingClientRect()
    return { x: Math.round(rr.x + 120), y: Math.round(rr.y + rr.height / 2) }
  })()`)
  if (!r) { console.log('[dialog] 未找到数据行'); return false }
  await clickAt(cdp, r.x, r.y, '弹窗首行')
  return true
}

async function clickOk(cdp) {
  const r = await cdp.evalMain(`(() => { const el = [...document.querySelectorAll('span,button,a,div')].find(e => e.children.length === 0 && e.innerText && e.innerText.trim() === '确定' && e.getBoundingClientRect().width > 0 && e.getBoundingClientRect().y >= 0 && e.getBoundingClientRect().y < 1000); if (!el) return null; const r = el.getBoundingClientRect(); return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) } })()`)
  if (!r) { console.log('[dialog] 未找到确定'); return false }
  await clickAt(cdp, r.x, r.y, '确定')
  return true
}

async function handlePopup(cdp, dlg, tag) {
  console.log('[' + tag + '] 弹窗 kind=' + dlg.kind + ' head=' + dlg.head)
  if (dlg.kind === 'dropdown') {
    await clickAt(cdp, dlg.x, dlg.y, tag + ' 下拉首项')
    await pressKey(cdp, 'Enter')
    await sleep(800)
  } else {
    await pickFirstRow(cdp)
    await sleep(800)
    await clickOk(cdp)
    await sleep(1200)
  }
}

// 参照字段：点输入框 → 若弹窗未出再点参照按钮 → 处理弹窗
async function fillRef(cdp, label) {
  console.log('[fillRef] ' + label)
  const c = await ctrlInfo(cdp, label)
  if (!c) { console.log('[fillRef] 未找到控件 ' + label); return false }
  let dlg = null
  if (c.input) {
    await clickAt(cdp, c.input.x, c.input.y, label + ' 输入框')
    await sleep(1500)
    dlg = await waitRefPopup(cdp, 6000)
  }
  if (!dlg && c.refBtn) {
    await clickAt(cdp, c.refBtn.x, c.refBtn.y, label + ' 参照按钮')
    await sleep(2000)
    dlg = await waitRefPopup(cdp, 8000)
  }
  if (!dlg) { console.log('[fillRef] 未等到弹窗（' + label + '）'); return false }
  await handlePopup(cdp, dlg, label)
  return true
}

async function fillDate(cdp, label, value) {
  const c = await ctrlInfo(cdp, label)
  if (!c) { console.log('[fillDate] 未找到 ' + label); return false }
  if (c.val) { console.log('[fillDate] ' + label + ' 已有值=' + c.val + '，跳过'); return true }
  const p = c.input || c.boxCenter
  await clickAt(cdp, p.x, p.y, label)
  await sleep(500)
  await typeText(cdp, value)
  await pressKey(cdp, 'Enter')
  await sleep(700)
  return true
}

// 明细单元格：找带 col[t] 的可见明细网格，列名匹配，取首个数据行（表头下方第一行）
async function detailCell(cdp, colName) {
  return cdp.evalMain(`(() => {
    const grids = [...document.querySelectorAll('table')].filter(t => {
      const r = t.getBoundingClientRect()
      if (r.width < 800 || r.height < 100) return false
      const s = getComputedStyle(t)
      if (s.visibility === 'hidden' || s.display === 'none') return false
      const cols = [...t.querySelectorAll('col')]
      return cols.some(c => (c.getAttribute('t') || '').replace(/<[^>]+>/g, '').replace(/^\\*/, '').trim() === '${colName}')
    })
    const grid = grids.sort((a, b) => b.getBoundingClientRect().width - a.getBoundingClientRect().width)[0]
    if (!grid) return null
    const cols = [...grid.querySelectorAll('col')]
    const idx = cols.findIndex(c => (c.getAttribute('t') || '').replace(/<[^>]+>/g, '').replace(/^\\*/, '').trim() === '${colName}')
    if (idx < 0) return null
    const gRect = grid.getBoundingClientRect()
    // 数据行 = 表头（thead 或首行）下方第一个含 input/编辑控件的行
    const headY = grid.querySelector('thead') ? grid.querySelector('thead').getBoundingClientRect().bottom : gRect.y + 28
    const rows = [...grid.querySelectorAll('tbody tr')].filter(tr => {
      const rr = tr.getBoundingClientRect()
      return rr.width > 0 && rr.height > 0 && rr.y >= headY - 2 && rr.y < gRect.y + gRect.height
    })
    const tr = rows[0]
    if (!tr) return null
    const tds = [...tr.querySelectorAll('td')]
    const td = tds[idx]
    if (!td) return null
    const r = td.getBoundingClientRect()
    if (r.width === 0 || r.height === 0) return null
    return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2), w: Math.round(r.width), colName: '${colName}', gridY: Math.round(gRect.y) }
  })()`)
}

async function main() {
  const args = parseArgs()
  const cdp = await connect(args.port)
  const log = {}
  const shot = async (tag, file) => { await screenshot(cdp, args, file); log.artifacts = log.artifacts || []; log.artifacts.push(file) }

  // 0. 预检当前表单值
  const pre = {}
  for (const f of ['客户', '订单日期', '业务类型', '预计交货日期']) {
    const c = await ctrlInfo(cdp, f)
    pre[f] = c ? c.val : null
  }
  console.log('[pre] ' + JSON.stringify(pre))

  // 1. 客户（参照）
  log['客户'] = (await fillRef(cdp, '客户')) ? '参照选首行' : '失败'
  await shot('dialog', args.out + '-03-dialog.png')
  // 2. 订单日期
  log['订单日期'] = (await fillDate(cdp, '订单日期', '2026-08-20')) ? '2026-08-20' : '失败'
  // 3. 业务类型
  log['业务类型'] = (await fillRef(cdp, '业务类型')) ? '弹窗/下拉选首项' : '跳过/失败'
  // 4. 预计交货日期
  log['预计交货日期'] = (await fillDate(cdp, '预计交货日期', '2026-08-30')) ? '2026-08-30' : '失败'
  await shot('filled', args.out + '-04-filled.png')

  // 5. 明细：存货名称
  const detailLog = {}
  const invCell = await detailCell(cdp, '存货名称')
  if (invCell) {
    await clickAt(cdp, invCell.x, invCell.y, '明细-存货名称 ' + JSON.stringify(invCell))
    await sleep(1500)
    const dlg = await waitRefPopup(cdp)
    if (dlg) { await handlePopup(cdp, dlg, '存货'); detailLog['存货名称'] = '参照选首行' }
    else { await typeText(cdp, '铝'); detailLog['存货名称'] = '直接输入 铝' }
    await sleep(1000)
  } else {
    detailLog['存货名称'] = '未找到列'
  }
  // 6. 数量
  const qtyCell = await detailCell(cdp, '数量')
  if (qtyCell) { await clickAt(cdp, qtyCell.x, qtyCell.y, '明细-数量'); await sleep(400); await typeText(cdp, '10'); await pressKey(cdp, 'Enter'); detailLog['数量'] = '10'; await sleep(400) }
  // 7. 单价
  const priceCell = await detailCell(cdp, '单价')
  if (priceCell) { await clickAt(cdp, priceCell.x, priceCell.y, '明细-单价'); await sleep(400); await typeText(cdp, '100'); await pressKey(cdp, 'Enter'); detailLog['单价'] = '100'; await sleep(400) }
  log.detail = detailLog
  await shot('detail-filled', args.out + '-04b-detail.png')

  // 8. 回读表头值
  const post = {}
  for (const f of ['客户', '订单日期', '业务类型', '预计交货日期']) {
    const c = await ctrlInfo(cdp, f)
    post[f] = c ? c.val : null
  }
  log.post = post
  console.log('[post] ' + JSON.stringify(post))

  // 9. 保存
  const saveBtn = await cdp.evalMain(`(() => { const el = [...document.querySelectorAll('[class*="tb-text"]')].find(e => e.innerText && e.innerText.trim() === '保存' && e.getBoundingClientRect().width > 0 && e.getBoundingClientRect().y >= 0 && e.getBoundingClientRect().y < 60); if (!el) return null; const r = el.getBoundingClientRect(); return { x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2) } })()`)
  if (saveBtn) { await clickAt(cdp, saveBtn.x, saveBtn.y, '保存'); await sleep(4500) }
  await shot('saved', args.out + '-05-saved.png')

  fs.writeFileSync(args.out + '.json', JSON.stringify(log, null, 2))
  console.log('\n[log] ' + args.out + '.json')
  cdp.ws.close()
}
main().catch((e) => { console.error('[FAILED]', e.message); process.exit(1) })
