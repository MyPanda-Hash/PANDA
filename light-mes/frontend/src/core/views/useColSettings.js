import { reactive, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { attachHeaderDrag, defaultSettings, menuCols, withRenamed, withToggled } from './tableColCustom'

/**
 * 表格列设置共享状态机：localStorage 存取（顺序/栏名/显隐）+ 右键显隐菜单 + 双击改名 + 表头拖拽调序。
 * PanelxList / PanelxForm 原各自内联约 150 行几乎相同的实现，差异点通过 options 注入：
 *
 * - panelCode:      ref，存储键前缀（panelx-col-settings:<panelCode>:<key>）
 * - resolveTarget:  target 统一化，返回 { key, defaults, title }；key 为存储键尾段，
 *                   defaults 为默认列名数组，title 为菜单标题
 * - targetFromScope: 改名提交时把模板传入的 scope 字符串还原为 target（找不到返回 null 则跳过）
 * - dragTargets:    需绑定表头拖拽的 [{ selector, onReorder(from,to) }]；表头异步渲染时自动延迟重试
 * - dragSignature:  computed，列顺序变化信号（变化后 nextTick 重绑表头拖拽）
 */
export function useColSettings({ panelCode, resolveTarget, targetFromScope, dragTargets, dragSignature }) {
  const settingsMap = reactive({}) // { storageKey: [{prop,label,visible}] | null }

  // localStorage 是共享、可被用户/旧版本改写的存储：非 [{prop,...}] 结构会让 filter/find 抛错白屏，
  // 校验失败一律视为无设置并清除该键
  function readSettings(k) {
    try {
      const parsed = JSON.parse(localStorage.getItem(k))
      if (Array.isArray(parsed) && parsed.every((s) => s && typeof s.prop === 'string')) return parsed
      localStorage.removeItem(k)
    } catch { /* 非法 JSON 按无设置处理 */ }
    return null
  }

  function getSettings(target) {
    const k = `panelx-col-settings:${panelCode.value}:${resolveTarget(target).key}`
    if (!(k in settingsMap)) settingsMap[k] = readSettings(k)
    return settingsMap[k] || null
  }

  function saveSettings(target, settings) {
    const k = `panelx-col-settings:${panelCode.value}:${resolveTarget(target).key}`
    settingsMap[k] = settings
    try {
      if (settings === null) localStorage.removeItem(k)
      else localStorage.setItem(k, JSON.stringify(settings))
    } catch { /* 隐私模式/配额满：仅内存生效 */ }
  }

  // ---------- 右键显隐菜单 ----------
  const menu = reactive({ visible: false, x: 0, y: 0, target: null, title: '', cols: [] })
  const renaming = reactive({ on: false, scope: '', prop: '', text: '' })
  const lastPointer = { x: 0, y: 0 }

  function openMenu(target, _prop, ev) {
    ev?.preventDefault?.()
    const x = ev?.clientX || lastPointer.x || 240
    const y = ev?.clientY || lastPointer.y || 240
    const rt = resolveTarget(target)
    menu.target = target
    menu.title = rt.title
    menu.cols = menuCols(rt.defaults, getSettings(target))
    menu.x = Math.max(8, Math.min(x, window.innerWidth - 250))
    menu.y = Math.max(8, Math.min(y, window.innerHeight - 380))
    setTimeout(() => { menu.visible = true }, 0)
  }

  function onMenuToggle(prop, visible) {
    const t = menu.target
    if (!t) return
    const c = menu.cols.find((x) => x.prop === prop)
    if (c) c.visible = visible
    saveSettings(t, withToggled(getSettings(t) || defaultSettings(resolveTarget(t).defaults), prop, visible))
  }

  function onMenuReset() {
    const t = menu.target
    if (!t) return
    menu.cols = menu.cols.map((c) => ({ ...c, label: c.prop, visible: true }))
    saveSettings(t, null)
  }

  // ---------- 双击表头改名（Enter/blur 提交，Esc 取消；空名不生效） ----------
  function startRename(scope, prop, currentLabel) {
    renaming.on = true
    renaming.scope = scope
    renaming.prop = prop
    renaming.text = currentLabel
  }

  function cancelRename() {
    renaming.on = false
  }

  function commitRename() {
    if (!renaming.on) return
    renaming.on = false
    const label = renaming.text.trim()
    const { scope, prop } = renaming
    if (!label) return
    const t = targetFromScope(scope)
    if (!t) return
    saveSettings(t, withRenamed(getSettings(t) || defaultSettings(resolveTarget(t).defaults), prop, label))
  }

  // ---------- 表头拖拽调序绑定：列顺序变化后 Element Plus 重渲染表头，新 th 需重新绑定 ----------
  let dragCleanups = []
  let dragTimer = null
  function bindDrags(attempt = 0) {
    dragCleanups.forEach((f) => f())
    dragCleanups = []
    clearTimeout(dragTimer)
    let ready = 0
    const targets = dragTargets()
    for (const t of targets) {
      const hw = document.querySelector(t.selector + ' .el-table__header-wrapper')
      if (!hw || hw.querySelectorAll('thead th[class*="hcol-"]').length === 0) continue
      ready++
      dragCleanups.push(attachHeaderDrag(hw, { onReorder: t.onReorder }))
    }
    if (targets.length > 0 && ready < targets.length && attempt < 12) {
      dragTimer = setTimeout(() => bindDrags(attempt + 1), 400)
    }
  }

  watch(dragSignature, () => nextTick(bindDrags))

  function onDocPointerDown(ev) {
    lastPointer.x = ev.clientX
    lastPointer.y = ev.clientY
    if (menu.visible && !ev.target.closest?.('.col-menu')) menu.visible = false
  }

  function onDocContextMenu(ev) {
    if (menu.visible && !ev.target.closest?.('.col-menu')) menu.visible = false
  }

  onMounted(() => {
    document.addEventListener('pointerdown', onDocPointerDown, true)
    document.addEventListener('contextmenu', onDocContextMenu, true)
    nextTick(bindDrags)
  })

  onUnmounted(() => {
    clearTimeout(dragTimer)
    document.removeEventListener('pointerdown', onDocPointerDown, true)
    document.removeEventListener('contextmenu', onDocContextMenu, true)
    dragCleanups.forEach((f) => f())
    dragCleanups = []
  })

  return {
    menu, renaming, lastPointer,
    getSettings, saveSettings,
    openMenu, onMenuToggle, onMenuReset,
    startRename, cancelRename, commitRename,
  }
}
