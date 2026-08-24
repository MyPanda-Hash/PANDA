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
        requests.push({
          method: request.method,
          url: request.url.replace(/([?&](?:pwd|token|sid|user|TaskSessionID)=)[^&]*/gi, '$1[REDACTED]'),
        })
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
    const tree = await cdp.frameTree()
    const find = (node) => {
      if (node.frame.url.includes(urlPart) || node.frame.name === urlPart) return node.frame
      for (const child of node.childFrames || []) {
        const found = find(child)
        if (found) return found
      }
      return null
    }
    const frame = find(tree)
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

async function clickTextByMouseInFrame(cdp, contextId, frameSelector, text) {
  const local = JSON.parse(await cdp.evaluate(`(() => {
    const visible = (element) => {
      const rect = element.getBoundingClientRect()
      const style = getComputedStyle(element)
      return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden'
    }
    const element = [...document.querySelectorAll('button,a,span,div,li,td')]
      .find((item) => visible(item) && (item.textContent || '').trim() === ${JSON.stringify(text)})
    if (!element) return null
    const rect = element.getBoundingClientRect()
    return JSON.stringify({ x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 })
  })()`, contextId))
  if (!local) return false
  const frame = JSON.parse(await cdp.evaluate(`(() => {
    const element = document.querySelector(${JSON.stringify(frameSelector)})
    if (!element) return null
    const rect = element.getBoundingClientRect()
    return JSON.stringify({ x: rect.x, y: rect.y })
  })()`))
  if (!frame) return false
  const x = frame.x + local.x
  const y = frame.y + local.y
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y })
  await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 })
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 })
  return { x, y }
}

async function clickPortalTab(cdp, text) {
  const position = JSON.parse(await cdp.evaluate(`(() => {
    const visible = (element) => {
      const rect = element.getBoundingClientRect()
      const style = getComputedStyle(element)
      return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden'
    }
    const candidates = [...document.querySelectorAll('span,div,a,li')]
      .filter((element) => {
        if (!visible(element) || (element.textContent || '').trim() !== ${JSON.stringify(text)}) return false
        const rect = element.getBoundingClientRect()
        return rect.x > 120 && rect.y >= 45 && rect.y < 90
      })
    const element = candidates[0]
    if (!element) return null
    const rect = element.getBoundingClientRect()
    return JSON.stringify({ x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 })
  })()`))
  if (!position) return false
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: position.x, y: position.y })
  await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: position.x, y: position.y, button: 'left', clickCount: 1 })
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: position.x, y: position.y, button: 'left', clickCount: 1 })
  return true
}

function allFrames(tree) {
  const frames = []
  const collect = (node) => {
    frames.push(node.frame)
    for (const child of node.childFrames || []) collect(child)
  }
  collect(tree)
  return frames
}

async function contextForFrame(cdp, frame) {
  let contextId = cdp.contexts.get(frame.id)
  if (!contextId) {
    const world = await cdp.send('Page.createIsolatedWorld', {
      frameId: frame.id,
      worldName: `overview-frame-${frame.id}`,
      grantUniveralAccess: true,
    })
    contextId = world.executionContextId
  }
  return contextId
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
    if (!await waitForText(cdp, '智能供应链', 120)) {
      throw new Error('门户一级菜单未加载完成')
    }
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
    let clicked = false
    let iscContext = null
    for (let index = 0; index < 3 && !iscContext; index += 1) {
      clicked = await clickBySelector(cdp, 'li.menufist_ISC') || await clickByText(cdp, '智能供应链')
      if (!clicked) throw new Error('未找到智能供应链一级菜单')
      await sleep(2000)
      iscContext = await frameContext(cdp, 'menuCode=ISC', 40)
    }
    if (!iscContext) throw new Error('未找到智能供应链 iframe')
    const menu = await dumpPage(cdp)
    fs.writeFileSync(path.join(OUT, 'isc-menu.json'), JSON.stringify(menu, null, 2))
    await screenshot(cdp, '02-intelligent-supply-chain.png')

    const iscDesk = await dumpContext(cdp, iscContext.contextId)
    fs.writeFileSync(path.join(OUT, 'isc-idesk.json'), JSON.stringify(iscDesk, null, 2))

    const overviewClicked = await clickTextInContext(cdp, iscContext.contextId, '业务总览')
    await sleep(1500)

    const modules = [
      ['production', '生产管理'],
      ['outsource', '委外管理'],
      ['quality', '质检管理'],
      ['sales', '销售管理'],
      ['retail', '零售管理'],
      ['supply-collaboration', '供应链协同'],
      ['inventory', '库存核算'],
      ['purchase', '采购管理'],
      ['distribution', '配货管理'],
      ['logistics', '物流管理'],
      ['mobile-warehouse', '移动仓管'],
      ['serial-number', '序列号管理'],
      ['batch', '批号管理'],
      ['barcode', '条码管理'],
      ['kuaidiniao', '快递鸟'],
      ['cainiao', '菜鸟'],
      ['isv', 'ISV对接'],
      ['third-party', '第三方系统'],
      ['open-api', 'API开放接口'],
    ]
    const moduleRelations = []
    let productionClicked = null
    let productionRelation = null

    for (const [code, moduleName] of modules) {
      if (!await clickPortalTab(cdp, '智能供应链')) {
        moduleRelations.push({ code, moduleName, status: 'tab-not-found' })
        continue
      }
      await sleep(350)
      const moduleIscContext = await frameContext(cdp, 'ISC_iframe', 40)
      if (!moduleIscContext) {
        moduleRelations.push({ code, moduleName, status: 'overview-frame-not-found' })
        continue
      }

      const clickedModule = await clickTextByMouseInFrame(cdp, moduleIscContext.contextId, '#ISC_iframe', moduleName)
      if (moduleName === '生产管理') productionClicked = clickedModule
      if (!clickedModule) {
        moduleRelations.push({ code, moduleName, status: 'module-not-found' })
        continue
      }
      await sleep(1400)

      const frames = allFrames(await cdp.frameTree())
      const relationFrame = frames.find((frame) => {
        try {
          return decodeURIComponent(frame.url).includes(`taskID=${moduleName}`)
        } catch {
          return frame.url.includes(moduleName)
        }
      })
      if (!relationFrame) {
        const overviewState = await dumpContext(cdp, moduleIscContext.contextId)
        const unavailable = overviewState.body.includes('暂未开通此应用')
        moduleRelations.push({
          code,
          moduleName,
          status: unavailable ? 'unavailable' : 'no-relation-frame',
        })
        continue
      }

      const relationContextId = await contextForFrame(cdp, relationFrame)
      const relation = await dumpContext(cdp, relationContextId)
      const savedRelation = {
        ...relation,
        url: relation.url.replace(/([?&]TaskSessionID=)[^&]*/i, '$1[REDACTED]'),
      }
      fs.writeFileSync(path.join(OUT, `${code}-relation.json`), JSON.stringify(savedRelation, null, 2))
      await screenshot(cdp, `${String(moduleRelations.length + 3).padStart(2, '0')}-${code}-relation.png`)
      moduleRelations.push({
        code,
        moduleName,
        status: 'opened',
        frameName: relationFrame.name,
        menuCode: (relationFrame.url.match(/[?&]menuCode=([^&]+)/i) || [])[1] || '',
        relation: savedRelation,
      })
      if (moduleName === '生产管理') productionRelation = savedRelation
    }

    fs.writeFileSync(path.join(OUT, 'module-relations.json'), JSON.stringify(moduleRelations, null, 2))

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
      moduleRelations,
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
  const samples = []
  const aggregate = new Map()
  const priorityModules = new Set(['production', 'outsource', 'quality', 'sales', 'inventory', 'purchase', 'distribution'])
  for (let attempt = 1; attempt <= 8; attempt += 1) {
    try {
      const sample = await run(attempt)
      console.log(`[attempt ${attempt}] ${sample.retry ? sample.reason : '门户可用'}`)
      if (!sample.retry) {
        samples.push(sample)
        result = sample
        for (const module of sample.moduleRelations || []) {
          const current = aggregate.get(module.code)
          if (!current || (current.status !== 'opened' && module.status === 'opened')) {
            aggregate.set(module.code, module)
          }
        }
        const openedPriority = [...priorityModules].filter((code) => aggregate.get(code)?.status === 'opened')
        console.log(`[attempt ${attempt}] 已抓取重点模块：${openedPriority.join(', ') || '无'}`)
        if (openedPriority.length === priorityModules.size) break
      }
    } catch (error) {
      console.log(`[attempt ${attempt}] ${error.message}，重新申请体验账号`)
    }
    await sleep(1500)
  }
  if (!result) throw new Error('连续分配到不可用的体验账号')
  result.moduleRelations = [...aggregate.values()]
  result.productionRelation = aggregate.get('production')?.relation || result.productionRelation
  result.productionClicked = aggregate.has('production') || result.productionClicked
  result.samples = samples.map((sample) => ({
    industry: sample.industry,
    moduleStatuses: (sample.moduleRelations || []).map((module) => ({
      code: module.code,
      moduleName: module.moduleName,
      status: module.status,
      menuCode: module.menuCode,
    })),
  }))
  fs.writeFileSync(path.join(OUT, 'module-relations.json'), JSON.stringify(result.moduleRelations, null, 2))
  fs.writeFileSync(
    path.join(OUT, 'portal-menu-probe.json'),
    JSON.stringify(result, null, 2).replace(
      /([?&](?:pwd|token|sid|user|TaskSessionID)=[^&"\\]*)/gi,
      (value) => value.replace(/=.*/, '=[REDACTED]'),
    ),
  )
  const hits = result.iscDesk.leaves.filter((item) => /业务总览|生产管理|库存|采购|销售|业务流程/.test(item.text))
  console.log(JSON.stringify({
    industry: result.industry,
    url: result.iscDesk.url,
    overviewClicked: result.overviewClicked,
    productionClicked: result.productionClicked,
    hits,
    body: result.iscDesk.body.slice(0, 10000),
    productionBody: result.productionRelation?.body.slice(0, 16000) || '',
    modules: result.moduleRelations.map((module) => ({
      code: module.code,
      moduleName: module.moduleName,
      status: module.status,
      menuCode: module.menuCode,
      body: module.relation?.body.slice(0, 3000),
    })),
    targetsAfterOverview: result.targetsAfterOverview,
    framesAfterOverview: result.framesAfterOverview,
  }, null, 2))
}

main().catch((error) => { console.error('[FAILED]', error.message); process.exit(1) })