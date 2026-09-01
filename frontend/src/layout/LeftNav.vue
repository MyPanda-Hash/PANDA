<template>
  <div ref="navRef" class="leftnav" :class="{ collapsed: app.collapsed, 'mobile-open': app.mobileNav }" @mouseleave="closeCard">
    <div class="func-zone">
      <el-tooltip :content="app.collapsed ? tt('展开菜单') : tt('折叠菜单')" placement="right">
        <el-icon class="rz-icon" @click="app.toggleCollapse()"><Expand /></el-icon>
      </el-tooltip>
      <el-tooltip :content="tt('单据查询')" placement="right">
        <el-icon class="rz-icon" @click="billSearchVisible = true"><Search /></el-icon>
      </el-tooltip>
      <el-tooltip :content="tt('新增单据')" placement="right">
        <el-icon class="rz-icon" @click="billAddVisible = true"><Plus /></el-icon>
      </el-tooltip>
    </div>

    <el-scrollbar class="nav-scroll">
      <template v-for="g in menuTree" :key="g.code">
        <el-tooltip :content="tt(g.title)" placement="right" :disabled="!app.collapsed">
          <div
            class="nav-group"
            :class="{ active: groupOrPath(route.path)?.code === g.code }"
            @click="clickGroup(g)"
            @mouseenter="openCard(g, $event)"
          >
            <el-icon class="gi"><component :is="g.icon || 'Folder'" /></el-icon>
            <span>{{ tt(g.title) }}</span>
            <el-icon v-if="g.children" class="arrow" :class="{ down: isExpanded(g) }"><ArrowDown /></el-icon>
          </div>
        </el-tooltip>
        <div v-if="g.children && !app.collapsed && isExpanded(g)" class="nav-modules">
          <div
            v-for="m in g.children"
            :key="m.code"
            class="nav-module"
            :class="{ active: cardModule?.code === m.code }"
            @mouseenter="openCard(m, $event)"
            @click="toggleCard(m, $event)"
          >
            <span>{{ tt(m.title) }}</span>
            <el-icon class="mi"><ArrowRight /></el-icon>
          </div>
        </div>
      </template>
    </el-scrollbar>

    <div
      v-if="cardModule"
      ref="cardRef"
      class="fly-card"
      :style="{
        top: cardTop + 'px',
        '--fly-pointer-top': cardPointerTop + 'px',
        '--fly-card-max-height': cardMaxHeight + 'px',
      }"
    >
      <div class="card-body">
        <div v-for="cat in cardColumns" :key="cat.title" class="card-group">
          <div class="card-group-title">{{ tt(cat.title) }}</div>
          <div class="card-items">
            <div
              v-for="leaf in cat.items"
              :key="leaf.code"
              class="card-item"
              :class="{ 'is-parent': leaf.children?.length, nested: leaf.depth > 0 }"
              :style="{ '--card-depth': leaf.depth || 0 }"
              :title="tt(leaf.fullTitle || leaf.title)"
              @click="go(leaf)"
            >
              <span>{{ tt(leaf.title) }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <el-dialog v-model="billSearchVisible" :title="tt('单据查询')" width="560px" append-to-body>
      <el-input v-model="billKeyword" :placeholder="tt('输入单据名称关键字')" :prefix-icon="Search" clearable />
      <div class="bill-list">
        <div v-for="b in billMatches" :key="`${b.code}:${b.path}`" class="bill-item" @click="goBill(b)">
          <el-icon><component :is="b.icon || 'Tickets'" /></el-icon>
          <span>{{ tt(b.fullTitle || b.title) }}</span>
          <span class="module">{{ tt(b.module) }}</span>
        </div>
        <el-empty v-if="!billMatches.length" :description="tt('无匹配单据')" :image-size="60" />
      </div>
    </el-dialog>

    <el-dialog v-model="billAddVisible" :title="tt('新增单据')" width="560px" append-to-body>
      <el-alert type="info" :closable="false" show-icon :title="tt('选择单据类型，进入新增页（当前为占位页，后续接入真实单据表单）')" />
      <div class="bill-list mt12">
        <div v-for="b in billMatches" :key="`${b.code}:${b.path}`" class="bill-item" @click="goBill(b, true)">
          <el-icon><component :is="b.icon || 'Tickets'" /></el-icon>
          <span>{{ tt(b.fullTitle || b.title) }}</span>
          <span class="module">{{ tt(b.module) }}</span>
        </div>
        <el-empty v-if="!billMatches.length" :description="tt('无匹配单据')" :image-size="60" />
      </div>
    </el-dialog>

    <!-- ===== 移动端：层级堆叠导航（下钻式，仅抽屉打开时显示） ===== -->
    <div v-if="app.mobileNav" class="mobile-nav">
      <div class="mn-head">
        <span v-if="mStack.length" class="mn-back" @click="mBack">‹ {{ tt('返回') }}</span>
        <span v-else class="mn-brand">轻MES</span>
        <span class="mn-title">{{ tt(mnTitle) }}</span>
        <span class="mn-close" @click="app.toggleMobileNav()">✕</span>
      </div>
      <div class="mn-quick">
        <span class="mn-q" @click="billSearchVisible = true"><el-icon><Search /></el-icon>{{ tt('单据查询') }}</span>
        <span class="mn-q" @click="billAddVisible = true"><el-icon><Plus /></el-icon>{{ tt('新增单据') }}</span>
      </div>
      <div class="mn-list">
        <template v-if="mnLevel.length">
          <div v-for="n in mnLevel" :key="n.code" class="mn-item" @click="mnClick(n)">
            <el-icon class="mn-ic"><component :is="n.icon || 'Folder'" /></el-icon>
            <span class="mn-label">{{ tt(n.title) }}</span>
            <el-icon
              v-if="n.path && n.children?.length"
              class="mn-open"
              :title="`${tt('打开')}${n.title}`"
              @click.stop="go(n)"
            ><Document /></el-icon>
            <el-icon v-if="n.children && n.children.length" class="mn-arrow"><ArrowRight /></el-icon>
          </div>
        </template>
        <div v-else class="mn-empty">{{ tt('暂无菜单') }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onBeforeUnmount, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { menuTree as rawMenuTree, filterMenuTree } from '@/business/menus'
import { tt } from '@/i18n'
import { useLocaleStore } from '@/stores/locale'
import { useTabsStore } from '@/stores/tabs'
import { useAppStore } from '@/stores/app'
import { useUserStore } from '@/stores/user'

const route = useRoute()
const router = useRouter()
const tabs = useTabsStore()
const app = useAppStore()
const user = useUserStore()
const localeStore = useLocaleStore()
const navRef = ref(null)
const cardRef = ref(null)

// 角色权限过滤后的菜单树（操作员仅见被授权面板）
const menuTree = computed(() => filterMenuTree(rawMenuTree, user.visiblePanels, user.isAdmin))

// 非中文语言：预取可见菜单标题词条（机翻/库表词典，静态包缺失的补齐）
watch(() => localeStore.current, async () => {
  if (!localeStore.isZh) {
    const titles = []
    for (const g of menuTree.value) {
      titles.push(g.title)
      for (const m of g.children || []) {
        titles.push(m.title)
        for (const c of m.children || []) titles.push(c.title)
      }
    }
    await localeStore.ensureDict(localeStore.current, titles)
  }
}, { immediate: true })

const expandedGroup = ref('')
const cardModule = ref(null)
const cardTop = ref(0)
const cardPointerTop = ref(24)
const cardMaxHeight = ref(420)
let cardAnchorEl = null
const billSearchVisible = ref(false)
const billAddVisible = ref(false)
const billKeyword = ref('')

const bills = computed(() => {
  const out = []
  for (const g of menuTree.value) {
    for (const m of g.children || []) {
      const walk = (n, parents = []) => {
        if (n.path) {
          const fullTitle = n.fullTitle || [...parents, n.title].join(' / ')
          out.push({ ...n, fullTitle, module: `${g.title} / ${m.title}` })
        }
        if (n.children) n.children.forEach((child) => walk(child, [...parents, n.title]))
      }
      m.children?.forEach((child) => walk(child))
    }
  }
  return out.filter((b) => b.path)
})

const billMatches = computed(() => {
  const k = billKeyword.value.trim().toLowerCase()
  if (!k) return bills.value
  return bills.value.filter((b) => `${b.fullTitle} ${b.module}`.toLowerCase().includes(k))
})

function isExpanded(g) {
  if (app.menuMode === 'flat') return !!g.children
  return expandedGroup.value === g.code
}

function clickGroup(g) {
  if (!g.children) {
    go(g)
    return
  }
  if (app.collapsed) {
    app.toggleCollapse()
    expandedGroup.value = g.code
    return
  }
  if (app.menuMode === 'flat') return
  expandedGroup.value = expandedGroup.value === g.code ? '' : g.code
}

async function openCard(m, e) {
  if (!app.collapsed && !m.children) return
  cardModule.value = m
  if (e?.currentTarget) cardAnchorEl = e.currentTarget
  await nextTick()
  updateCardPosition()
}

function toggleCard(m, e) {
  // 叶子模块（无 children，如业务总览）：直接跳转，不弹浮层
  if (!m.children || !m.children.length) {
    go(m)
    return
  }
  if (cardModule.value === m) closeCard()
  else openCard(m, e)
}

function closeCard() {
  cardModule.value = null
  cardAnchorEl = null
}

// 弹层尽量从所选菜单项开始展示；空间不足时自动上移，指示箭头仍精确指向所选项。
async function updateCardPosition() {
  if (!cardModule.value || !cardAnchorEl || !navRef.value || !cardRef.value) return

  const navRect = navRef.value.getBoundingClientRect()
  const anchorRect = cardAnchorEl.getBoundingClientRect()
  const visibleBottom = Math.min(navRect.bottom, window.innerHeight)
  const availableHeight = Math.max(180, visibleBottom - navRect.top - 16)
  cardMaxHeight.value = availableHeight

  await nextTick()
  if (!cardRef.value) return

  const cardHeight = Math.min(cardRef.value.offsetHeight, availableHeight)
  const anchorTop = anchorRect.top - navRect.top
  const anchorCenter = anchorTop + anchorRect.height / 2
  const minTop = 8
  const maxTop = Math.max(minTop, visibleBottom - navRect.top - cardHeight - 8)
  const nextTop = Math.min(Math.max(anchorTop, minTop), maxTop)
  const pointerInset = 14

  cardTop.value = nextTop
  cardPointerTop.value = Math.min(
    Math.max(anchorCenter - nextTop, pointerInset),
    Math.max(pointerInset, cardHeight - pointerInset)
  )
}

// ===== 移动端：层级堆叠导航（下钻式） =====
// mStack 记录当前下钻路径上的分组节点；空 = 顶层（menuTree 全部分组）
const mStack = ref([])
const mnLevel = computed(() => {
  const top = mStack.value.length ? mStack.value[mStack.value.length - 1] : null
  if (!top) return menuTree.value
  return top.children || []
})
const mnTitle = computed(() => {
  if (!mStack.value.length) return '全部功能'
  return mStack.value[mStack.value.length - 1].title
})
function mnClick(n) {
  if (n.children && n.children.length) mStack.value.push(n)
  else go(n)
}
function mBack() {
  if (mStack.value.length) mStack.value.pop()
}
// 每次打开抽屉回到顶层
watch(
  () => app.mobileNav,
  (v) => {
    if (v) mStack.value = []
  }
)

function go(leaf) {
  router.push(leaf.path)
  tabs.open(leaf)
  closeCard()
  // 移动端：跳转后关闭抽屉
  if (app.mobileNav) app.toggleMobileNav()
}

function goBill(b, isNew) {
  router.push({ path: b.path, query: isNew ? { new: 1 } : {} })
  tabs.open(b)
  billSearchVisible.value = false
  billAddVisible.value = false
  billKeyword.value = ''
  // 移动端：跳转后关闭抽屉
  if (app.mobileNav) app.toggleMobileNav()
}

function groupOrPath(path) {
  for (const g of menuTree.value) {
    const walk = (n) => {
      if (n.path === path) return true
      if (n.children) return n.children.some(walk)
      return false
    }
    if (g.path === path) return g
    if (g.children && g.children.some(walk)) return g
  }
  return null
}

const cardColumns = computed(() => {
  const m = cardModule.value
  if (!m) return []
  if (!m.children) return [{ title: m.title, items: [m] }]
  if (m.children[0]?.children) {
    return m.children.map((cat) => ({ title: cat.title, items: flattenCardItems(cat.children || [cat]) }))
  }
  return m.children.map((mod) => ({ title: mod.title, items: flattenCardItems([mod]) }))
})

function flattenCardItems(nodes) {
  const out = []
  const walk = (node, depth) => {
    if (node.path) out.push({ ...node, depth })
    if (node.children) node.children.forEach((child) => walk(child, node.path ? depth + 1 : depth))
  }
  nodes.forEach((node) => walk(node, 0))
  return out
}

watch(
  () => route.path,
  (p) => {
    const g = groupOrPath(p)
    if (g && g.children) expandedGroup.value = g.code
  },
  { immediate: true }
)

watch(
  () => [billSearchVisible.value, billAddVisible.value],
  () => { billKeyword.value = '' }
)

onMounted(() => window.addEventListener('resize', updateCardPosition))
onBeforeUnmount(() => window.removeEventListener('resize', updateCardPosition))
</script>

<style scoped>
.leftnav {
  position: relative;
  width: 184px;
  flex-shrink: 0;
  height: 100%;
  background: var(--t-sidebar-bg);
  border-right: 1px solid var(--t-border);
  z-index: 100;
  transition: width 0.2s;
}
.dark .leftnav {
  background: #26272e;
  border-color: #3a3b42;
}
.leftnav.collapsed {
  width: 56px;
}
.func-zone {
  display: flex;
  align-items: center;
  justify-content: space-around;
  height: 42px;
  border-bottom: 1px solid var(--t-border-light);
}
.dark .func-zone {
  border-color: #3a3b42;
}
.rz-icon {
  font-size: 16px;
  color: var(--t-text-2);
  cursor: pointer;
  padding: 4px;
  border-radius: 6px;
}
.dark .rz-icon {
  color: #bbb;
}
.rz-icon:hover {
  color: var(--t-primary);
  background: var(--t-hover-bg);
}
.dark .rz-icon:hover {
  background: var(--t-hover-bg);
  color: var(--t-primary);
}
.leftnav.collapsed .func-zone {
  flex-direction: column;
  height: auto;
  padding: 8px 0;
  gap: 6px;
}
.nav-scroll {
  height: calc(100% - 42px);
}
.leftnav.collapsed .nav-scroll {
  height: calc(100% - 118px);
}
.nav-group {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 14px;
  font-size: 14px;
  color: var(--t-text-1);
  cursor: pointer;
  user-select: none;
}
.dark .nav-group {
  color: #ccc;
}
.nav-group:hover,
.nav-group.active {
  color: var(--t-primary);
  background: var(--t-hover-bg);
}
.dark .nav-group:hover,
.dark .nav-group.active {
  background: var(--t-hover-bg);
  color: var(--t-primary);
}
.gi {
  font-size: 16px;
}
.arrow {
  margin-left: auto;
  font-size: 12px;
  transition: transform 0.2s;
}
.arrow.down {
  transform: rotate(180deg);
}
.leftnav.collapsed .nav-group span,
.leftnav.collapsed .nav-group .arrow {
  display: none;
}
.leftnav.collapsed .nav-group {
  justify-content: center;
  padding: 12px 0;
}
.nav-modules {
  background: var(--t-card-bg);
}
.dark .nav-modules {
  background: #2c2d35;
}
.nav-module {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 9px 12px 9px 36px;
  font-size: 13px;
  color: var(--t-text-2);
  cursor: pointer;
  white-space: nowrap;
}
.dark .nav-module {
  color: #bbb;
}
.nav-module:hover,
.nav-module.active {
  color: var(--t-primary);
  background: var(--t-hover-bg);
}
.dark .nav-module:hover,
.dark .nav-module.active {
  background: var(--t-hover-bg);
  color: var(--t-primary);
}
.mi {
  font-size: 12px;
}
.fly-card {
  position: absolute;
  left: 100%;
  min-width: 520px;
  max-width: min(820px, calc(100vw - 210px));
  max-height: var(--fly-card-max-height, 420px);
  background: var(--t-card-bg);
  border: 1px solid #d5d8dc;
  border-radius: 5px;
  box-shadow: 0 4px 14px rgba(31, 42, 55, 0.14);
  z-index: 200;
}
.fly-card::before,
.fly-card::after {
  position: absolute;
  top: calc(var(--fly-pointer-top, 24px) - 11px);
  width: 0;
  height: 0;
  border-top: 11px solid transparent;
  border-bottom: 11px solid transparent;
  content: '';
  pointer-events: none;
}
.fly-card::before {
  left: -13px;
  border-right: 13px solid #d5d8dc;
}
.fly-card::after {
  left: -11px;
  border-right: 12px solid var(--t-card-bg);
}
.dark .fly-card {
  background: #26272e;
  border-color: #3a3b42;
}
.dark .fly-card::before {
  border-right-color: #3a3b42;
}
.dark .fly-card::after {
  border-right-color: #26272e;
}
.card-body {
  display: flex;
  flex-direction: row;
  max-height: var(--fly-card-max-height, 420px);
  overflow: auto;
  padding: 14px 16px 16px;
  border-radius: 5px;
}
.card-group {
  flex: 1;
  min-width: 180px;
  display: flex;
  flex-direction: column;
  padding: 0 4px;
}
.card-group + .card-group {
  margin-left: 14px;
  padding-left: 18px;
  border-left: 1px dashed #d9dde2;
}
.card-group-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--t-text-1);
  padding: 2px 0 10px;
  margin-bottom: 2px;
  white-space: nowrap;
}
.dark .card-group-title {
  color: #ddd;
}
.dark .card-group + .card-group {
  border-left-color: #45464f;
}
.card-items {
  display: flex;
  flex-direction: column;
}
.card-item {
  display: flex;
  align-items: center;
  min-width: 0;
  padding: 7px 0;
  font-size: 14px;
  color: var(--t-text-1);
  border-radius: 3px;
  cursor: pointer;
  white-space: nowrap;
  transition: color 0.15s, background-color 0.15s, padding-left 0.15s;
  margin-left: calc(var(--card-depth, 0) * 14px);
}
.card-item.is-parent {
  font-weight: 600;
}
.card-item.nested {
  color: var(--t-text-2);
  font-size: 13px;
}
.card-item span {
  overflow: hidden;
  text-overflow: ellipsis;
}
.dark .card-item {
  color: #ccc;
}
.card-item:hover {
  padding-left: 8px;
  background: var(--t-hover-bg);
  color: var(--t-primary);
}
.dark .card-item:hover {
  background: var(--t-hover-bg);
  color: var(--t-primary);
}
.bill-list {
  max-height: 400px;
  overflow: auto;
  margin-top: 12px;
}
.bill-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px 10px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
}
.bill-item:hover {
  background: var(--t-hover-bg);
  color: var(--t-primary);
}
.bill-item .module {
  margin-left: auto;
  font-size: 12px;
  color: var(--t-text-3);
}
.mt12 {
  margin-top: 12px;
}

/* 模块卡片返回头：仅移动端显示 */
.fly-head {
  display: none;
}

/* ===== 移动端（≤768px）：侧栏变抽屉 + 层级堆叠导航 ===== */
@media (max-width: 768px) {
  .leftnav {
    position: fixed;
    top: 0;
    left: 0;
    bottom: 0;
    width: min(320px, 86vw) !important;
    z-index: 1000;
    transform: translateX(-100%);
    transition: transform 0.22s ease;
    box-shadow: 0 0 24px rgba(0, 0, 0, 0.18);
    overflow: hidden;
  }
  .leftnav.mobile-open {
    transform: translateX(0);
  }
  /* 桌面导航结构（功能区/菜单树/悬浮卡片）移动端隐藏 */
  .func-zone,
  .nav-scroll,
  .fly-card {
    display: none !important;
  }

  /* ===== 层级堆叠导航 ===== */
  .mobile-nav {
    display: flex;
    flex-direction: column;
    height: 100%;
  }
  .mn-head {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 14px 12px;
    border-bottom: 1px solid var(--t-border);
    background: var(--t-card-bg);
    flex-shrink: 0;
  }
  .mn-back {
    font-size: 15px;
    color: var(--t-primary);
    cursor: pointer;
    padding: 6px 8px 6px 0;
    flex-shrink: 0;
  }
  .mn-brand {
    font-size: 15px;
    font-weight: 700;
    color: var(--t-primary);
    flex-shrink: 0;
  }
  .mn-title {
    flex: 1;
    font-size: 15px;
    font-weight: 600;
    color: var(--t-text-1);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    text-align: center;
  }
  .mn-close {
    font-size: 16px;
    color: var(--t-text-2);
    cursor: pointer;
    padding: 6px;
    flex-shrink: 0;
  }
  .mn-quick {
    display: flex;
    gap: 8px;
    padding: 10px 12px;
    border-bottom: 1px solid var(--t-border-light);
    flex-shrink: 0;
  }
  .mn-q {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    min-height: 38px;
    font-size: 14px;
    color: var(--t-primary);
    background: var(--t-hover-bg);
    border-radius: 8px;
    cursor: pointer;
  }
  .mn-list {
    flex: 1;
    overflow-y: auto;
    -webkit-overflow-scrolling: touch;
    padding: 6px 8px calc(12px + env(safe-area-inset-bottom));
  }
  .mn-item {
    display: flex;
    align-items: center;
    gap: 10px;
    min-height: 46px;
    padding: 8px 12px;
    border-radius: 8px;
    font-size: 15px;
    color: var(--t-text-1);
    cursor: pointer;
  }
  .mn-item:active {
    background: var(--t-hover-bg);
    color: var(--t-primary);
  }
  .mn-ic {
    font-size: 17px;
    color: var(--t-text-2);
    flex-shrink: 0;
  }
  .mn-label {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .mn-arrow {
    font-size: 14px;
    color: var(--t-text-3);
    flex-shrink: 0;
  }
  .mn-open {
    font-size: 16px;
    color: var(--t-primary);
    cursor: pointer;
    flex-shrink: 0;
  }
  .mn-empty {
    padding: 40px 0;
    text-align: center;
    color: var(--t-text-3);
    font-size: 14px;
  }
}
</style>
