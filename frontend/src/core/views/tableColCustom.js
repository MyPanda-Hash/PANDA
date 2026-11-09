// 表格列自定义共享工具：表头拖拽调序 + 显隐/栏名设置的纯函数操作
// settings 结构：[{ prop, label, visible }]，prop 为列原始名，label 为显示名

export function defaultSettings(props) {
  return (props || []).map((p) => ({ prop: p, label: p, visible: true }))
}

/** 全量列的当前状态（供显隐菜单渲染） */
export function menuCols(defaultProps, saved) {
  const sMap = new Map((saved || []).map((s) => [s.prop, s]))
  return (defaultProps || []).map((p) => {
    const s = sMap.get(p)
    return { prop: p, label: s?.label || p, visible: s ? s.visible !== false : true }
  })
}

/** 拖拽落子后重建设置：可见列按新顺序，隐藏列保持原相对顺序排在其后 */
export function withReordered(base, newVisibleOrder) {
  const prev = base || []
  const label = (p) => prev.find((s) => s.prop === p)?.label || p
  const hidden = prev.filter((s) => s.visible === false && !newVisibleOrder.includes(s.prop))
  return newVisibleOrder.map((p) => ({ prop: p, label: label(p), visible: true })).concat(hidden)
}

/** 更新显隐 */
export function withToggled(base, prop, visible) {
  const prev = base || []
  if (!prev.some((s) => s.prop === prop)) return prev.concat([{ prop, label: prop, visible }])
  return prev.map((s) => (s.prop === prop ? { ...s, visible } : s))
}

/** 更新栏名 */
export function withRenamed(base, prop, label) {
  const prev = base || []
  if (!prev.some((s) => s.prop === prop)) return prev.concat([{ prop, label, visible: true }])
  return prev.map((s) => (s.prop === prop ? { ...s, label } : s))
}

/**
 * 给 el-table 表头绑定原生拖拽调序。
 * 只对带 `hcol-<prop>` label-class-name 的表头单元格生效。
 * 返回解绑函数。
 */
export function attachHeaderDrag(headerEl, { onReorder } = {}) {
  if (!headerEl) return () => {}
  const ths = Array.from(headerEl.querySelectorAll('thead th')).filter((th) =>
    Array.from(th.classList).some((c) => c.startsWith('hcol-'))
  )
  const cleanups = []
  let draggingProp = null
  for (const th of ths) {
    const prop = Array.from(th.classList).find((c) => c.startsWith('hcol-')).slice(5)
    th.draggable = true
    const onDragStart = (ev) => {
      draggingProp = prop
      if (ev.dataTransfer) {
        ev.dataTransfer.effectAllowed = 'move'
        try { ev.dataTransfer.setData('text/plain', prop) } catch {}
      }
      th.classList.add('hcol-dragging')
    }
    const onDragOver = (ev) => {
      ev.preventDefault()
      if (ev.dataTransfer) ev.dataTransfer.dropEffect = 'move'
      th.classList.add('hcol-over')
    }
    const onDragLeave = () => th.classList.remove('hcol-over')
    const onDrop = (ev) => {
      ev.preventDefault?.()
      th.classList.remove('hcol-over')
      const from = draggingProp || (ev.dataTransfer ? ev.dataTransfer.getData('text/plain') : null)
      draggingProp = null
      if (from && from !== prop) onReorder?.(from, prop)
    }
    const onDragEnd = () => {
      th.classList.remove('hcol-dragging')
      ths.forEach((t) => t.classList.remove('hcol-over'))
    }
    th.addEventListener('dragstart', onDragStart)
    th.addEventListener('dragover', onDragOver)
    th.addEventListener('dragleave', onDragLeave)
    th.addEventListener('drop', onDrop)
    th.addEventListener('dragend', onDragEnd)
    cleanups.push(() => {
      th.draggable = false
      th.removeEventListener('dragstart', onDragStart)
      th.removeEventListener('dragover', onDragOver)
      th.removeEventListener('dragleave', onDragLeave)
      th.removeEventListener('drop', onDrop)
      th.removeEventListener('dragend', onDragEnd)
    })
  }
  return () => cleanups.forEach((f) => f())
}