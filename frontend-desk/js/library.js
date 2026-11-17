/**
 * 资料库：报表模板、制度文件、工艺文件集中存放。
 *
 * 交互取向：列表优先、上传常驻在顶部；不做花哨的卡片墙，
 * 文件是要拿走的，一行一条最好扫。
 */
import { LIBRARY } from './api.js'
import { el, clear, toast } from './util.js'

let page = null
const state = { rows: [], category: '', keyword: '' }

function describe(err) {
  if (!err) return '操作失败'
  if (err.status === 401) return '登录已失效，请重新登录'
  if (err.status === 403) return err.message || '没有权限'
  if (err.status === 413) return '文件太大，单个不超过 20 MB'
  if (err.status === 0) return '连不上服务，检查后端是否在运行'
  return err.message || '操作失败'
}

function sizeText(bytes) {
  const n = Number(bytes) || 0
  if (n < 1024) return n + ' B'
  if (n < 1024 * 1024) return (n / 1024).toFixed(0) + ' KB'
  return (n / 1024 / 1024).toFixed(1) + ' MB'
}

function timeText(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const pad = (v) => String(v).padStart(2, '0')
  return pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes())
}

function buildPage() {
  const cats = el('div', { cls: 'lib__cats' })
  const listBox = el('div', { cls: 'lib__list' })
  const countEl = el('span', { cls: 'lib__count' })
  const fileInput = el('input', { cls: 'lib__file', attrs: { type: 'file' } })
  const noteInput = el('input', {
    cls: 'input lib__note',
    attrs: { type: 'text', placeholder: '备注（可选）', 'aria-label': '备注' },
  })
  const uploadBtn = el('button', { cls: 'btn btn--primary btn--sm', attrs: { type: 'button' }, text: '上传' })

  let categories = []

  function renderCats() {
    clear(cats)
    const all = [{ key: '', label: '全部' }, ...categories]
    for (const item of all) {
      const btn = el('button', {
        cls: 'lib__cat' + (state.category === item.key ? ' is-on' : ''),
        attrs: { type: 'button' },
        text: item.label,
      })
      btn.addEventListener('click', () => {
        state.category = item.key
        renderCats()
        void load()
      })
      cats.append(btn)
    }
  }

  function renderList() {
    clear(listBox)
    countEl.textContent = state.rows.length ? state.rows.length + ' 个文件' : ''
    if (!state.rows.length) {
      listBox.append(
        el('div', { cls: 'lib__empty' }, [
          el('div', { text: '这一类还没有文件' }),
          el('div', { cls: 'lib__empty-hint', text: '把 PPTX / DOCX / XLSX 模板拖到上方「选择文件」上传' }),
        ]),
      )
      return
    }
    for (const row of state.rows) {
      const download = el('button', { cls: 'btn btn--sm', attrs: { type: 'button' }, text: '下载' })
      download.addEventListener('click', async () => {
        try {
          await LIBRARY.download(row.id, row.name)
        } catch (err) {
          toast(describe(err), 'error')
        }
      })
      const remove = el('button', { cls: 'btn btn--text btn--sm', attrs: { type: 'button' }, text: '删除' })
      remove.addEventListener('click', async () => {
        if (!window.confirm('删除「' + row.name + '」？此操作不可撤销。')) return
        try {
          await LIBRARY.remove(row.id)
          toast('已删除')
          await load()
        } catch (err) {
          toast(describe(err), 'error')
        }
      })

      listBox.append(
        el('div', { cls: 'lib__row' }, [
          el('div', { cls: 'lib__row-main' }, [
            el('div', { cls: 'lib__row-name', text: row.name }),
            el('div', { cls: 'lib__row-meta', text: [
              row.categoryLabel,
              sizeText(row.size),
              row.ownerName,
              timeText(row.createdAt),
              row.note || '',
            ].filter(Boolean).join(' · ') }),
          ]),
          el('div', { cls: 'lib__row-acts' }, [download, remove]),
        ]),
      )
    }
  }

  async function load() {
    clear(listBox)
    listBox.append(el('div', { cls: 'lib__empty', text: '读取中…' }))
    try {
      const payload = await LIBRARY.list(state.category, state.keyword)
      const data = (payload && payload.data) || payload
      state.rows = Array.isArray(data) ? data : []
      renderList()
    } catch (err) {
      clear(listBox)
      listBox.append(el('div', { cls: 'lib__empty', text: describe(err) }))
    }
  }

  uploadBtn.addEventListener('click', async () => {
    const file = fileInput.files && fileInput.files[0]
    if (!file) {
      toast('先选一个文件', 'error')
      return
    }
    uploadBtn.disabled = true
    uploadBtn.textContent = '上传中…'
    try {
      await LIBRARY.upload(file, state.category || 'template', noteInput.value.trim())
      toast('已上传：' + file.name)
      fileInput.value = ''
      noteInput.value = ''
      await load()
    } catch (err) {
      toast(describe(err), 'error')
    } finally {
      uploadBtn.disabled = false
      uploadBtn.textContent = '上传'
    }
  })

  const node = el('div', { cls: 'lib' }, [
    el('header', { cls: 'lib__head' }, [
      el('div', {}, [
        el('h2', { text: '资料库' }),
        el('p', { cls: 'lib__hint', text: '报表模板、制度文件、工艺文件集中存放；登录即可下载。' }),
      ]),
      countEl,
    ]),
    el('div', { cls: 'lib__upload' }, [fileInput, noteInput, uploadBtn]),
    cats,
    listBox,
  ])

  async function boot() {
    try {
      const payload = await LIBRARY.categories()
      const data = (payload && payload.data) || payload
      categories = Array.isArray(data) ? data : []
    } catch {
      categories = []
    }
    renderCats()
    await load()
  }

  return { node, boot }
}

export async function mount(root) {
  page = buildPage()
  root.append(page.node)
  await page.boot()
}

export async function unmount() {
  if (page) page.node.remove()
  page = null
}