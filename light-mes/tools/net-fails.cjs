const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
async function main() {
  const res = await fetch('http://127.0.0.1:9222/json/list')
  const targets = await res.json()
  const page = targets.find((t) => t.type === 'page')
  const ws = new WebSocket(page.webSocketDebuggerUrl)
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
  let msgId = 0
  const pending = new Map()
  const fails = []
  const bad = []
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data)
    if (msg.id && pending.has(msg.id)) {
      const p = pending.get(msg.id); pending.delete(msg.id)
      msg.error ? p.reject(new Error(msg.error.message)) : p.resolve(msg.result)
    } else if (msg.method === 'Network.responseReceived') {
      const st = msg.params.response.status
      const u = msg.params.response.url
      if (st >= 400) bad.push(st + ' ' + u.replace('http://localhost:5173', '').slice(0, 100))
    } else if (msg.method === 'Network.loadingFailed') {
      fails.push(msg.params.errorText + ' ' + (msg.params.blockedReason || ''))
    }
  }
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++msgId; pending.set(id, { resolve, reject })
    ws.send(JSON.stringify({ id, method, params }))
  })
  await send('Page.enable'); await send('Runtime.enable'); await send('Network.enable')
  await send('Page.navigate', { url: 'http://localhost:5173/#/dashboard' })
  await sleep(12000)
  console.log('HTTP >=400:', bad.length ? bad.slice(0, 10) : '无')
  console.log('加载失败:', fails.length ? fails.slice(0, 5) : '无')
  ws.close()
}
main().catch((e) => { console.log('ERR', e.message); process.exit(1) })