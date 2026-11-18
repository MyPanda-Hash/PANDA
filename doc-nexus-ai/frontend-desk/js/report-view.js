/**
 * 报表查看抽屉：日报/周报/月报生成后，就地打开看正文。
 *
 * 为什么需要它：报表是投递到「对话」的后端会话里的，而对话页已经换成
 * chat-demo（浏览器本地存储），从对话界面翻不到这些报表。
 * 所以简报这里直接按 sessionId 把正文取回来展示。
 */
import { CHAT, downloadReportPptx } from './api.js'
import { renderReportDeck } from './report-deck.js'
import { el, clear, toast } from './util.js'

let current = null

export function closeReport() {
  if (current) current.remove()
  current = null
}

export async function openReport({ label, title, sessionId, kind }) {
  closeReport()

  const body = el('div', { cls: 'report-view__body' }, [
    el('div', { cls: 'report-view__loading', text: '读取报表…' }),
  ])
  const closeBtn = el('button', {
    cls: 'btn btn--text btn--sm',
    attrs: { type: 'button' },
    text: '关闭',
  })
  closeBtn.addEventListener('click', closeReport)

  // 导出 PPTX：企业里日报/周报要拿去过会，纯文本不够用
  const exportBtn = el('button', {
    cls: 'btn btn--sm',
    attrs: { type: 'button' },
    text: '导出 PPTX',
  })
  if (!kind) {
    exportBtn.hidden = true
  } else {
    exportBtn.addEventListener('click', async () => {
      exportBtn.disabled = true
      const before = exportBtn.textContent
      exportBtn.textContent = '生成中…'
      try {
        const name = await downloadReportPptx(kind, sessionId)
        toast('已导出：' + name)
      } catch (err) {
        toast((err && err.message) || '导出失败', 'error')
      } finally {
        exportBtn.disabled = false
        exportBtn.textContent = before
      }
    })
  }

  const panel = el('div', { cls: 'drawer__panel report-view__panel' }, [
    el('div', { cls: 'drawer__head report-view__head' }, [
      el('div', {}, [
        el('h2', { text: label || '报表' }),
        el('p', { cls: 'report-view__sub', text: title || '' }),
      ]),
      el('div', { cls: 'report-view__acts' }, [exportBtn, closeBtn]),
    ]),
    body,
  ])

  const root = el('div', {
    cls: 'drawer report-view',
    attrs: { role: 'dialog', 'aria-modal': 'true', 'aria-label': label || '报表' },
  }, [el('div', { cls: 'drawer__mask', on: { click: closeReport } }), panel])

  const overlay = document.getElementById('overlayRoot') || document.body
  overlay.append(root)
  current = root

  try {
    const payload = await CHAT.session(sessionId, 5)
    const data = (payload && payload.data) || payload || {}
    const messages = data.messages || []
    const last = [...messages].reverse().find((m) => String(m.role) === 'assistant')
    const content = last ? String(last.content || '') : ''
    clear(body)
    if (!content.trim()) {
      body.append(el('div', { cls: 'report-view__loading', text: '这条报表没有正文，可以点「重新生成」。' }))
      return
    }
    // 用「文档版式」渲染：指标卡 / 分布条 / 警示条目 / 责任人卡片，
    // 让网页里就能看到接近 PPT 的层级，而不是一屏纯文本。
    const prose = el('div', { cls: 'rdeck' })
    renderReportDeck(prose, content)
    body.append(prose)
  } catch (err) {
    clear(body)
    body.append(el('div', { cls: 'report-view__loading', text: '读取失败：' + ((err && err.message) || '请稍后重试') }))
  }
}