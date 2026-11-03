const { spawn } = require('node:child_process')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const API = process.env.BOM_E2E_API || 'http://127.0.0.1:3308/api'
const WEB = process.env.BOM_E2E_WEB || 'http://127.0.0.1:4173'
const PORT = 9266
const PROFILE = path.join(os.tmpdir(), `bom-md-e2e-${Date.now()}`)
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

function edgePath() {
  return [
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  ].find(fs.existsSync)
}

async function waitJson(url, attempts = 80) {
  for (let index = 0; index < attempts; index++) {
    try {
      const response = await fetch(url)
      if (response.ok) return response.json()
    } catch {}
    await sleep(250)
  }
  throw new Error(`等待失败：${url}`)
}

async function connect(page) {
  const socket = new WebSocket(page.webSocketDebuggerUrl)
  await new Promise((resolve, reject) => {
    socket.onopen = resolve
    socket.onerror = reject
  })
  let id = 0
  const pending = new Map()
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data)
    if (!message.id || !pending.has(message.id)) return
    const promise = pending.get(message.id)
    pending.delete(message.id)
    if (message.error) promise.reject(new Error(message.error.message))
    else promise.resolve(message.result)
  }
  const send = (method, params = {}, timeout = 30000) => new Promise((resolve, reject) => {
    const requestId = ++id
    pending.set(requestId, { resolve, reject })
    setTimeout(() => {
      if (!pending.has(requestId)) return
      pending.delete(requestId)
      reject(new Error(`${method} 超时`))
    }, timeout)
    socket.send(JSON.stringify({ id: requestId, method, params }))
  })
  const evaluate = async (expression) => {
    const result = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails).slice(0, 500))
    return result.result.value
  }
  return { socket, send, evaluate }
}

async function api(pathname, token, options = {}) {
  const response = await fetch(API + pathname, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers || {}),
    },
  })
  const payload = await response.json()
  if (!response.ok || (payload.code !== undefined && payload.code !== 200)) {
    throw new Error(`API ${pathname} 失败：${JSON.stringify(payload)}`)
  }
  return payload.data ?? payload
}

async function main() {
  const login = await api('/auth/login', '', {
    method: 'POST',
    body: JSON.stringify({ userName: 'admin', password: '123456' }),
  })
  const token = login.token
  const inventory = await api('/px/queryFormDataList', token, {
    method: 'POST',
    body: JSON.stringify({ panelCode: 'INV', pageNo: 1, pageSize: 200 }),
  })
  const items = (inventory.list || []).flatMap((document) => document.detail?.items || [])
  const parent = items.find((item) => ['自制', '自制+外购'].includes(item['属性'])) || items[0]
  const child = items.find((item) => item['存货编码'] !== parent?.['存货编码'] && item['属性'] === '外购')
    || items.find((item) => item['存货编码'] !== parent?.['存货编码'])
  if (!parent || !child) throw new Error('存货档案不足，无法选择不同的父件和子件')

  const before = await api('/px/queryFormDataList', token, {
    method: 'POST',
    body: JSON.stringify({ panelCode: 'BOM', pageNo: 1, pageSize: 500 }),
  })
  const beforeNumbers = new Set((before.list || []).map((row) => row['编号']))
  let createdNo = ''
  let browser
  let cdp

  fs.mkdirSync(PROFILE, { recursive: true })
  browser = spawn(edgePath(), [
    '--headless=new',
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${PROFILE}`,
    '--no-first-run',
    '--disable-gpu',
    '--window-size=1600,1000',
    '--force-device-scale-factor=1',
    'about:blank',
  ], { stdio: 'ignore' })

  try {
    await waitJson(`http://127.0.0.1:${PORT}/json/version`)
    const pages = await waitJson(`http://127.0.0.1:${PORT}/json/list`)
    cdp = await connect(pages.find((target) => target.type === 'page'))
    await cdp.send('Page.enable')
    await cdp.send('Runtime.enable')
    await cdp.send('Page.navigate', { url: `${WEB}/?e2e=${Date.now()}` })
    await sleep(2500)
    await cdp.evaluate(`(() => {
      localStorage.setItem('mes_token', ${JSON.stringify(token)})
      localStorage.setItem('mes_user', JSON.stringify({ name: 'admin', roleId: 1, roleName: '管理员', isAdmin: true }))
      localStorage.setItem('mes_login_date', '2026-08-25')
      return true
    })()`)
    await cdp.send('Page.navigate', { url: `${WEB}/#/panelx/list/BOM?e2e=${Date.now()}` })
    await sleep(5000)

    const initial = await cdp.evaluate(`(() => {
      const text = document.body.innerText
      return {
        parentTitle: text.includes('父件（产成品/物料）'),
        childTitle: text.includes('子件：'),
        addChild: [...document.querySelectorAll('button')].some((button) => button.innerText.trim() === '新增子件'),
      }
    })()`)
    if (!initial.parentTitle || !initial.childTitle) throw new Error(`主从视图未显示：${JSON.stringify(initial)}`)

    await cdp.evaluate(`(async () => {
      const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
      const add = [...document.querySelectorAll('.tb-main')].find((element) => element.innerText.trim() === '新增')
      if (!add) throw new Error('未找到新增按钮')
      add.click()
      await sleep(2500)
      return true
    })()`)
    const afterAdd = await api('/px/queryFormDataList', token, {
      method: 'POST',
      body: JSON.stringify({ panelCode: 'BOM', pageNo: 1, pageSize: 500 }),
    })
    createdNo = (afterAdd.list || []).map((row) => row['编号']).find((number) => !beforeNumbers.has(number)) || ''
    if (!createdNo) throw new Error('新增后未找到测试 BOM 编号')

    const draftView = await cdp.evaluate(`(() => {
      const sections = [...document.querySelectorAll('.bom-md-sec')]
      return {
        parentCount: sections[0]?.querySelectorAll('.el-table__body-wrapper tbody tr').length || 0,
        childCount: sections[1]?.querySelectorAll('.el-table__body-wrapper tbody tr').length || 0,
        parentText: sections[0]?.innerText || '',
        childText: sections[1]?.innerText || '',
      }
    })()`)
    if (draftView.parentCount !== 1 || !draftView.parentText.includes('共 1 项')) {
      throw new Error(`父件表不是固定一项：${JSON.stringify(draftView)}`)
    }

    async function selectReference(sectionIndex, code) {
      await cdp.evaluate(`(() => {
        const section = document.querySelectorAll('.bom-md-sec')[${sectionIndex}]
        const button = section?.querySelector('.bom-ref')
        if (!button) throw new Error('未找到参照按钮')
        button.click()
        return true
      })()`)
      await sleep(1200)
      const selected = await cdp.evaluate(`(() => {
        const dialogs = [...document.querySelectorAll('.el-dialog')]
        const dialog = dialogs.find((element) => element.innerText.includes('参照选择'))
        if (!dialog) return { ok: false, reason: '参照弹窗未打开' }
        const rows = [...dialog.querySelectorAll('.el-table__body-wrapper tbody tr')]
        const row = rows.find((element) => element.innerText.includes(${JSON.stringify(code)}))
        if (!row) return { ok: false, reason: '参照行不存在', sample: rows.slice(0, 3).map((element) => element.innerText) }
        const checkbox = row.querySelector('.el-checkbox')
        if (!checkbox) return { ok: false, reason: '参照行无复选框' }
        checkbox.click()
        return { ok: true }
      })()`)
      if (!selected.ok) throw new Error(`选择参照 ${code} 失败：${JSON.stringify(selected)}`)
      await sleep(300)
      const confirmed = await cdp.evaluate(`(() => {
        const dialog = [...document.querySelectorAll('.el-dialog')]
          .find((element) => element.innerText.includes('参照选择'))
        if (!dialog) return { ok: false, reason: '确认前弹窗已关闭' }
        const confirm = [...dialog.querySelectorAll('button')].find((button) => button.innerText.includes('确定导入'))
        if (!confirm) return { ok: false, reason: '无确定导入按钮' }
        if (confirm.disabled) return { ok: false, reason: '确定导入按钮仍禁用' }
        confirm.click()
        return { ok: true }
      })()`)
      if (!confirmed.ok) throw new Error(`确认参照 ${code} 失败：${JSON.stringify(confirmed)}`)
      await sleep(800)
    }

    await selectReference(0, parent['存货编码'])
    await cdp.evaluate(`(() => {
      const button = [...document.querySelectorAll('.bom-md-sec:nth-child(2) button')]
        .find((element) => element.innerText.trim() === '新增子件')
      if (!button) throw new Error('未找到新增子件按钮')
      button.click()
      return true
    })()`)
    await sleep(500)
    await selectReference(1, child['存货编码'])

    const inputResult = await cdp.evaluate(`(() => {
      function cell(sectionIndex, label) {
        const section = document.querySelectorAll('.bom-md-sec')[sectionIndex]
        const table = section?.querySelector('.el-table')
        const headers = [...table.querySelectorAll('.el-table__header-wrapper th')]
        const index = headers.findIndex((header) => header.innerText.trim() === label)
        const row = table.querySelector('.el-table__body-wrapper tbody tr')
        return index >= 0 ? row?.children[index] : null
      }
      function setInput(sectionIndex, label, value) {
        const input = cell(sectionIndex, label)?.querySelector('input')
        if (!input) return false
        input.value = String(value)
        input.dispatchEvent(new Event('input', { bubbles: true }))
        input.dispatchEvent(new Event('change', { bubbles: true }))
        return true
      }
      const version = setInput(0, '版本号', 'V1.0')
      const quota = setInput(1, '定额数量', 0.5)
      const loss = setInput(1, '损耗率%', 2)
      const required = setInput(1, '需用数量', 0.5)
      const switcher = cell(0, '默认BOM')?.querySelector('.el-switch')
      if (switcher && !switcher.classList.contains('is-checked')) switcher.click()
      return { version, quota, loss, required }
    })()`)
    if (Object.values(inputResult).some((value) => !value)) throw new Error(`字段输入失败：${JSON.stringify(inputResult)}`)
    await sleep(600)

    const uiState = await cdp.evaluate(`(() => {
      const sections = [...document.querySelectorAll('.bom-md-sec')]
      return {
        parent: sections[0]?.innerText || '',
        child: sections[1]?.innerText || '',
        parentRows: sections[0]?.querySelectorAll('.el-table__body-wrapper tbody tr').length || 0,
        childRows: sections[1]?.querySelectorAll('.el-table__body-wrapper tbody tr').length || 0,
      }
    })()`)
    if (!uiState.parent.includes(parent['存货编码'])) {
      throw new Error(`父件带出失败：${JSON.stringify(uiState)}`)
    }
    if (!uiState.child.includes(child['存货编码'])) {
      throw new Error(`子件带出失败：${JSON.stringify(uiState)}`)
    }

    // 不手工保存，直接审核：列表页必须先自动保存当前父子表，再执行审核。
    await cdp.evaluate(`(() => {
      const audit = [...document.querySelectorAll('.tb-main')].find((element) => element.innerText.trim() === '审核')
      if (!audit) throw new Error('未找到审核按钮')
      audit.click()
      return true
    })()`)
    await sleep(500)
    const auditConfirmed = await cdp.evaluate(`(() => {
      const box = document.querySelector('.el-message-box')
      if (!box) return { ok: false, reason: '审核确认框未打开' }
      const confirm = [...box.querySelectorAll('button')].find((button) => button.innerText.includes('确认审核'))
      if (!confirm) return { ok: false, reason: '未找到确认审核按钮' }
      confirm.click()
      return { ok: true }
    })()`)
    if (!auditConfirmed.ok) throw new Error(`审核确认失败：${JSON.stringify(auditConfirmed)}`)
    await cdp.evaluate(`(async () => {
      const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
      await sleep(4000)
      return true
    })()`)

    const descriptor = await api(`/px/getFormDescriptor?panelCode=BOM&code=${encodeURIComponent(createdNo)}`, token)
    const children = descriptor.detailData?.children || descriptor.data?.detail?.children || []
    if (children.length !== 1) throw new Error(`保存后子件数错误：${children.length}`)
    const saved = children[0]
    const expected = {
      documentNo: createdNo,
      parentCode: parent['存货编码'],
      parentName: parent['存货名称'],
      version: 'V1.0',
      defaultBom: true,
      parentUnit: parent['计量单位'],
      productionQuantity: 1,
      childCode: child['存货编码'],
      childName: child['存货名称'],
      childSpec: child['规格型号'],
      childUnit: child['计量单位'],
      quota: 0.5,
      loss: 2,
      required: 0.5,
    }
    const actual = {
      documentNo: saved['物料清单编码'],
      parentCode: saved['父件编码'],
      parentName: saved['父件名称'],
      version: saved['版本号'],
      defaultBom: saved['默认BOM'],
      parentUnit: saved['计量单位'],
      productionQuantity: saved['生产数量'],
      childCode: saved['子件编码'],
      childName: saved['子件名称'],
      childSpec: saved['规格型号'],
      childUnit: saved['子件计量单位'],
      quota: saved['定额数量'],
      loss: saved['损耗率%'],
      required: saved['需用数量'],
    }
    for (const key of Object.keys(expected)) {
      if (String(actual[key] ?? '') !== String(expected[key] ?? '')) {
        throw new Error(`保存字段 ${key} 不一致：expected=${expected[key]} actual=${actual[key]}`)
      }
    }
    if (descriptor.data?.['单据状态'] !== '已审核') {
      throw new Error(`审核后状态错误：${descriptor.data?.['单据状态'] || ''}`)
    }

    const approvedView = await cdp.evaluate(`(() => {
      const sections = [...document.querySelectorAll('.bom-md-sec')]
      const body = document.body.innerText
      return {
        currentNo: document.querySelector('.doc-chip')?.innerText?.trim() || '',
        status: body.includes('已审核') ? '已审核' : '',
        parent: sections[0]?.innerText || '',
        child: sections[1]?.innerText || '',
        editableRefs: document.querySelectorAll('.bom-md .bom-ref').length,
        addChild: [...document.querySelectorAll('.bom-md button')].some((button) => button.innerText.trim() === '新增子件'),
      }
    })()`)
    if (!approvedView.currentNo.includes(createdNo) || approvedView.status !== '已审核') {
      throw new Error(`审核后未定位原单据：${JSON.stringify(approvedView)}`)
    }
    if (!approvedView.parent.includes(parent['存货编码']) || !approvedView.child.includes(child['存货编码'])) {
      throw new Error(`审核后父子数据显示失败：${JSON.stringify(approvedView)}`)
    }
    if (approvedView.editableRefs || approvedView.addChild) {
      throw new Error(`审核后仍处于编辑状态：${JSON.stringify(approvedView)}`)
    }

    await cdp.send('Page.navigate', { url: `${WEB}/#/panelx/list/BOM_FWD?e2e=${Date.now()}` })
    await sleep(2500)
    const readonly = await cdp.evaluate(`(() => ({
      hasMasterDetail: !!document.querySelector('.bom-md'),
      editableRefs: document.querySelectorAll('.bom-md .bom-ref').length,
      addChild: [...document.querySelectorAll('.bom-md button')].some((button) => button.innerText.trim() === '新增子件'),
    }))()`)
    if (!readonly.hasMasterDetail || readonly.editableRefs || readonly.addChild) {
      throw new Error(`正向查询只读状态错误：${JSON.stringify(readonly)}`)
    }

    console.log(JSON.stringify({
      pass: true,
      createdNo,
      parent: `${parent['存货编码']} ${parent['存货名称']}`,
      child: `${child['存货编码']} ${child['存货名称']}`,
      uiState,
      saved: actual,
      approvedView,
      readonly,
    }, null, 2))
  } finally {
    if (createdNo) {
      try {
        await api('/px/callButton', token, {
          method: 'POST',
          body: JSON.stringify({ panelCode: 'BOM', buttonName: '弃审', formData: { 编号: createdNo }, buttonParam: {} }),
        })
      } catch {}
      try {
        await api('/px/deleteForms', token, {
          method: 'POST',
          body: JSON.stringify({ panelCode: 'BOM', rowCodes: [createdNo] }),
        })
      } catch (error) {
        console.error(`清理测试 BOM 失败：${error.message}`)
      }
    }
    if (cdp?.socket) cdp.socket.close()
    if (browser) browser.kill()
    await sleep(300)
    try { fs.rmSync(PROFILE, { recursive: true, force: true }) } catch {}
  }
}

main().catch((error) => {
  console.error(`FATAL ${error.stack || error.message}`)
  process.exit(1)
})
