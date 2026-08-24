#!/usr/bin/env node

/**
 * 真实 T+ 机械行业「智能供应链 -> 业务总览」只读探针。
 * 登录、点击模块并采集关系图；不提交任何业务单据。
 */
const { spawn } = require('node:child_process')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const PORT = 9333
const OUT = path.resolve(__dirname, '../../docs/ref/tplus-live/business-overview-20260824')

function edgePath() {
  return [
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  ].find(fs.existsSync)
}

async function waitJson(url, attempts = 80) {
  for (let index = 0; index < attempts; index += 1) {
    try {
      const response = await fetch(url)
      if (response.ok) return response.json()
    } catch {}
    await sleep(500)
  }
  throw new Error(`等待失败：${url}`)
}

async function connect(page) {
  const ws = new WebSocket(page.webSocketDebuggerUrl)
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject })
  let sequence = 0
  const pending = new Map()
  const contexts = new Map()
  const requests = []

  ws.onmessage = (event) => {
    const message = JSON.parse(event.data)
    if (message.id && pending.has(message.id)) {
      const task = pending.get(message.id)
      pending.delete(message.id)
      message.error ? task.reject(new Error(message.error.message)) : task.resolve(message.result)
    } else if (message.method === 'Runtime.executionContextCreated') {
      const context = message.params.context
      if (context.auxData?.frameId) contexts.set(context.auxData.frameId, context.id)
    } else if (message.method === 'Network.requestWillBeSent') {
      const request = message.params.request
      if (/overview|relation|flow|map|portal|menu/i.test(request.url)) {
        requests.push({ method: request.method, url: request.url.replace(/([?&](?:pwd|token|sid|user)=)[^&]*/gi, '$1[REDACTED]') })
      }
    }
  }

  const send = (method, params = {}, timeout = 20000) => new Promise((resolve, reject) => {
    const id = ++sequence
    const timer = setTimeout(() => { pending.delete(id); reject(new Error(`${method} timeout`)) }, timeout)
    pending.set(id, {
      resolve: (value) => { clearTimeout(timer); resolve(value) },
      reject: (error) => { clearTimeout(timer); reject(error) },
    })
    ws.send(JSON.stringify({ id, method, params }))
  })
  const evaluate = async (expression, contextId) => {
    const result = await send('Runtime.evaluate', { expression, contextId, returnByValue: true, awaitPromise: true })
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text)
    return result.result?.value
  }
  const frameTree = async () => (await send('Page.getFrameTree')).frameTree
  const findFrame = (node, text) => {
    if (node.frame.url.includes(text)) return node.frame
    for (const child of node.childFrames || []) {
      const found = findFrame(child, text)
      if (found) return found
    }
    return null
  }
  return { ws, send, evaluate, frameTree, findFrame, contexts, requests }
}

async function clickByText(cdp, text) {
  const position = JSON.parse(await cdp.evaluate(`(() => {
    const visible = (element) => {
      const rect = element.getBoundingClientRect()
      const style = getComputedStyle(element)
      return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden'
    }
    const element = [...document.querySelectorAll('button,a,span,div,li')]
      .find((item) => visible(item) && (item.textContent || '').trim() === ${JSON.stringify(text)})
    if (!element) return null
    const rect = element.getBoundingClientRect()
    return JSON.stringify({ x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 })
  })()`))
  if (!position) return false
  await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: position.x, y: position.y, button: 'left', clickCount: 1 })
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: position.x, y: position.y, button: 'left', clickCount: 1 })
  return true
}

async function clickBySelector(cdp, selector) {
  const position = JSON.parse(await cdp.evaluate(`(() => {
    const element = document.querySelector(${JSON.stringify(selector)})
    if (!element) return null
    const rect = element.getBoundingClientRect()
    const style = getComputedStyle(element)
    if (!rect.width || !rect.height || style.display === 'none' || style.visibility === 'hidden') return null
    return JSON.stringify({ x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 })
  })()`))
  if (!position) return false
  await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: position.x, y: position.y, button: 'left', clickCount: 1 })
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: position.x, y: position.y, button: 'left', clickCount: 1 })
  return true
}

async function waitForText(cdp, text, attempts = 40) {
  for (let index = 0; index < attempts; index += 1) {
    const found = await cdp.evaluate(`(document.body?.innerText || '').includes(${JSON.stringify(text)})`)
    if (found) return true
    await sleep(250)
  }
  return false
}

async function frameContext(cdp, urlPart, attempts = 60) {
  for (let index = 0; index < attempts; index += 1) {
    const frame = cdp.findFrame(await cdp.frameTree(), urlPart)
    if (frame) {
      let contextId = cdp.contexts.get(frame.id)
      if (!contextId) {
        const world = await cdp.send('Page.createIsolatedWorld', {
          frameId: frame.id,
          worldName: `overview-${urlPart.replace(/\W/g, '-')}`,
          grantUniveralAccess: true,
        })
        contextId = world.executionContextId
      }
      return { frame, contextId }
    }
    await sleep(250)
  }
  return null
}

async function dumpContext(cdp, contextId) {
  return JSON.parse(await cdp.evaluate(`(() => {
    const visible = (element) => {
      const rect = element.getBoundingClientRect()
      const style = getComputedStyle(element)
      return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden'
    }
    const rectOf = (element) => {
      const rect = element.getBoundingClientRect()
      return { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) }
    }
    return JSON.stringify({
      url: location.href,
      title: document.title,
      body: (document.body.innerText || '').slice(0, 30000),
      leaves: [...document.querySelectorAll('a,li,div,span,td,p,h1,h2,h3,h4')]
        .filter((element) => visible(element) && element.children.length === 0 && (element.textContent || '').trim())
        .map((element) => ({
          text: (element.textContent || '').trim(),
          tag: element.tagName,
          id: element.id,
          className: (element.className || '').toString(),
          rect: rectOf(element),
          href: element.getAttribute('href'),
          onclick: element.getAttribute('onclick'),
          attrs: [...element.attributes].map((attribute) => [attribute.name, attribute.value]),
        }))
        .filter((item) => item.text.length < 160)
        .slice(0, 4000),
      iframes: [...document.querySelectorAll('iframe')].filter(visible).map((frame) => ({
        src: frame.src,
        id: frame.id,
        name: frame.name,
        rect: rectOf(frame),
      })),
    })
  })()`, contextId))
}

async function clickTextInContext(cdp, contextId, text) {
  return cdp.evaluate(`(() => {
    const visible = (element) => {
      const rect = element.getBoundingClientRect()
      const style = getComputedStyle(element)
      return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden'
    }
    const element = [...document.querySelectorAll('button,a,span,div,li,td')]
      .find((item) => visible(item) && (item.textContent || '').trim() === ${JSON.stringify(text)})
    if (!element) return false
    element.click()
    return true
  })()`, contextId)
}

async function screenshot(cdp, name) {
  const image = await cdp.send('Page.captureScreenshot', { format: 'png' })
  fs.writeFileSync(path.join(OUT, name), Buffer.from(image.data, 'base64'))
}

async function login(cdp) {
  await cdp.send('Page.navigate', { url: 'https://t.chanjet.com/tplus/view/login.html' })
  await sleep(6500)
  if (!await clickByText(cdp, '立即体验')) throw new Error('未找到立即体验')

  let industry = null
  for (let index = 0; index < 50; index += 1) {
    const frame = cdp.findFrame(await cdp.frameTree(), 'selectRoles')
    if (frame) {
      let contextId = cdp.contexts.get(frame.id)
      if (!contextId) {
        const world = await cdp.send('Page.createIsolatedWorld', { frameId: frame.id, worldName: 'overview-login', grantUniveralAccess: true })
        contextId = world.executionContextId
      }
      industry = await cdp.evaluate(`(() => {
        const item = [...document.querySelectorAll('*')].find((node) => (node.textContent || '').trim() === '机械行业')
        if (!item) return null
        item.click()
        return item.textContent.trim()
      })()`, contextId)
      if (industry) break
    }
    await sleep(1000)
  }
  if (!industry) throw new Error('未找到机械行业')

  for (let index = 0; index < 120; index += 1) {
    const url = (await cdp.frameTree()).frame.url
    if (url.includes('/portal/portal.html')) break
    await sleep(1000)
  }
  await sleep(6000)
  return industry
}

async function dumpPage(cdp) {
  return JSON.parse(await cdp.evaluate(`(() => {
    const visible = (element) => {
      const rect = element.getBoundingClientRect()
      const style = getComputedStyle(element)
      return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden'
    }
    const rectOf = (element) => {
      const rect = element.getBoundingClientRect()
      return { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) }
    }
    return JSON.stringify({
      url: location.href,
      title: document.title,
      body: (document.body.innerText || '').slice(0, 20000),
      leaves: [...document.querySelectorAll('a,li,div,span,td')]
        .filter((element) => visible(element) && element.children.length === 0 && (element.textContent || '').trim())
        .map((element) => ({
          text: (element.textContent || '').trim(),
          tag: element.tagName,
          id: element.id,
          className: (element.className || '').toString(),
          rect: rectOf(element),
          href: element.getAttribute('href'),
          onclick: element.getAttribute('onclick'),
          attrs: [...element.attributes]
            .filter((attribute) => attribute.name.startsWith('data-') || attribute.name.includes('code'))
            .map((attribute) => [attribute.name, attribute.value]),
        }))
        .filter((item) => item.text.length < 120)
        .slice(0, 2000),
      iframes: [...document.querySelectorAll('iframe')].filter(visible).map((frame) => ({ src: frame.src, id: frame.id, name: frame.name, rect: rectOf(frame) })),
    })
  })()`))
}

async function run(attempt) {
  const profile = path.join(os.tmpdir(), `tplus-business-overview-${Date.now()}-${attempt}`)
  const edge = edgePath()
  fs.mkdirSync(profile, { recursive: true })
  const child = spawn(edge, [
    '--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
    '--no-first-run', '--no-default-browser-check', '--disable-gpu', '--disable-features=msEdgeSignin,msEdgeSync,msEdgeOnRamp',
    '--window-size=1600,1000', '--force-device-scale-factor=1', 'about:blank',
  ], { stdio: 'ignore' })
  let cdp
  try {
    await waitJson(`http://127.0.0.1:${PORT}/json/version`)
    const targets = await waitJson(`http://127.0.0.1:${PORT}/json/list`)
    const page = targets.find((target) => target.type === 'page' && target.url === 'about:blank') || targets.find((target) => target.type === 'page')
    cdp = await connect(page)
    await cdp.send('Page.enable'); await cdp.send('Runtime.enable'); await cdp.send('Network.enable')
    const industry = await login(cdp)
    let portal = await dumpPage(cdp)
    if (portal.body.includes('长时间未操作或相同账号在另外地点登录')) {
      return { retry: true, reason: '演示账号会话冲突', industry, portal }
    }
    await screenshot(cdp, '01-mechanical-portal.png')

    // 门户首次进入会弹“消息通知”，关闭后才能点击左侧一级菜单。
    if (portal.body.includes('消息通知')) {
      await clickByText(cdp, '×')
      await sleep(500)
    }
    const clicked = await clickBySelector(cdp, 'li.menufist_ISC') || await clickByText(cdp, '智能供应链')
    if (!clicked) throw new Error('未找到智能供应链一级菜单')
    await sleep(2000)
    const menu = await dumpPage(cdp)
    fs.writeFileSync(path.join(OUT, 'isc-menu.json'), JSON.stringify(menu, null, 2))
    await screenshot(cdp, '02-intelligent-supply-chain.png')

    const iscContext = await frameContext(cdp, 'menuCode=ISC')
    if (!iscContext) throw new Error('未找到智能供应链 iframe')
    const iscDesk = await dumpContext(cdp, iscContext.contextId)
    fs.writeFileSync(path.join(OUT, 'isc-idesk.json'), JSON.stringify(iscDesk, null, 2))

    const overviewClicked = await clickTextInContext(cdp, iscContext.contextId, '业务总览')
    await sleep(1500)

    const productionClicked = await clickTextInContext(cdp, iscContext.contextId, '生产管理')
    await sleep(2500)
    const productionRelation = await dumpContext(cdp, iscContext.contextId)
    fs.writeFileSync(path.join(OUT, 'production-relation.json'), JSON.stringify(productionRelation, null, 2))
    await screenshot(cdp, '03-production-relation.png')

    const targetsAfterOverview = await waitJson(`http://127.0.0.1:${PORT}/json/list`, 3)
    const framesAfterOverview = []
    const collectFrames = (node) => {
      framesAfterOverview.push({ id: node.frame.id, url: node.frame.url, name: node.frame.name })
      for (const child of node.childFrames || []) collectFrames(child)
    }
    collectFrames(await cdp.frameTree())

    return {
      retry: false,
      industry,
      portal,
      menu,
      iscDesk,
      overviewClicked,
      productionClicked,
      productionRelation,
      targetsAfterOverview: targetsAfterOverview
        .filter((target) => target.type === 'page')
        .map((target) => ({ id: target.id, title: target.title, url: target.url })),
      framesAfterOverview,
      requests: cdp.requests,
    }
  } finally {
    try { cdp?.ws.close() } catch {}
    child.kill('SIGTERM')
    await sleep(1000)
    fs.rmSync(profile, { recursive: true, force: true })
  }
}

async function main() {
  fs.mkdirSync(OUT, { recursive: true })
  let result
  for (let attempt = 1; attempt <= 4; attempt += 1) {
    result = await run(attempt)
    console.log(`[attempt ${attempt}] ${result.retry ? result.reason : '门户可用'}`)
    if (!result.retry) break
    await sleep(1500)
  }
  if (!result || result.retry) throw new Error('连续分配到冲突的体验账号')
  fs.writeFileSync(path.join(OUT, 'portal-menu-probe.json'), JSON.stringify(result, null, 2).replace(/([?&](?:pwd|token|sid|user)=[^&"\\]*)/gi, (value) => value.replace(/=.*/, '=[REDACTED]')))
  const hits = result.iscDesk.leaves.filter((item) => /业务总览|生产管理|库存|采购|销售|业务流程/.test(item.text))
  console.log(JSON.stringify({
    industry: result.industry,
    url: result.iscDesk.url,
    overviewClicked: result.overviewClicked,
    productionClicked: result.productionClicked,
    hits,
    body: result.iscDesk.body.slice(0, 10000),
    productionBody: result.productionRelation.body.slice(0, 16000),
    targetsAfterOverview: result.targetsAfterOverview,
    framesAfterOverview: result.framesAfterOverview,
  }, null, 2))
}

main().catch((error) => { console.error('[FAILED]', error.message); process.exit(1) })