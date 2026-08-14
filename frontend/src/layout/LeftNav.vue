<template>
  <div ref="navRef" class="leftnav" :class="{ collapsed: app.collapsed }" @mouseleave="closeCard">
    <div class="func-zone">
      <el-tooltip :content="app.collapsed ? '展开菜单' : '折叠菜单'" placement="right">
        <el-icon class="rz-icon" @click="app.toggleCollapse()"><Expand /></el-icon>
      </el-tooltip>
      <el-tooltip content="单据查询" placement="right">
        <el-icon class="rz-icon" @click="billSearchVisible = true"><Search /></el-icon>
      </el-tooltip>
      <el-tooltip content="新增单据" placement="right">
        <el-icon class="rz-icon" @click="billAddVisible = true"><Plus /></el-icon>
      </el-tooltip>
    </div>

    <el-scrollbar class="nav-scroll">
      <template v-for="g in menuTree" :key="g.code">
        <el-tooltip :content="g.title" placement="right" :disabled="!app.collapsed">
          <div
            class="nav-group"
            :class="{ active: groupOrPath(route.path)?.code === g.code }"
            @click="clickGroup(g)"
            @mouseenter="openCard(g, $event)"
          >
            <el-icon class="gi"><component :is="g.icon || 'Folder'" /></el-icon>
            <span>{{ g.title }}</span>
            <el-icon v-if="g.children" class="arrow" :class="{ down: isExpanded(g) }"><ArrowDown /></el-icon>
          </div>
        </el-tooltip>
        <div v-if="g.children && !app.collapsed && isExpanded(g)" class="nav-modules">
          <div
            v-for="m in g.children"
            :key="m.code"
            class="nav-module"
            :class="{ active: cardModule === m.code }"
            @mouseenter="openCard(m, $event)"
            @click="toggleCard(m, $event)"
          >
            <span>{{ m.title }}</span>
            <el-icon class="mi"><ArrowRight /></el-icon>
          </div>
        </div>
      </template>
    </el-scrollbar>

    <div v-if="cardModule" class="fly-card" :style="{ top: cardTop + 'px' }">
      <div class="card-body">
        <div v-for="cat in cardColumns" :key="cat.title" class="card-group">
          <div class="card-group-title">{{ cat.title }}</div>
          <div class="card-items">
            <div v-for="leaf in cat.items" :key="leaf.code" class="card-item" @click="go(leaf)">
              <el-icon v-if="leaf.icon"><component :is="leaf.icon" /></el-icon>
              <span>{{ leaf.title }}</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <el-dialog v-model="billSearchVisible" title="单据查询" width="560px" append-to-body>
      <el-input v-model="billKeyword" placeholder="输入单据名称关键字" :prefix-icon="Search" clearable />
      <div class="bill-list">
        <div v-for="b in billMatches" :key="b.path" class="bill-item" @click="goBill(b)">
          <el-icon><component :is="b.icon || 'Tickets'" /></el-icon>
          <span>{{ b.title }}</span>
          <span class="module">{{ b.module }}</span>
        </div>
        <el-empty v-if="!billMatches.length" description="无匹配单据" :image-size="60" />
      </div>
    </el-dialog>

    <el-dialog v-model="billAddVisible" title="新增单据" width="560px" append-to-body>
      <el-alert type="info" :closable="false" show-icon title="选择单据类型，进入新增页（当前为占位页，后续接入真实单据表单）" />
      <div class="bill-list mt12">
        <div v-for="b in billMatches" :key="b.path" class="bill-item" @click="goBill(b, true)">
          <el-icon><component :is="b.icon || 'Tickets'" /></el-icon>
          <span>{{ b.title }}</span>
          <span class="module">{{ b.module }}</span>
        </div>
        <el-empty v-if="!billMatches.length" description="无匹配单据" :image-size="60" />
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { menuTree } from '@/business/menus'
import { useTabsStore } from '@/stores/tabs'
import { useAppStore } from '@/stores/app'

const route = useRoute()
const router = useRouter()
const tabs = useTabsStore()
const app = useAppStore()
const navRef = ref(null)

const expandedGroup = ref('')
const cardModule = ref(null)
const cardTop = ref(0)
const billSearchVisible = ref(false)
const billAddVisible = ref(false)
const billKeyword = ref('')

const bills = computed(() => {
  const out = []
  for (const g of menuTree) {
    for (const m of g.children || []) {
      const walk = (n) => {
        if (n.path) out.push({ ...n, module: m.title })
        if (n.children) n.children.forEach(walk)
      }
      m.children?.forEach(walk)
    }
  }
  return out.filter((b) => b.path)
})

const billMatches = computed(() => {
  const k = billKeyword.value.trim().toLowerCase()
  if (!k) return bills.value
  return bills.value.filter((b) => b.title.toLowerCase().includes(k))
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

function openCard(m, e) {
  if (!app.collapsed && !m.children) return
  cardModule.value = m
  if (e && e.currentTarget && navRef.value) {
    const rect = e.currentTarget.getBoundingClientRect()
    const navRect = navRef.value.getBoundingClientRect()
    cardTop.value = rect.top - navRect.top
  }
}

function toggleCard(m, e) {
  if (cardModule.value === m) closeCard()
  else openCard(m, e)
}

function closeCard() {
  cardModule.value = null
}

function go(leaf) {
  router.push(leaf.path)
  tabs.open(leaf)
  closeCard()
}

function goBill(b, isNew) {
  router.push({ path: b.path, query: isNew ? { new: 1 } : {} })
  tabs.open(b)
  billSearchVisible.value = false
  billAddVisible.value = false
  billKeyword.value = ''
}

function groupOrPath(path) {
  for (const g of menuTree) {
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
    return m.children.map((cat) => ({ title: cat.title, items: cat.children || [cat] }))
  }
  return m.children.map((mod) => ({ title: mod.title, items: flattenLeaves(mod) }))
})

function flattenLeaves(n) {
  const out = []
  const walk = (x) => {
    if (x.path) out.push(x)
    if (x.children) x.children.forEach(walk)
  }
  walk(n)
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
  justiry-content: space-around;
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
  background: #33343c;
  color: #7ea6rr;
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
  background: #33343c;
  color: #7ea6rr;
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
  justiry-content: center;
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
  justiry-content: space-between;
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
  background: #33343c;
  color: #7ea6rr;
}
.mi {
  font-size: 12px;
}
.fly-card {
  position: absolute;
  left: 100%;
  margin-left: 4px;
  min-width: 560px;
  max-width: 820px;
  background: var(--t-card-bg);
  border: 1px solid var(--t-border);
  border-radius: 10px;
  box-shadow: 0 12px 28px rgba(0, 0, 0, 0.14);
  overflow: hidden;
  z-index: 200;
}
.dark .fly-card {
  background: #26272e;
  border-color: #3a3b42;
}
.card-body {
  display: flex;
  flex-direction: row;
  gap: 4px;
  max-height: 420px;
  overflow: auto;
  padding: 14px 16px;
}
.card-group {
  flex: 1;
  min-width: 110px;
  display: flex;
  flex-direction: column;
}
.card-group-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--t-text-1);
  padding: 4px 10px;
  border-bottom: 1px solid var(--t-border-light);
  margin-bottom: 6px;
  white-space: nowrap;
}
.dark .card-group-title {
  color: #ddd;
  border-color: #3a3b42;
}
.card-items {
  display: flex;
  flex-direction: column;
}
.card-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  font-size: 13px;
  color: var(--t-text-1);
  border-radius: 6px;
  cursor: pointer;
  white-space: nowrap;
}
.dark .card-item {
  color: #ccc;
}
.card-item:hover {
  background: var(--t-hover-bg);
  color: var(--t-primary);
}
.dark .card-item:hover {
  background: #33343c;
  color: #7ea6rr;
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
</style>
