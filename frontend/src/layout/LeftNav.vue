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
      class="fly-card adx"
      :style="{
        top: cardTop + 'px',
        '--fly-pointer-top': cardPointerTop + 'px',
        '--fly-card-max-height': cardMaxHeight + 'px',
      }"
    >
      <div class="card-head">
        <el-icon class="card-head__icon"><component :is="cardModule.icon || 'Folder'" /></el-icon>
        <span class="card-head__title">{{ tt(cardModule.title) }}</span>
        <span v-if="cardModule.desc" class="card-head__desc">{{ tt(cardModule.desc) }}</span>
        <span class="card-head__count">{{ cardItemCount }} {{ tt('项') }}</span>
      </div>
      <!-- 悬停的是顶层分组：左侧模块清单，右侧当前模块的入口（不再一次性平铺 80+ 条） -->
      <div v-if="cardIsGroup" class="card-split">
        <div class="card-side">
          <button
            v-for="mod in cardModules"
            :key="mod.code"
            class="side-item"
            :class="{ on: activeModule?.code === mod.code }"
            @mouseenter="activeModuleCode = mod.code"
            @focus="activeModuleCode = mod.code"
            @click="onSideClick(mod)"
          >
            <span class="side-item__label">{{ tt(mod.title) }}</span>
            <el-icon class="side-item__arrow"><ArrowRight /></el-icon>
          </button>
        </div>
        <div class="card-main">
          <div v-if="activeHeadVisible" class="card-main__head">
            <span>{{ tt(activeModule?.title || '') }}</span>
            <em>{{ activeLeafCount }} {{ tt('项') }}</em>
          </div>
          <div class="card-sections">
            <section v-for="cat in activeSections" :key="cat.title" class="card-group" :class="{ 'is-wide': cat.total > 6 }">
              <div v-if="cat.showTitle" class="card-group-title">
                <span>{{ tt(cat.title) }}</span>
                <em>{{ cat.total }}</em>
              </div>
              <div class="card-items">
                <div v-for="row in cat.rows" :key="row.node.code" class="card-row">
                  <button
                    class="card-item"
                    :class="{ 'is-parent': row.links.length, 'is-leaf': !row.links.length }"
                    :title="tt(row.node.fullTitle || row.node.title)"
                    @click="go(row.node)"
                  >
                    <span>{{ tt(row.node.title) }}</span>
                  </button>
                  <span v-if="row.links.length" class="card-links">
                    <button
                      v-for="link in row.links"
                      :key="link.code"
                      class="card-link"
                      :title="tt(link.title)"
                      @click="go(link)"
                    >{{ tt(link.title) }}</button>
                  </span>
                </div>
              </div>
            </section>
          </div>
          <div v-if="!activeLeafCount" class="card-main__empty">{{ tt('该模块下暂无入口') }}</div>
        </div>
      </div>

      <!-- 悬停的是具体模块：同样按「分类 → 单据 + 报表链接」排，不再平铺 -->
      <div v-else class="card-body">
        <section v-for="cat in cardSections" :key="cat.title" class="card-group" :class="{ 'is-wide': cat.total > 6 }">
          <div v-if="cat.showTitle" class="card-group-title">
            <span>{{ tt(cat.title) }}</span>
            <em>{{ cat.total }}</em>
          </div>
          <div class="card-items">
            <div v-for="row in cat.rows" :key="row.node.code" class="card-row">
              <button
                class="card-item"
                :class="{ 'is-parent': row.links.length, 'is-leaf': !row.links.length }"
                :title="tt(row.node.fullTitle || row.node.title)"
                @click="go(row.node)"
              >
                <span>{{ tt(row.node.title) }}</span>
              </button>
              <span v-if="row.links.length" class="card-links">
                <button
                  v-for="link in row.links"
                  :key="link.code"
                  class="card-link"
                  :title="tt(link.title)"
                  @click="go(link)"
                >{{ tt(link.title) }}</button>
              </span>
            </div>
          </div>
        </section>
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
  const sameCard = cardModule.value?.code === m.code
  cardModule.value = m
  // 分组浮层每次重新打开都回到第一个模块，避免停留上次位置
  if (!sameCard) activeModuleCode.value = ''
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

/** 浮层头部的「N 项」：分组时是全部模块条目合计，模块时是它自己的一级入口数 */
const cardItemCount = computed(() => (
  cardIsGroup.value
    ? cardTotalForGroup.value
    : cardSections.value.reduce((n, cat) => n + cat.total, 0)
))

// ---------- 悬浮层两级结构：分组 → 模块清单 + 当前模块入口 ----------
// 顶层分组的 children 是模块（有 children），逐个平铺会把 80+ 条挤在一屏，
// 所以分组悬停时先列模块，右侧只展开当前模块的入口。
const groupCodes = computed(() => new Set(menuTree.value.map((g) => g.code)))
// 分组下只有一个模块（如 TOP应用 › 方案中心）：不做左右分栏，直接给这个模块的入口
const cardSingleModule = computed(() => {
  const m = cardModule.value
  if (!m || !groupCodes.value.has(m.code)) return null
  const kids = m.children || []
  return kids.length === 1 ? kids[0] : null
})
const cardIsGroup = computed(() => (
  !!cardModule.value && groupCodes.value.has(cardModule.value.code) && !cardSingleModule.value
))
const cardModules = computed(() => (cardIsGroup.value ? cardModule.value.children || [] : []))
const activeModuleCode = ref('')
const activeModule = computed(() => {
  const list = cardModules.value
  if (!list.length) return null
  if (activeModuleCode.value) {
    const hit = list.find((m) => m.code === activeModuleCode.value)
    if (hit) return hit
  }
  // 默认落在第一个「有入口」的模块上：叶子模块（如业务总览）点一下就跳走，
  // 拿它当默认会让右侧只剩一条，看起来像空浮层
  return list.find((m) => m.children && m.children.length) || list[0]
})

/**
 * 把模块的菜单子树压成「分类 → 行」：
 * 一行 = 一张单据（可点）+ 它自己的统计表 / 明细表等报表（可点的小链接）。
 * 原来把 单据/统计表/明细表 拉平成同级条目，25 条竖排下来浮层超过一屏。
 */
function sectionsOf(module) {
  if (!module) return []
  const kids = module.children || []
  if (!kids.length) {
    return [{ title: module.title, rows: [{ node: module, links: [] }], total: 1, showTitle: false }]
  }
  // 子级本身可点（没有下级分类）：整体作为一节，标题用模块名，
  // 否则会出现「物料清单正向查询 1 / 物料清单正向查询」这种同名重复
  if (kids.every((k) => k.path)) {
    const rows = kids.map((node) => ({ node, links: (node.children || []).filter((c) => c.path) }))
    return [{ title: module.title, rows, total: rows.length, showTitle: false }]
  }
  return kids.map((cat) => {
    const nodes = cat.children && cat.children.length ? cat.children : [cat]
    const rows = nodes.map((node) => ({
      node,
      links: (node.children || []).filter((c) => c.path),
    }))
    // 分类里只有一条、且名字和分类名一样（如 TOP应用 › 方案中心）：不再重复一行标题
    const redundant = rows.length === 1 && rows[0].node.title === cat.title
    return { title: cat.title, rows, total: rows.length, showTitle: !redundant }
  })
}

const activeSections = computed(() => sectionsOf(activeModule.value))
const cardSections = computed(() => sectionsOf(cardSingleModule.value || cardModule.value))
const activeLeafCount = computed(() => activeSections.value.reduce((n, cat) => n + cat.total, 0))
// 模块只有一个入口且名字与模块相同时（如 TOP应用 › 方案中心），右侧不再重复一遍模块名
const activeHeadVisible = computed(() => {
  const sections = activeSections.value
  if (!sections.length) return false
  return !(sections.length === 1 && !sections[0].showTitle)
})
const cardTotalForGroup = computed(() => (
  cardModules.value.reduce((n, m) => n + sectionsOf(m).reduce((k, c) => k + c.total, 0), 0)
))

function onSideClick(mod) {
  activeModuleCode.value = mod.code
  // 模块本身没有子级（如「业务总览」）：直接跳转，不留在浮层里
  if (!mod.children || !mod.children.length) {
    go(mod)
    return
  }
  // 整块只有一条可点入口（如「方案中心」）：侧栏点一下就到，不必再展开右侧
  const sections = sectionsOf(mod)
  const total = sections.reduce((n, cat) => n + cat.total, 0)
  if (total === 1) {
    const only = sections[0].rows[0].node
    if (only.path) go(only)
  }
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
/* ===== 悬浮层：按设计库（antd Popover / Menu）重排 =====
   - 卡片：白底 + 1px 边框 + 弹层阴影，圆角 8
   - 头部：图标 + 分组名 + 条目数，44 高，底边 1px
   - 内容：网格自动分列（每列 ≥190），模块名做列头，条目 36 高、圆角 6、悬停填充色不位移 */
.fly-card {
  position: absolute;
  left: 100%;
  z-index: 200;
  display: flex;
  flex-direction: column;
  min-width: 440px;
  max-width: min(880px, calc(100vw - 200px));
  max-height: var(--fly-card-max-height, 420px);
  overflow: hidden;
  border: 1px solid var(--d-border-strong);
  border-radius: var(--d-radius);
  background: var(--d-card);
  box-shadow: 0 6px 16px 0 rgba(0, 0, 0, 0.08), 0 3px 6px -4px rgba(0, 0, 0, 0.12);
}
.fly-card::before,
.fly-card::after {
  position: absolute;
  top: calc(var(--fly-pointer-top, 24px) - 6px);
  width: 0;
  height: 0;
  border-top: 6px solid transparent;
  border-bottom: 6px solid transparent;
  content: '';
  pointer-events: none;
}
.fly-card::before {
  left: -7px;
  border-right: 6px solid var(--d-border-strong);
}
.fly-card::after {
  left: -6px;
  border-right: 6px solid var(--d-card);
}
.card-head {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
  height: 44px;
  padding: 0 16px;
  border-bottom: 1px solid var(--d-border);
  background: var(--d-fill-soft);
}
.card-head__icon {
  color: var(--d-text-3);
  font-size: 14px;
}
.card-head__title {
  overflow: hidden;
  color: var(--d-text);
  font-size: 14px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.card-head__count {
  margin-left: auto;
  color: var(--d-text-3);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
/* 分组标题后的中文释义：ERP / MES 这类缩写需要一句人话说明范围 */
.card-head__desc {
  overflow: hidden;
  color: var(--d-text-3);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.card-body {
  /* 与 .card-sections 同口径：分类一行并排，放不下就底部横向滚动 */
  display: flex;
  align-items: flex-start;
  gap: 0 12px;
  max-height: calc(var(--fly-card-max-height, 420px) - 44px);
  overflow: auto;
  padding: 12px 16px 12px;
  scrollbar-width: thin;
}
.card-body::-webkit-scrollbar {
  height: 8px;
}
.card-body::-webkit-scrollbar-thumb {
  background: var(--d-border);
  border-radius: 4px;
}

/* ===== 两级浮层：左模块清单 + 右当前模块入口 ===== */
.card-split {
  display: flex;
  min-height: 0;
  max-height: calc(var(--fly-card-max-height, 420px) - 44px);
}
.card-side {
  display: flex;
  flex: 0 0 176px;
  flex-direction: column;
  gap: 2px;
  overflow-y: auto;
  padding: 8px;
  border-right: 1px solid var(--d-border);
  background: var(--d-fill-soft);
}
.side-item {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  min-height: 40px;
  padding: 0 8px 0 10px;
  border: 0;
  border-radius: var(--d-radius-sm);
  background: transparent;
  color: var(--d-text);
  font: inherit;
  font-size: 14px;
  text-align: left;
  cursor: pointer;
  transition: background-color 0.15s, color 0.15s;
}
.side-item__label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.side-item__arrow {
  flex: 0 0 auto;
  color: var(--d-text-3);
  font-size: 12px;
  opacity: 0;
  transition: opacity 0.15s;
}
.side-item:hover {
  background: var(--d-fill);
}
.side-item:hover .side-item__arrow {
  opacity: 1;
}
.side-item.on {
  background: var(--d-card);
  color: var(--d-primary);
  font-weight: 600;
  box-shadow: inset 0 0 0 1px var(--d-border-strong);
}
.side-item.on .side-item__arrow {
  opacity: 1;
  color: var(--d-primary);
}
.side-item:focus-visible {
  outline: 2px solid var(--d-primary);
  outline-offset: -2px;
}
.card-main {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  overflow: hidden;
}
.card-main__head {
  display: flex;
  flex: 0 0 auto;
  align-items: baseline;
  gap: 8px;
  padding: 10px 16px 6px;
  color: var(--d-text);
  font-size: 14px;
  font-weight: 600;
}
.card-main__head em {
  color: var(--d-text-3);
  font-size: 12px;
  font-style: normal;
  font-weight: 400;
}
/* 两级浮层需要更宽的卡面：右栏才能排成两列，条目不必竖着排到屏幕外 */
.fly-card:has(.card-split) {
  min-width: 880px;
  /* 分类按列并排：主区需要约 700px 才放得下 4 列（4×132 + 间距），否则会退成 2 列、
     最后一两个小分类被迫独占一行，浮层被拉得很长 */
  max-width: min(1220px, calc(100vw - 200px));
}
.card-sections {
  /* 所有分类排成一行并排：分类 = 一列，行与行上下对齐，
     于是「单据第 i 行」正对着「明细表/统计表第 i 行」，眼一扫就知道对应关系。
     6 列 × 128px + 5×10px = 818px，1440 屏主区约 890px 能一次排开；
     再窄时底部出现横向滚动条（不折行，折行会破坏上面的行对应关系）。 */
  display: flex;
  align-items: flex-start;
  gap: 0 10px;
  overflow: auto;
  padding: 4px 8px 12px;
  scrollbar-width: thin;
}
.card-sections::-webkit-scrollbar {
  height: 8px;
}
.card-sections::-webkit-scrollbar-thumb {
  background: var(--d-border);
  border-radius: 4px;
}
.card-main__empty {
  padding: 24px 16px;
  color: var(--d-text-3);
  font-size: 12px;
}
.card-group {
  flex: 0 0 auto;
  /* 128px + 12px 间距：6 列共 828px，1440 屏主区约 872px 能一次排开、不出滚动条；
     「产成品入库明细表」这类 8 字标题也放得下，不会被省略号截断。
     再窄时按用户要求退化为底部横向滚动条。 */
  width: 128px;
  display: flex;
  flex-direction: column;
}
/* 分类之间的间隔交给网格 row-gap：原来每节各自 padding-top + border-top，
   同一行两节的标题就会差一条线的高度而错位 */
/* 分类标题：比条目更重、更深，右侧数量做成小胶囊，扫读时先把「分类」和「条目」分开 */
.card-group-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  padding: 0 8px;
  color: var(--d-text);
  font-size: 13px;
  font-weight: 600;
  line-height: 22px;
  letter-spacing: 0.2px;
  /* 各列标题等高，下面第 i 行才能一一对齐 */
  min-height: 22px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.card-group-title em {
  flex: 0 0 auto;
  padding: 0 8px;
  border-radius: 100px;
  background: var(--d-fill);
  color: var(--d-text-3);
  font-style: normal;
  font-size: 12px;
  font-weight: 400;
  line-height: 20px;
  font-variant-numeric: tabular-nums;
}
.card-items {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
/* 分类一律单列：多列会让「单据第 i 行」与「明细/统计第 i 行」错位 */
/* 一行 = 单据 + 它的统计表/明细表：报表收成行内小链接，不再各占一行 */
.card-row {
  display: flex;
  align-items: stretch;
  flex-wrap: nowrap;
  gap: 2px 4px;
  min-height: 40px;
  min-width: 0;
}
.card-row .card-item {
  flex: 1;
  min-width: 0;
}
.card-links {
  display: flex;
  flex: 0 0 auto;
  gap: 2px;
}
.card-link {
  display: inline-flex;
  align-items: center;
  height: 100%;
  padding: 0 8px;
  border: 0;
  border-radius: var(--d-radius-sm);
  background: transparent;
  color: var(--d-text-3);
  font: inherit;
  font-size: 12px;
  line-height: 20px;
  white-space: nowrap;
  cursor: pointer;
  transition: background-color 0.15s, color 0.15s;
}
.card-link:hover {
  background: var(--d-fill);
  color: var(--d-primary);
}
.card-link:focus-visible {
  outline: 2px solid var(--d-primary);
  outline-offset: -2px;
}
.card-item {
  display: flex;
  align-items: center;
  min-width: 0;
  min-height: 40px;
  padding: 0 5px;
  border: 0;
  border-radius: var(--d-radius-sm);
  background: transparent;
  color: var(--d-text);
  font: inherit;
  font-size: 14px;
  line-height: 20px;
  text-align: left;
  cursor: pointer;
  white-space: nowrap;
  transition: background-color 0.15s, color 0.15s;
}
/* 单据行（带报表链接）比纯报表行更重：一眼分清「单据」和「报表」 */
.card-item.is-parent {
  font-weight: 600;
}
.card-item.is-leaf {
  color: var(--d-text-2);
  font-weight: 400;
}
.card-item.nested {
  color: var(--d-text-2);
  font-size: 13px;
}
.card-item span {
  overflow: hidden;
  text-overflow: ellipsis;
}
.card-item:hover {
  background: var(--d-fill);
  color: var(--d-primary);
}
.card-item:focus-visible {
  outline: 2px solid var(--d-primary);
  outline-offset: -2px;
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
