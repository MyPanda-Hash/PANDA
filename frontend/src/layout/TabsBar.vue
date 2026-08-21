<template>
  <div class="tabsbar">
    <div class="tabs" ref="tabsWrapRef">
      <div
        v-for="t in tabs.tabs"
        :key="t.path"
        class="tab"
        :class="{ active: tabs.active === t.path }"
        @click="go(t)"
        @contextmenu.prevent="showCtx(t, $event)"
      >
        <span>{{ t.title }}</span>
        <el-icon v-if="!t.affix" class="close" @click.stop="closeAndGo(t.path)"><Close /></el-icon>
      </div>
    </div>

    <!-- T+ tabs-right 按钮组：快速查找 / 最大化恢复 / 关闭全部 / 更多 -->
    <div class="tab-actions">
      <el-popover v-model:visible="findPop" placement="bottom-end" :width="260" trigger="click">
        <template #reference>
          <el-tooltip content="快速查找单据" placement="bottom">
            <el-icon class="action-icon"><Search /></el-icon>
          </el-tooltip>
        </template>
        <div class="find-box">
          <el-input
            v-model="findNo"
            placeholder="单据编号（如 MO20260813-001）"
            size="small"
            clearable
            @keyup.enter="quickFind"
          >
            <template #append>
              <el-icon class="find-btn" @click="quickFind"><Search /></el-icon>
            </template>
          </el-input>
        </div>
      </el-popover>

      <el-tooltip :content="app.maxContent ? '恢复' : '最大化'" placement="bottom">
        <el-icon class="action-icon" @click="app.toggleMaxContent()">
          <FullScreen v-if="!app.maxContent" />
          <Crop v-else />
        </el-icon>
      </el-tooltip>

      <el-tooltip content="关闭全部页签" placement="bottom">
        <el-icon class="action-icon" @click="closeAll"><Close /></el-icon>
      </el-tooltip>

      <el-dropdown @command="onMore">
        <el-tooltip content="更多" placement="bottom">
          <el-icon class="action-icon"><ArrowDown /></el-icon>
        </el-tooltip>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item v-for="t in tabs.tabs.filter((x) => !x.affix)" :key="t.path" :command="`go:${t.path}`">
              {{ t.title }}
            </el-dropdown-item>
            <el-dropdown-item v-if="tabs.tabs.every((x) => x.affix)" disabled>暂无其他页签</el-dropdown-item>
            <el-dropdown-item divided command="others">关闭其他</el-dropdown-item>
            <el-dropdown-item command="all">关闭全部</el-dropdown-item>
            <el-dropdown-item divided command="refresh">刷新当前</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>

    <ul v-if="ctx.tab" class="ctx-menu" :style="{ left: ctx.x + 'px', top: ctx.y + 'px' }" @mouseleave="ctx.tab = null">
      <li @click="doCtx('refresh')">刷新</li>
      <li v-if="!ctx.tab.affix" @click="doCtx('close')">关闭</li>
      <li @click="doCtx('others')">关闭其他</li>
      <li @click="doCtx('all')">关闭全部</li>
    </ul>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useTabsStore } from '@/stores/tabs'
import { useAppStore } from '@/stores/app'
import { apiPageManuOrders } from '@/business/api'
import { ElMessage } from 'element-plus'

const tabs = useTabsStore()
const app = useAppStore()
const router = useRouter()
const route = useRoute()

// 路由变化时同步活动页签的 query/标题（含 生单跳转 ?code= 等），保证切回页签数据不丢
watch(
  () => route.fullPath,
  () => tabs.sync(route),
  { immediate: true }
)
const ctx = ref({ tab: null, x: 0, y: 0 })
const findPop = ref(false)
const findNo = ref('')

function go(t) {
  tabs.setActive(t.path)
  // 恢复页签携带的 query（如 ?code=单据号），修复切换单据回来数据丢失
  router.push({ path: t.path, query: t.query || {} })
}

function refresh() {
  router.replace({ path: route.path, query: { ...route.query, _t: Date.now() } })
}

function showCtx(tab, e) {
  ctx.value = { tab, x: e.clientX, y: e.clientY + 4 }
}

function doCtx(cmd) {
  const { tab } = ctx.value
  ctx.value.tab = null
  if (cmd === 'refresh') refresh()
  else if (cmd === 'close') closeAndGo(tab.path)
  else if (cmd === 'others') { tabs.closeOthers(tab.path); router.replace(tab.path) }
  else if (cmd === 'all') { tabs.closeAll(); router.replace('/dashboard') }
}

function closeAndGo(path) {
  tabs.close(path)
  if (route.path === path) {
    const act = tabs.tabs.find((t) => t.path === tabs.active)
    router.replace(act ? { path: act.path, query: act.query || {} } : tabs.active)
  }
}

function closeAll() {
  tabs.closeAll()
  router.replace('/dashboard')
}

function onMore(cmd) {
  if (!cmd) return
  if (cmd.startsWith('go:')) {
    const p = cmd.slice(3)
    tabs.setActive(p)
    const t = tabs.tabs.find((x) => x.path === p)
    router.push({ path: p, query: t ? t.query || {} : {} })
  } else if (cmd === 'others') {
    tabs.closeOthers(tabs.active)
  } else if (cmd === 'all') {
    closeAll()
  } else if (cmd === 'refresh') {
    refresh()
  }
}

// 快速查找单据：按编号搜索生产加工单（T+ searchVoucher 对应能力）
async function quickFind() {
  const no = findNo.value.trim()
  if (!no) return ElMessage.warning('请输入单据编号')
  try {
    const res = await apiPageManuOrders({ orderNo: no, pageNo: 1, pageSize: 5 })
    const hit = res.records?.[0] || res.list?.[0]
    if (!hit) {
      ElMessage.warning(`未找到单据：${no}`)
      return
    }
    const path = `/panelx/form/MANU_ORDER?id=${hit.id}`
    tabs.open({ title: `加工单 ${hit.orderNo}`, path: '/panelx/form/MANU_ORDER', query: { id: hit.id } })
    router.push(path)
    findPop.value = false
    findNo.value = ''
  } catch (e) {
    ElMessage.error(e?.response?.data?.message || '查询失败')
  }
}
</script>

<style scoped>
.tabsbar {
  height: 32px;
  flex-shrink: 0;
  display: flex;
  align-items: stfetch;
  background: var(--t-content-bg);
  border-bottom: 1px solid var(--t-border);
}
.tabs {
  display: flex;
  padding: 0 8px;
  overflow: hidden;
  flex: 1;
}
.tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 0 14px;
  height: 32px;
  font-size: 13px;
  color: var(--t-text-2);
  border-bottom: 3px solid transparent;
  cursor: pointer;
  white-space: nowrap;
  user-select: none;
}
.tab:hover {
  color: var(--t-text-1);
}
.tab.active {
  color: var(--t-text-1);
  border-bottom-color: var(--t-tab-active);
  font-weight: 600;
}
.tab .close {
  font-size: 13px;
  border-radius: 50%;
}
.tab .close:hover {
  background: #d9dee5;
  color: var(--t-text-1);
}
.tab-actions {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 0 12px;
  flex-shrink: 0;
}
.action-icon {
  font-size: 15px;
  color: var(--t-text-2);
  cursor: pointer;
  outline: none;
}
.action-icon:hover {
  color: var(--t-primary);
}
.find-box {
  padding: 2px 0;
}
.find-btn {
  cursor: pointer;
}
.ctx-menu {
  position: fixed;
  z-index: 3000;
  background: var(--t-card-bg);
  border: 1px solid var(--t-border);
  border-radius: 6px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.12);
  list-style: none;
  margin: 0;
  padding: 4px 0;
  min-width: 110px;
}
.ctx-menu li {
  padding: 7px 16px;
  font-size: 13px;
  cursor: pointer;
  color: var(--t-text-1);
}
.ctx-menu li:hover {
  background: var(--t-hover-bg);
  color: var(--t-primary);
}

/* ===== 移动端（≤768px）：页签横向滑动 + 操作区紧凑 ===== */
@media (max-width: 768px) {
  .tabs {
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }
  .tab {
    padding: 0 10px;
    font-size: 13px;
  }
  .tab-actions {
    gap: 8px;
    padding: 0 8px;
  }
  .action-icon {
    font-size: 16px;
    padding: 4px;
  }
}
</style>
