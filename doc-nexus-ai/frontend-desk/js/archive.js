/**
 * 会话归档：管理员看「谁在什么时候用过 AI、聊了什么」。
 *
 * 数据来自 /chat-archive（后端旁路归档）。对话正文本身仍留在各人浏览器里，
 * 这里看到的是归档快照——用于审计，不是给普通用户回看历史。
 */
import { CHAT_ARCHIVE } from './api.js'
import { el, clear } from './util.js'

let page = null

function describe(err) {
  if (!err) return '加载失败'
  if (err.status === 401) return '登录已失效，请重新登录'
  if (err.status === 403) return '只有管理员可以查看会话归档'
  if (err.status === 0) return '连不上服务，检查后端是否在运行'
  return err.message || '加载失败'
}

function fmtTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const pad = (n) => String(n).padStart(2, '0')
  return pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes())
}

function buildPage() {
  const ownerInput = el('input', {
    cls: 'input arc__filter',
    attrs: { type: 'search', placeholder: '按账号过滤', 'aria-label': '按账号过滤' },
  })
  const countEl = el('span', { cls: 'arc__count' })
  const listBox = el('div', { cls: 'arc__list' })
  const detailBox = el('aside', { cls: 'arc__detail' })
  const refreshBtn = el('button', { cls: 'btn btn--sm', attrs: { type: 'button' }, text: '刷新' })

  let rows = []

  function renderDetail(row) {
    clear(detailBox)
    if (!row) {
      detailBox.append(el('div', { cls: 'arc__empty', text: '选一条记录查看对话内容' }))
      return
    }
    detailBox.append(
      el('div', { cls: 'arc__detail-head' }, [
        el('div', { cls: 'arc__detail-title', text: row.title || '新对话' }),
        el('div', { cls: 'arc__detail-meta', text: (row.ownerDisplay || '') + '（' + row.ownerUsername + '）' }),
        el('div', { cls: 'arc__detail-meta', text: '开始 ' + fmtTime(row.startedAt) + ' · 最后活动 ' + fmtTime(row.lastActiveAt) }),
        el('div', { cls: 'arc__detail-meta', text: row.messageCount + ' 条消息' }),
      ]),
    )
    const msgs = el('div', { cls: 'arc__msgs' }, [el('div', { cls: 'arc__empty', text: '读取中…' })])
    detailBox.append(msgs)

    CHAT_ARCHIVE.detail(row.id)
      .then((payload) => {
        const data = (payload && payload.data) || payload || {}
        clear(msgs)
        const items = data.messages || []
        if (!items.length) {
          msgs.append(el('div', { cls: 'arc__empty', text: '这条归档没有消息正文' }))
          return
        }
        for (const item of items) {
          const isUser = String(item.role) === 'user'
          msgs.append(
            el('div', { cls: 'arc__msg' + (isUser ? ' arc__msg--user' : '') }, [
              el('div', { cls: 'arc__msg-role', text: isUser ? '用户' : 'AI' }),
              el('div', { cls: 'arc__msg-body', text: String(item.content || '') }),
            ]),
          )
        }
      })
      .catch((err) => {
        clear(msgs)
        msgs.append(el('div', { cls: 'arc__empty', text: describe(err) }))
      })
  }

  function renderList() {
    clear(listBox)
    countEl.textContent = rows.length ? rows.length + ' 条' : ''
    if (!rows.length) {
      listBox.append(el('div', { cls: 'arc__empty', text: '还没有归档记录。有人在对话里聊过之后就会出现在这里。' }))
      return
    }
    for (const row of rows) {
      const item = el('button', { cls: 'arc__row', attrs: { type: 'button' } }, [
        el('div', { cls: 'arc__row-main' }, [
          el('div', { cls: 'arc__row-title', text: row.title || '新对话' }),
          el('div', { cls: 'arc__row-preview', text: row.preview || '（无提问记录）' }),
        ]),
        el('div', { cls: 'arc__row-side' }, [
          el('div', { cls: 'arc__row-owner', text: row.ownerDisplay || row.ownerUsername }),
          el('div', { cls: 'arc__row-time', text: fmtTime(row.lastActiveAt) }),
        ]),
      ])
      item.addEventListener('click', () => {
        for (const btn of listBox.querySelectorAll('.arc__row')) btn.removeAttribute('aria-current')
        item.setAttribute('aria-current', 'true')
        renderDetail(row)
      })
      listBox.append(item)
    }
  }

  async function load() {
    clear(listBox)
    listBox.append(el('div', { cls: 'arc__empty', text: '读取归档…' }))
    try {
      const payload = await CHAT_ARCHIVE.list(ownerInput.value.trim())
      const data = (payload && payload.data) || payload
      rows = Array.isArray(data) ? data : []
      renderList()
    } catch (err) {
      clear(listBox)
      listBox.append(el('div', { cls: 'arc__empty', text: describe(err) }))
    }
  }

  refreshBtn.addEventListener('click', load)
  ownerInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') load()
  })

  const node = el('div', { cls: 'arc' }, [
    el('header', { cls: 'arc__head' }, [
      el('div', {}, [
        el('h2', { text: '会话归档' }),
        el('p', { cls: 'arc__hint', text: '谁在什么时候用过 AI、聊了什么。仅管理员可见。' }),
      ]),
      el('div', { cls: 'arc__tools' }, [ownerInput, countEl, refreshBtn]),
    ]),
    el('div', { cls: 'arc__split' }, [listBox, detailBox]),
  ])

  renderDetail(null)
  return { node, refresh: load }
}

export async function mount(root) {
  page = buildPage()
  root.append(page.node)
  await page.refresh()
}

export async function unmount() {
  if (page) page.node.remove()
  page = null
}