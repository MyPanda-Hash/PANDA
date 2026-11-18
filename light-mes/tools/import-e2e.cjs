// import-e2e.cjs: 完整导入链路 E2E（FINISH_IN 新增弹窗 → 导入 xlsx → 预览 → 导入 → 明细追加 → 保存）
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
async function main() {
  const res = await fetch('http://127.0.0.1:9222/json/list')
  const targets = await res.json()
  const page = targets.find((t) => t.type === 'page')
  const ws = new WebSocket(page.webSocketDebuggerUrl)
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
  let msgId = 0
  const pending = new Map()
  const errs = []
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data)
    if (msg.id && pending.has(msg.id)) {
      const p = pending.get(msg.id); pending.delete(msg.id)
      msg.error ? p.reject(new Error(msg.error.message)) : p.resolve(msg.result)
    } else if (msg.method === 'Runtime.exceptionThrown') {
      errs.push((msg.params.exceptionDetails.exception ? msg.params.exceptionDetails.exception.description : msg.params.exceptionDetails.text).slice(0, 200))
    }
  }
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++msgId; pending.set(id, { resolve, reject })
    ws.send(JSON.stringify({ id, method, params }))
  })
  const evalMain = async (expression) => {
    const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true, timeout: 20000 })
    if (r.exceptionDetails) throw new Error('eval: ' + JSON.stringify(r.exceptionDetails).slice(0, 300))
    return r.result.value
  }
  await send('Page.enable'); await send('Runtime.enable')
  await send('Page.navigate', { url: 'http://localhost:5173/#/panelx/list/FINISH_IN?e2e=' + Date.now() })
  await sleep(4000)
  // 点击新增（工具栏第一个主按钮）
  const added = await evalMain(`(() => {
    const btns = [...document.querySelectorAll('.tb-main')];
    const nb = btns.find((b) => (b.textContent || '').includes('\u65b0\u589e'));
    if (!nb) return 'no-new-btn';
    nb.click(); return 'clicked';
  })()`)
  console.log('新增:', added)
  await sleep(2500)
  // 打开导入对话框（表单页工具栏「导入」主按钮）
  const imp = await evalMain(`(() => {
    const btns = [...document.querySelectorAll('.tb-main')];
    const ib = btns.find((b) => (b.textContent || '').includes('\u5bfc\u5165'));
    if (!ib) return 'no-import-btn';
    ib.click(); return 'clicked';
  })()`)
  console.log('导入按钮:', imp)
  await sleep(1500)
  // 文件 input 赋值
  const files = await send('DOM.getDocument')
  const input = await evalMain(`(() => {
    const el = document.querySelector('input[type=file]');
    if (!el) return null;
    return { id: el.getAttribute('__cdp'), exists: true };
  })()`)
  // 用 Runtime 直接操作：找到 input 元素并设置文件（CDP DOM.setFileInputFiles 需要 nodeId）
  const doc = await send('DOM.getDocument', { depth: -1 })
  const q = await send('DOM.querySelector', { nodeId: doc.root.nodeId, selector: 'input[type=file]' })
  console.log('file input nodeId:', q.nodeId)
  if (q.nodeId) {
    await send('DOM.setFileInputFiles', { nodeId: q.nodeId, files: ['F:/INCER/light-mes/tools/import-test.xlsx'] })
    console.log('已设置文件')
  }
  await sleep(2500)
  // 验证预览
  const preview = await evalMain(`(() => {
    const rows = document.querySelectorAll('.imp-preview .el-table__body tr');
    const txt = (document.querySelector('.imp-match') || {}).textContent || '';
    return JSON.stringify({ previewRows: rows.length, matchText: txt.trim() });
  })()`)
  console.log('预览:', preview)
  // 点击导入按钮
  const done = await evalMain(`(() => {
    const btns = [...document.querySelectorAll('.el-dialog__footer button')];
    const b = btns.find((x) => (x.textContent || '').includes('\u5bfc\u5165'));
    if (!b) return 'no-import-confirm';
    b.click(); return 'clicked';
  })()`)
  console.log('确认导入:', done)
  await sleep(2000)
  // 验证明细行数
  const rows = await evalMain(`(() => {
    const trs = document.querySelectorAll('.el-dialog .el-table__body tr, .panel-form .el-table__body tr');
    return JSON.stringify({ detailTrs: trs.length });
  })()`)
  console.log('明细:', rows)
  console.log('页面异常:', errs.length ? errs.slice(0, 3) : '无')
  const shot = await send('Page.captureScreenshot', { format: 'png' })
  require('node:fs').writeFileSync('docs/ref/mes-live/import-e2e.png', Buffer.from(shot.data, 'base64'))
  console.log('screenshot saved')
  ws.close()
}
main().catch((e) => { console.log('ERR', e.message); process.exit(1) })