/**
 * AI 用量：管理员看「每个账号每个月花了多少钱」，并给账号设月度额度。
 *
 * 和「会话归档」的分工：归档看聊了什么（内容审计），这里看花了多少（成本管理）。
 * 额度用完后对话会被拒（后端 ai_usage.check_quota），提示语由后端给。
 */
import { AI_USAGE } from './api.js'
import { el, clear } from './util.js'

let page = null

function describe(err) {
  if (!err) return '加载失败'
  if (err.status === 401) return '登录已失效，请重新登录'
  if (err.status === 403) return '只有管理员可以查看 AI 用量'
  if (err.status === 0) return '连不上服务，检查后端是否在运行'
  return err.message || '加载失败'
}

function money(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return '—'
  if (n === 0) return '¥0'
  if (n < 1) return '¥' + n.toFixed(4)
  return '¥' + n.toFixed(2)
}

function currentMonth() {
  const d = new Date()
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0')
}

function buildPage() {
  const monthInput = el('input', {
    cls: 'input au__month',
    attrs: { type: 'month', value: currentMonth(), 'aria-label': '账期' },
  })
  const summaryEl = el('div', { cls: 'au__summary' })
  const tableBox = el('div', { cls: 'au__table' })
  const refreshBtn = el('button', { cls: 'btn btn--sm', attrs: { type: 'button' }, text: '刷新' })

  let rows = []

  function renderSummary() {
    clear(summaryEl)
    const total = rows.reduce((sum, r) => sum + Number(r.cost_cny || 0), 0)
    const calls = rows.reduce((sum, r) => sum + Number(r.calls || 0), 0)
    const over = rows.filter((r) => r.over_limit).length
    // 不要往 append 里塞 null：DOM 会把它当成字符串 "null" 渲染出来
    const stats = [
      el('span', { cls: 'au__stat', text: '账期 ' + monthInput.value }),
      el('span', { cls: 'au__stat', text: '合计 ' + money(total) }),
      el('span', { cls: 'au__stat', text: '调用 ' + calls + ' 次' }),
    ]
    if (over) {
      stats.push(el('span', { cls: 'au__stat au__stat--warn', text: over + ' 个账号超额' }))
    }
    summaryEl.append(...stats)
  }

  function renderTable() {
    clear(tableBox)
    if (!rows.length) {
      tableBox.append(el('div', { cls: 'au__empty', text: '这个账期还没有 AI 调用记录' }))
      return
    }

    const head = el('div', { cls: 'au__row au__row--head' }, [
      el('span', { text: '账号' }),
      el('span', { text: '姓名' }),
      el('span', { cls: 'au__num', text: '调用' }),
      el('span', { cls: 'au__num', text: '输入 token' }),
      el('span', { cls: 'au__num', text: '输出 token' }),
      el('span', { cls: 'au__num', text: '花费' }),
      el('span', { cls: 'au__num', text: '额度' }),
      el('span', { text: '' }),
    ])
    tableBox.append(head)

    for (const row of rows) {
      const limitText = row.limit_cny === null || row.limit_cny === undefined
        ? '不限'
        : money(row.limit_cny)
      const editBtn = el('button', {
        cls: 'btn btn--ghost btn--sm',
        attrs: { type: 'button' },
        text: '设额度',
      })
      editBtn.addEventListener('click', () => openQuota(row))

      tableBox.append(el('div', { cls: 'au__row' + (row.over_limit ? ' au__row--over' : '') }, [
        el('span', { cls: 'au__name', text: row.username || '—' }),
        el('span', { text: row.real_name || '—' }),
        el('span', { cls: 'au__num', text: String(row.calls || 0) }),
        el('span', { cls: 'au__num', text: String(row.input_tokens || 0) }),
        el('span', { cls: 'au__num', text: String(row.output_tokens || 0) }),
        el('span', { cls: 'au__num au__cost', text: money(row.cost_cny) }),
        el('span', { cls: 'au__num' + (row.over_limit ? ' au__cost--over' : ''), text: limitText }),
        el('span', { cls: 'au__ops' }, [editBtn]),
      ]))
    }
  }

  function openQuota(row) {
    const input = el('input', {
      cls: 'input',
      attrs: {
        type: 'number',
        step: '0.01',
        min: '0',
        value: row.limit_cny === null || row.limit_cny === undefined ? '' : String(row.limit_cny),
        placeholder: '留空＝不限，0＝禁止使用',
      },
    })
    const mask = el('div', { cls: 'au__mask' })
    const saveBtn = el('button', { cls: 'btn btn--primary btn--sm', attrs: { type: 'button' }, text: '保存' })
    const cancelBtn = el('button', { cls: 'btn btn--ghost btn--sm', attrs: { type: 'button' }, text: '取消' })
    const close = () => mask.remove()

    saveBtn.addEventListener('click', async () => {
      const raw = input.value.trim()
      saveBtn.disabled = true
      saveBtn.textContent = '保存中…'
      try {
        await AI_USAGE.saveQuota({
          user_id: row.user_id,
          username: row.username,
          limit_cny: raw === '' ? null : Number(raw),
        })
        close()
        await load()
      } catch (err) {
        window.alert(describe(err))
      } finally {
        saveBtn.disabled = false
        saveBtn.textContent = '保存'
      }
    })
    cancelBtn.addEventListener('click', close)

    mask.append(el('div', { cls: 'au__dialog' }, [
      el('h3', { cls: 'au__dialog-title', text: '设置额度 · ' + (row.username || row.user_id) }),
      el('p', { cls: 'au__dialog-tip', text: '每月上限（人民币）。留空表示不限，填 0 表示禁止该账号使用 AI。' }),
      input,
      el('div', { cls: 'au__dialog-acts' }, [cancelBtn, saveBtn]),
    ]))
    mask.addEventListener('click', (e) => { if (e.target === mask) close() })
    document.body.append(mask)
    input.focus()
  }

  async function load() {
    try {
      const payload = await AI_USAGE.report(monthInput.value)
      const data = (payload && payload.data) || payload || {}
      rows = data.rows || []
      renderSummary()
      renderTable()
    } catch (err) {
      clear(tableBox)
      tableBox.append(el('div', { cls: 'au__empty', text: describe(err) }))
    }
  }

  const toolbar = el('div', { cls: 'au__toolbar' }, [
    el('label', { cls: 'au__field' }, [el('span', { text: '账期' }), monthInput]),
    refreshBtn,
  ])
  monthInput.addEventListener('change', load)
  refreshBtn.addEventListener('click', load)

  const node = el('section', { cls: 'au' }, [
    el('p', { cls: 'au__intro secondary' }, [
      '按账号统计每月的 AI 调用与花费，额度用完会拒绝调用。' +
        '单价取自「模型价格」表，历史流水保存下单时的单价快照，调价不改旧账。',
    ]),
    toolbar,
    summaryEl,
    tableBox,
  ])

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