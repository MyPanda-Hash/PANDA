/**
 * 对话页：直接嵌入 DeepSeek 官方开源 chat 前端（deepseek-ai/chat-demo，MIT）。
 *
 * 为什么换成它：
 * - 官方那份是「真·DeepSeek 会话」：流式、深度思考、智能搜索、多会话、
 *   代码高亮、公式、图表、附件、语音输入，全都在里面；
 * - 自己再仿一遍没有意义，直接用它才是「和官网一样」。
 *
 * 关键约定：
 * - key 不落到浏览器。chat-demo 原来要用户填 API Key，这里在它的 index.html
 *   里把 localStorage 的 api-key 换成了 MES 登录 token；后端 /anthropic/v1/messages
 *   验这个 token，再用「模型配置」里保存的真 key 调上游。
 * - 它有自己的会话存储（浏览器 IndexedDB），与后端会话列表暂不互通
 *   （2026-11-17 决定：先各自独立，等需要共享时再改它的 db.ts）。
 * - 原来的自研对话页已被替换并删除，没有保留回退副本；
 */

import { el, toast } from './util.js'
import { BRIEFING } from './api.js'
import { openReport } from './report-view.js'
import { appPath } from './util.js'

let frame = null

export async function mount(root, ctx) {
  const stage = el('div', { cls: 'chatds' })
  frame = el('iframe', {
    cls: 'chatds__frame',
    attrs: {
      src: appPath('chat-deepseek/'),
      title: '对话',
      // 语音输入与麦克风在 chat-demo 内部按需申请
      allow: 'microphone',
    },
  })
  stage.append(frame)
  root.append(stage)
  // 报表入口桥：chat-demo 里的 nxs-report 按钮通过 postMessage 回来
  uninstallReportBridge()
  installReportBridge()
  if (ctx && typeof ctx.setTitle === 'function') {
    ctx.setTitle('对话')
  }
}

export async function unmount() {
  uninstallReportBridge()
  if (frame) frame.remove()
  frame = null
}

/**
 * 对话里的报表入口。
 *
 * chat-demo 渲染消息时会把 `nxs-report:<kind>` 伪协议变成按钮，点击后 postMessage 过来；
 * 这里把 kind 换成真正的报表数据，再交给 report-view 打开。
 *
 * 为什么不直接把 http 链接塞进对话：报表正文存在后端会话里（按 sessionId 取），
 * 不是独立页面，做成链接只会跳到 404。
 */
let reportBridgeHandler = null

function installReportBridge() {
  reportBridgeHandler = async (event) => {
    const data = event.data
    if (!data || data.type !== 'nxs-report') return
    const kind = String(data.kind || '').trim()
    if (!kind) return
    try {
      // 报表列表走后端权威接口；角色取默认的 manager（和简报页首次加载一致）
      const payload = await BRIEFING.board({ role: 'manager' })
      const reports = ((payload && payload.data) || payload || {}).reports || []
      const hit = reports.find((r) => r.kind === kind)
      if (!hit || !hit.sessionId) {
        toast('这份报表还没生成，回简报页重新生成一次')
        return
      }
      await openReport({ label: hit.label, title: hit.title, sessionId: hit.sessionId, kind: hit.kind })
    } catch (err) {
      toast((err && err.message) || '打开报表失败', 'error')
    }
  }
  window.addEventListener('message', reportBridgeHandler)
}

/** 重复挂载时先摘掉旧监听，否则一次点击会开多次报表 */
function uninstallReportBridge() {
  if (reportBridgeHandler) {
    window.removeEventListener('message', reportBridgeHandler)
    reportBridgeHandler = null
  }
}
