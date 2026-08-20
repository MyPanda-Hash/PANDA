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
      errs.push((msg.params.exceptionDetails.exception ? msg.params.exceptionDetails.exception.description : msg.params.exceptionDetails.text).slice(0, 500))
    }
  }
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++msgId; pending.set(id, { resolve, reject })
    ws.send(JSON.stringify({ id, method, params }))
  })
  await send('Page.enable'); await send('Runtime.enable')
  await send('Page.reload', { ignoreCache: true })
  await sleep(10000)
  console.log('reload 后异常:', errs.length ? errs.slice(0, 5) : '无')
  const r = await send('Runtime.evaluate', { expression: `(() => JSON.stringify({ appHtml: (document.querySelector('#app') || {}).innerHTML ? document.querySelector('#app').innerHTML.slice(0, 100) : 'no-app', bodyLen: (document.body.innerText || '').length }))()`, returnByValue: true })
  console.log(r.result.value)
  ws.close()
}
main().catch((e) => { console.log('ERR', e.message); process.exit(1) })