#!/usr/bin/env node
/**
 * voucher-buttons-capture.cjs —— 单据页按钮功能证据补抓（只读，无第三方依赖，Node >= 21）
 *
 * 依据 tools/tplus-grab/README.md 规范与 mech-20260827/qc 按钮证据格式：
 *   - 对每张单据页（Voucher.aspx）输出
 *       <编码>-<名称>.buttons.json       工具栏 DOM 树（commandId=元素id、文本、accesskey 快捷键、可用状态、层级）
 *       <编码>-<名称>.implementation.json 列表态 + 新增态命令模型（value/action/shortKey/state/parentValue）
 *     —— 与 mech-20260827/qc 的 QM05-到货单.buttons.json / .implementation.json 同构。
 *   - 只做：读取 DOM/JS 模型、点击一次「新增」打开空白卡片态（不保存、不提交任何数据）、关闭提示弹窗。
 *     绝不点击 保存/删除/审核/弃审/生单 等任何写操作按钮。
 *   - URL 中的会话参数统一脱敏为 [REDACTED]。
 *
 * 用法：
 *   node tools/tplus-grab/voucher-buttons-capture.cjs [--dir <批次目录>] [--port 9222] [--only <编码>] [--no-add-state]
 */
const fs = require('node:fs')
const path = require('node:path')

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const args = { port: 9222, dir: path.resolve('docs/ref/tplus-live/mech-20260901/isc'), only: '', noAddState: false }
for (let i = 2; i < process.argv.length; i++) {
  const k = process.argv[i]
  if (k === '--dir' || k === '--port' || k === '--only') args[k.slice(2)] = process.argv[++i]
  else if (k === '--no-add-state') args.noAddState = true
  else throw new Error('未知参数: ' + k)
}
args.port = Number(args.port)

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
    if (res.ok) return
  } catch {}
  const edge = findEdge()
  if (!edge) throw new Error('未找到 msedge.exe')
  const profile = path.join(__dirname, 'profile-crawl')
  fs.mkdirSync(profile, { recursive: true })
  const child = spawn(edge, [
    '--headless=new', `--remote-debugging-port=${args.port}`, `--user-data-dir=${profile}`,
    '--no-first-run', '--no-default-browser-check', '--disable-gpu',
    '--window-size=1600,1000', '--force-device-scale-factor=1', 'about:blank',
  ], { detached: true, stdio: 'ignore' })
  child.unref()
  for (let i = 0; i < 60; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${args.port}/json/version`)
      if (res.ok) return
    } catch {}
    await sleep(1000)
  }
  throw new Error('Edge 启动超时')
}

// 需要 spawn 引用
const { spawn } = require('node:child_process')

async function connectTarget(target) {
  const ws = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
  let msgId = 0
  const pending = new Map()
  const contexts = new Map()
  const responseUrls = new Map() // requestId -> url
  const doneIds = new Set()
  let dialogSeen = null
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data)
    if (msg.id && pending.has(msg.id)) {
      const p = pending.get(msg.id); pending.delete(msg.id)
      msg.error ? p.reject(new Error(msg.error.message)) : p.resolve(msg.result)
      return
    }
    const m = msg.method || ''
    if (m === 'Runtime.executionContextCreated') {
      const c = msg.params.context
      if (c.auxData && c.auxData.frameId) contexts.set(c.auxData.frameId, c.id)
    } else if (m === 'Page.javascriptDialogOpening') {
      dialogSeen = (msg.params && msg.params.message) || ''
      console.log('  [dialog] 自动关闭: ' + dialogSeen.slice(0, 70))
      ws.send(JSON.stringify({ id: --msgId, method: 'Page.handleJavaScriptDialog', params: { accept: false } }))
    } else if (m === 'Network.responseReceived') {
      responseUrls.set(msg.params.requestId, msg.params.response.url)
    } else if (m === 'Network.loadingFinished') {
      doneIds.add(msg.params.requestId)
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
    if (r.exceptionDetails) throw new Error('eval: ' + String((r.exceptionDetails.exception && r.exceptionDetails.exception.description) || r.exceptionDetails.text || '').slice(0, 200))
    return r.result.value
  }
  const waitResponsive = async (contextId, attempts = 40) => {
    for (let i = 0; i < attempts; i++) {
      try { if ((await evalIn('1+1', contextId, 4000)) === 2) return true } catch {}
      await sleep(1000)
    }
    return false
  }
  const frameTree = async () => (await send('Page.getFrameTree')).frameTree
  return { ws, target, send, evalIn, contexts, frameTree, waitResponsive, responseUrls, doneIds, dialogSeen: () => dialogSeen }
}

async function pageTargets(port) {
  try {
    const res = await fetch(`http://127.0.0.1:${port}/json/list`)
    return (await res.json()).filter((t) => t.type === 'page')
  } catch { return [] }
}

// 工具栏 DOM 树枚举（列表态/卡片态通用）
const TOOLBAR_DUMP = `(() => {
  const roots = [...document.querySelectorAll('[id^=toolbarContainer]')]
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).display !== 'none' && getComputedStyle(e).visibility !== 'hidden' }
  const out = []
  for (const root of roots) {
    const items = []
    const walk = (li, depth, groupIds) => {
      const a = li.querySelector(':scope > a')
      const txt = li.querySelector(':scope > a .tb-text')
      const childUl = li.querySelector(':scope > ul')
      const r = li.getBoundingClientRect()
      const title = (txt && txt.getAttribute('title')) || (a && a.getAttribute('title')) || ''
      const m = title.match(/\\(([^)]*Alt[^)]*|[^)]*Ctrl[^)]*|[^)]*Shift[^)]*)\\)/i)
      items.push({
        commandId: li.id || '',
        text: ((txt && txt.textContent) || li.textContent || '').trim().slice(0, 30),
        shortcut: (a && a.getAttribute('accesskey')) || null,
        shortcutText: m ? m[1] : null,
        anchorClass: (a && a.className || '').toString().slice(0, 30),
        enabled: !!(a && /tb-enable/.test(a.className || '')),
        menuDepth: depth,
        groupIds,
        hasSubmenu: !!childUl,
        visible: vis(li),
        rect: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
      })
      if (childUl) {
        const gid = [...groupIds, li.id]
        for (const c of childUl.children) if (c.tagName === 'LI') walk(c, depth + 1, gid)
      }
    }
    for (const c of root.children) if (c.tagName === 'UL') { for (const li of c.children) if (li.tagName === 'LI') walk(li, 0, [root.id]) }
    out.push({ rootId: root.id, itemCount: items.length, items })
  }
  return JSON.stringify({ toolbars: out })
})()`

// 命令模型：优先读 T+ 工具栏控件 JS 对象；读不到则从 DOM id 树推导 value/parentValue
const COMMAND_DUMP = `(() => {
  const out = []
  const roots = [...document.querySelectorAll('[id^=toolbarContainer]')]
  const walkDom = (li, parentValue, depth) => {
    const a = li.querySelector(':scope > a')
    const txt = li.querySelector(':scope > a .tb-text')
    const seg = (li.id || '').split('-')
    const childUl = li.querySelector(':scope > ul')
    out.push({
      value: seg[seg.length - 1] || '',
      text: ((txt && txt.textContent) || '').trim().slice(0, 30),
      commandId: li.id || '',
      shortKey: (a && a.getAttribute('accesskey')) || null,
      state: (a && /tb-enable/.test(a.className || '')) ? 'enable' : 'disable',
      parentValue: parentValue,
      commandFrom: 'dom-id',
      visible: !!(a && a.getBoundingClientRect().height > 0),
    })
    if (childUl) for (const c of childUl.children) if (c.tagName === 'LI') walkDom(c, seg[seg.length - 1] || parentValue, depth + 1)
  }
  for (const root of roots) for (const c of root.children) if (c.tagName === 'UL') for (const li of c.children) if (li.tagName === 'LI') walkDom(li, root.id.replace('toolbarContainer-', ''), 0)
  // 尝试控件模型（若页面暴露）
  try {
    for (const k of Object.getOwnPropertyNames(window)) {
      const v = window[k]
      if (v && typeof v === 'object' && Array.isArray(v.items) && v.items.length && v.items[0] && v.items[0].action !== undefined) {
        for (const it of v.items) {
          const hit = out.find((o) => o.commandId === it.id || o.value === it.value)
          if (hit) { hit.action = it.action; hit.args = it.args; hit.selected = it.selected; hit.isDefaultMenu = it.isDefaultMenu; hit.commandFrom = 'js-model' }
        }
      }
    }
  } catch (e) {}
  return JSON.stringify({ commands: out })
})()`

async function waitToolbar(cdp, attempts, timeoutEach = 4000) {
  for (let i = 0; i < attempts; i++) {
    try { if (await cdp.evalIn(`!!document.querySelector('[id^=toolbarContainer]')`, undefined, timeoutEach)) return true } catch {}
    await sleep(1000)
  }
  return false
}

async function grabConfigResponse(cdp) {
  // 从网络响应中找工具栏命令配置原始报文（若存在）
  for (const [reqId, url] of cdp.responseUrls) {
    if (!cdp.doneIds.has(reqId)) continue
    if (/\.(png|gif|jpg|jpeg|css|woff2?|ico|svg)(\?|$)/i.test(url)) continue
    try {
      const body = await cdp.send('Network.getResponseBody', { requestId: reqId }, 8000)
      const b = body.base64Encoded ? Buffer.from(body.body, 'base64').toString('utf8') : body.body
      if (b && b.includes('toolbarContainer') && b.length < 3_000_000) return { url: REDACT(url), body: b }
    } catch {}
  }
  return null
}

async function captureOne(base, item, url, opts) {
  const created = await fetch(`http://127.0.0.1:${args.port}/json/new?${encodeURIComponent(url)}`, { method: 'PUT' }).then((r) => r.json()).catch(() => null)
  if (!created || !created.id) throw new Error('新开 tab 失败')
  const cdp = await connectTarget(created)
  await cdp.send('Page.enable'); await cdp.send('Runtime.enable'); await cdp.send('Network.enable')
  try {
    for (let i = 0; i < 30; i++) {
      const u = (await cdp.frameTree().catch(() => ({ frame: { url: '' } }))).frame.url || ''
      if (u.includes('Voucher.aspx') || u.includes('aspx')) break
      await sleep(1000)
    }
    if (!(await cdp.waitResponsive(undefined, 30))) throw new Error('页面渲染无响应')
    if (!(await waitToolbar(cdp, 8))) {
      // 直连偶发服务端错误弹窗（已自动关闭），重载一次
      cdp.responseUrls.clear(); cdp.doneIds.clear()
      await cdp.send('Page.navigate', { url })
      await cdp.waitResponsive(undefined, 30)
      if (!(await waitToolbar(cdp, 12))) throw new Error('工具栏未出现（可能非单据页）')
    }
    await sleep(3000)

    // 列表态：DOM 树 + 命令模型 + 网络配置
    const domTree = JSON.parse(await cdp.evalIn(TOOLBAR_DUMP, undefined, 25000))
    const cmdModel = JSON.parse(await cdp.evalIn(COMMAND_DUMP, undefined, 25000))
    const netCfg = await grabConfigResponse(cdp)
    const listRecord = {
      capturedAt: new Date().toISOString(),
      readOnly: true,
      code: item.code,
      label: item.text,
      editState: 'List',
      url: REDACT((await cdp.frameTree()).frame.url),
      note: '工具栏 DOM 树枚举；commandId=元素id；快捷键取 accesskey 与标题括号文本；未点击任何写操作按钮',
      toolbarRoots: domTree.toolbars.map((t) => ({ rootId: t.rootId, itemCount: t.itemCount })),
      toolbarItems: domTree.toolbars.flatMap((t) => t.items),
      commands: cmdModel.commands,
      networkConfig: netCfg ? { url: netCfg.url, note: '加载时携带工具栏命令配置的响应原文截断', excerpt: netCfg.body.slice(0, 60000) } : null,
    }
    fs.writeFileSync(base + '.buttons.json', JSON.stringify(listRecord, null, 2))

    // 新增（卡片）态：仅点击一次顶层「新增」打开空白表单，不保存
    if (!opts.noAddState) {
      const addId = JSON.parse(await cdp.evalIn(`(() => {
        const lis=[...document.querySelectorAll('[id^=toolbarContainer] li')]
        const li=lis.find((li)=>{const t=li.querySelector(':scope > a .tb-text');return t&&(t.textContent||'').trim()==='新增'&&li.getBoundingClientRect().height>0})
        return li ? JSON.stringify({id:li.id}) : 'null'
      })()`, undefined, 12000))
      if (addId) {
        const urlBefore = (await cdp.frameTree()).frame.url
        await cdp.evalIn(`document.getElementById(${JSON.stringify(addId.id)}).click()`, undefined, 10000).catch(() => {})
        await sleep(2500)
        for (let i = 0; i < 15; i++) {
          const u = (await cdp.frameTree().catch(() => ({ frame: { url: '' } }))).frame.url || ''
          if (u !== urlBefore || /voucherStateControl=Add|pId=voucherCard/i.test(u)) break
          await sleep(1000)
        }
        await cdp.waitResponsive(undefined, 25)
        if (await waitToolbar(cdp, 10)) {
          await sleep(2500)
          const domTree2 = JSON.parse(await cdp.evalIn(TOOLBAR_DUMP, undefined, 25000))
          const cmdModel2 = JSON.parse(await cdp.evalIn(COMMAND_DUMP, undefined, 25000))
          const u2 = (await cdp.frameTree()).frame.url || ''
          const addRecord = {
            capturedAt: new Date().toISOString(),
            readOnly: true,
            code: item.code,
            label: item.text,
            editState: /voucherStateControl=(\w+)/.test(u2) ? (u2.match(/voucherStateControl=(\w+)/)[1]) : 'Add',
            url: REDACT(u2),
            note: '点击一次顶层「新增」后的卡片态工具栏；未保存、未提交任何数据',
            toolbarRoots: domTree2.toolbars.map((t) => ({ rootId: t.rootId, itemCount: t.itemCount })),
            toolbarItems: domTree2.toolbars.flatMap((t) => t.items),
            commands: cmdModel2.commands,
          }
          fs.writeFileSync(base + '.implementation.json', JSON.stringify(addRecord, null, 2))
          return { list: listRecord, add: addRecord }
        }
        throw new Error('新增态工具栏未出现（列表态已保存）')
      }
      throw new Error('未找到顶层「新增」按钮（列表态已保存）')
    }
    return { list: listRecord, add: null }
  } finally {
    try { cdp.ws.close() } catch {}
    await fetch(`http://127.0.0.1:${args.port}/json/close/${created.id}`).catch(() => {})
  }
}

async function main() {
  const dir = args.dir
  const list = JSON.parse(fs.readFileSync(path.join(dir, 'remaining-isc.json'), 'utf8'))
  // 仅对真单据页（已抓 dom.json 且其 URL 为 Voucher.aspx）做深度按钮采集；
  // 报表/工具页按钮保留在各自 dom.json 的 page.buttons（同 QM 批次惯例）
  const items = [...(list.vouchers || []), ...(list.reports || [])]
    .filter((it) => !args.only || it.code === args.only)
    .map((it) => {
      let voucherUrl = ''
      try {
        const d = JSON.parse(fs.readFileSync(path.join(dir, it.code + '-' + it.text + '.dom.json'), 'utf8'))
        const u = d.url || ''
        if (u.includes('Voucher.aspx')) {
          const mId = (u.match(/mId=([^&]+)/) || [])[1]
          const sysId = (u.match(/sysId=([^&]+)/) || [])[1]
          if (mId && sysId) voucherUrl = `https://h4t.chanjet.com/tplus/BAPView/Voucher.aspx?sysId=${sysId}&mId=${mId}&pId=voucherView`
        }
      } catch {}
      return { ...it, voucherUrl }
    })
    .filter((it) => it.voucherUrl)
    .map((it) => {
      // 用已抓 dom.json 里的真实 URL 解析 sysId/mId
      let url = ''
      try {
        const d = JSON.parse(fs.readFileSync(path.join(dir, it.code + '-' + it.text + '.dom.json'), 'utf8'))
        const u = d.url || ''
        const mId = (u.match(/mId=([^&]+)/) || [])[1]
        const sysId = (u.match(/sysId=([^&]+)/) || [])[1]
        if (mId && sysId) url = `https://h4t.chanjet.com/tplus/BAPView/Voucher.aspx?sysId=${sysId}&mId=${mId}&pId=voucherView`
      } catch {}
      if (!url) {
        const sysId = (it.code.match(/^[A-Za-z]+/) || [it.code.slice(0, 2)])[0]
        url = `https://h4t.chanjet.com/tplus/BAPView/Voucher.aspx?sysId=${sysId}&mId=${it.code}&pId=voucherView`
      }
      return { ...it, url }
    })
  const items2 = items
  console.log('[list] 目标单据页 ' + items2.length + ' 张' + (args.noAddState ? '（仅列表态）' : '（列表态+新增态）'))

  await ensureBrowser()
  const agg = { capturedAt: new Date().toISOString(), readOnly: true, note: '单据页按钮命令绑定汇总（列表态+新增态）；只读采集；报表/工具页按钮见各自 dom.json 的 page.buttons', vouchers: [] }
  let ok = 0, fail = 0
  for (const item of items2) {
    const base = path.join(dir, item.code + '-' + item.text)
    if (fs.existsSync(base + '.buttons.json') && (args.noAddState || fs.existsSync(base + '.implementation.json'))) { console.log('[skip] ' + item.code + ' ' + item.text); continue }
    const t0 = Date.now()
    try {
      const r = await captureOne(base, item, item.voucherUrl, { noAddState: args.noAddState })
      ok++
      agg.vouchers.push({ code: item.code, label: item.text, url: r.list.url, listToolbar: r.list.toolbarItems.length, listCommands: r.list.commands.length, addState: r.add ? { editState: r.add.editState, toolbarItems: r.add.toolbarItems.length, commands: r.add.commands.length } : null, file: path.basename(base) })
      console.log('[ok] ' + item.code + ' ' + item.text + ' 列表' + r.list.toolbarItems.length + '项' + (r.add ? ' / ' + r.add.editState + '态' + r.add.toolbarItems.length + '项' : '') + ' (' + Math.round((Date.now() - t0) / 1000) + 's)')
    } catch (e) {
      fail++
      agg.vouchers.push({ code: item.code, label: item.text, error: e.message })
      console.log('[fail] ' + item.code + ' ' + item.text + ' :: ' + e.message)
    }
    fs.writeFileSync(path.join(dir, 'buttons-capture-summary.json'), JSON.stringify({ ...agg, finishedAt: new Date().toISOString() }, null, 2))
  }
  console.log('[done] 成功 ' + ok + ' / 失败 ' + fail)
  process.exit(0)
}

main().catch((e) => { console.error('[FAILED]', e.message); process.exit(1) })
