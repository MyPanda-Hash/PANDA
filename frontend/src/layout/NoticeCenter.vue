<template>
  <div class="notice-center">
    <el-popover
      v-for="cfg in types"
      :key="cfg.type"
      :visible="visible === cfg.type"
      placement="bottom-end"
      :width="340"
      popper-class="notice-popover"
      @show="load(cfg.type)"
      @hide="handleHide(cfg.type)"
    >
      <template #reference>
        <button
          type="button"
          class="nc-ref"
          :class="[`type-${cfg.type}`, { active: visible === cfg.type }]"
          :aria-label="`${tt(cfg.title)}${badge[cfg.type] ? `，${badge[cfg.type]} ${tt('条')}` : ''}`"
          @click="visible = visible === cfg.type ? null : cfg.type"
        >
          <el-tooltip :content="tt(cfg.title)" placement="bottom">
            <el-badge :value="badge[cfg.type]" :max="99" :hidden="!badge[cfg.type]" class="nc-badge">
              <el-icon><component :is="cfg.icon" /></el-icon>
            </el-badge>
          </el-tooltip>
        </button>
      </template>
      <div class="nc-head">
        <div class="nc-heading">
          <span class="nc-title">{{ tt(cfg.title) }}</span>
          <span class="nc-scope">{{ user.account || user.realName }}</span>
        </div>
        <span class="nc-more" @click="openHistory(cfg)">{{ tt('全部') }} {{ badge[cfg.type] || 0 }} {{ tt('项') }}</span>
      </div>
      <div v-loading="loadingMap[cfg.type]" class="nc-list">
        <div
          v-for="n in listMap[cfg.type]"
          :key="n.id"
          class="nc-item"
          :class="{ unread: !n.read }"
          @click="openDetail(n, cfg)"
        >
          <div class="nc-item-top">
            <span class="nc-item-title">{{ n.title }}</span>
            <span v-if="!n.read" class="nc-dot"></span>
          </div>
          <div class="nc-item-summary">{{ n.content }}</div>
          <div class="nc-item-time">{{ n.time }}</div>
        </div>
        <el-empty v-if="!listMap[cfg.type]?.length" :description="tt('暂无数据')" :image-size="50" />
      </div>
    </el-popover>

    <el-dialog v-model="detailVisible" :title="current?.typeTitle ? tt(current.typeTitle) : tt('消息通知')" width="560px" append-to-body>
      <div v-if="current" class="nc-detail">
        <div class="nc-detail-head">
          <el-tag size="small" :type="current.tagType">{{ tt(current.typeTitle) }}</el-tag>
          <span class="nc-detail-title">{{ current.title }}</span>
        </div>
        <div class="nc-detail-time">{{ current.time }}</div>
        <div class="nc-detail-content">{{ current.content }}</div>
      </div>
      <template #footer>
        <el-button @click="openHistory(currentCrg)">{{ tt('全部记录') }}</el-button>
        <el-button :disabled="!hasPrev" @click="step(-1)">{{ tt('上一条') }}</el-button>
        <el-button :disabled="!hasNext" @click="step(1)">{{ tt('下一条') }}</el-button>
        <el-button @click="detailVisible = false">{{ tt('关闭') }}</el-button>
        <el-button v-if="current?.targetPath" type="primary" @click="goCurrent">
          {{ current.actionLabel || tt('前往处理') }}
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="historyVisible" :title="`${tt(historyCrg.title)}${tt('记录')}`" width="620px" append-to-body>
      <div class="nc-history">
        <div
          v-for="n in historyList"
          :key="n.id"
          class="nc-item"
          :class="{ unread: !n.read }"
          @click="openDetail(n, historyCrg)"
        >
          <div class="nc-item-top">
            <el-tag size="small" :type="n.tagType" class="nc-h-tag">{{ tt(n.typeTitle) }}</el-tag>
            <span class="nc-item-title">{{ n.title }}</span>
            <span v-if="!n.read" class="nc-dot"></span>
          </div>
          <div class="nc-item-summary">{{ n.content }}</div>
          <div class="nc-item-time">{{ n.time }}</div>
        </div>
        <el-empty v-if="!historyList.length" :description="tt('暂无历史消息')" :image-size="60" />
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { apiGetBadge, apiGetNotices } from '@/business/api'
import { useUserStore } from '@/stores/user'
import { tt } from '@/i18n'

const router = useRouter()
const user = useUserStore()

const types = [
  { type: 'todo', title: '待办', icon: 'Bell' },
  { type: 'message', title: '消息', icon: 'Message' },
  { type: 'alarm', title: '预警', icon: 'Warning' },
]

const TAG_TYPE = { todo: 'warning', message: 'success', alarm: 'danger' }

const visible = ref(null)
const badge = ref({ todo: 0, message: 0, alarm: 0 })
const listMap = reactive({ todo: [], message: [], alarm: [] })
const loadingMap = reactive({ todo: false, message: false, alarm: false })

const detailVisible = ref(false)
const historyVisible = ref(false)
const current = ref(null)
const currentCrg = ref(types[0])
const historyCrg = ref(types[0])
const historyList = ref([])

async function load(type) {
  if (loadingMap[type]) return
  loadingMap[type] = true
  try {
    listMap[type] = await apiGetNotices(type)
    badge.value = { ...badge.value, [type]: listMap[type].length }
  } catch (e) {
    listMap[type] = []
  } finally {
    loadingMap[type] = false
  }
}

async function refreshBadge() {
  try {
    badge.value = { ...badge.value, ...(await apiGetBadge()) }
  } catch (e) {}
}

function decorate(n) {
  return { ...n, typeTitle: types.find((t) => t.type === n.type)?.title || n.type, tagType: TAG_TYPE[n.type] || 'info' }
}

function handleHide(type) {
  if (visible.value === type) visible.value = null
}

function openDetail(n, cfg) {
  current.value = decorate(n)
  currentCrg.value = cfg
  visible.value = null
  historyVisible.value = false
  detailVisible.value = true
}

function openHistory(cfg) {
  historyCrg.value = cfg
  historyList.value = listMap[cfg.type].map(decorate)
  visible.value = null
  detailVisible.value = false
  historyVisible.value = true
}

async function goCurrent() {
  if (!current.value?.targetPath) return
  const target = {
    path: current.value.targetPath,
    query: current.value.formNo ? { focus: current.value.formNo } : {},
  }
  detailVisible.value = false
  historyVisible.value = false
  await router.push(target)
}

const hasPrev = computed(() => {
  if (!current.value) return false
  const list = listMap[current.value.type] || []
  return list.findIndex((n) => n.id === current.value.id) > 0
})

const hasNext = computed(() => {
  if (!current.value) return false
  const list = listMap[current.value.type] || []
  const idx = list.findIndex((n) => n.id === current.value.id)
  return idx >= 0 && idx < list.length - 1
})

function step(dir) {
  if (!current.value) return
  const list = listMap[current.value.type] || []
  const idx = list.findIndex((n) => n.id === current.value.id)
  const next = list[idx + dir]
  if (next) current.value = decorate(next)
}

let refreshTimer = null

onMounted(() => {
  Promise.all(types.map((type) => load(type.type)))
  refreshTimer = window.setInterval(() => {
    refreshBadge()
    if (visible.value) load(visible.value)
  }, 60_000)
})

onBeforeUnmount(() => {
  if (refreshTimer) window.clearInterval(refreshTimer)
})
</script>

<style scoped>
.notice-center {
  height: 34px;
  display: inline-grid;
  grid-template-columns: repeat(3, 34px);
  align-items: center;
  flex: 0 0 auto;
  overflow: visible;
  border: 1px solid var(--t-border);
  border-radius: 6px;
  background: var(--t-sidebar-bg);
}

.nc-ref {
  position: relative;
  width: 34px;
  height: 32px;
  display: grid;
  place-items: center;
  margin: 0;
  padding: 0;
  appearance: none;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--t-navbar-text);
  font: inherit;
  font-size: 17px;
  cursor: pointer;
  outline: none;
}

.nc-ref + .nc-ref::before {
  position: absolute;
  top: 7px;
  bottom: 7px;
  left: 0;
  width: 1px;
  background: var(--t-border);
  content: '';
}

.nc-ref:hover,
.nc-ref.active {
  color: var(--t-primary);
  background: var(--t-hover-bg);
}

.nc-ref.type-todo.active { color: #a56b14; background: #fff3dc; }
.nc-ref.type-message.active { color: #456f7e; background: #eaf0f2; }
.nc-ref.type-alarm.active { color: #ad4438; background: #faece9; }

.nc-badge {
  width: 22px;
  height: 22px;
  display: grid;
  place-items: center;
}

.nc-badge :deep(.el-badge__content) {
  top: 0;
  right: 2px;
  min-width: 15px;
  height: 15px;
  padding: 0 3px;
  border: 2px solid var(--t-navbar-bg);
  background-color: var(--t-badge);
  font-size: 9px;
  line-height: 11px;
  transform: translate(52%, -38%);
}
.nc-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  border-bottom: 1px solid var(--t-border-light);
  padding-bottom: 8px;
  margin-bottom: 6px;
}
.nc-title {
  font-weight: 600;
  font-size: 14px;
  color: var(--t-text-1);
}
.nc-heading {
  min-width: 0;
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.nc-scope {
  max-width: 120px;
  overflow: hidden;
  color: var(--t-text-3);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.nc-more {
  font-size: 12px;
  color: var(--t-primary);
  cursor: pointer;
}
.nc-list {
  min-height: 86px;
  max-height: 320px;
  overflow: auto;
}
.nc-item {
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
}
.nc-item:hover {
  background: var(--t-hover-bg);
}
.nc-item-top {
  display: flex;
  align-items: center;
  gap: 6px;
}
.nc-item-title {
  font-size: 13px;
  color: var(--t-text-1);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.nc-item.unread .nc-item-title {
  font-weight: 600;
}
.nc-item-summary {
  display: -webkit-box;
  margin-top: 3px;
  overflow: hidden;
  color: var(--t-text-2);
  font-size: 12px;
  line-height: 18px;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.nc-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--t-badge);
  flex-shrink: 0;
}
.nc-item-time {
  font-size: 12px;
  color: var(--t-text-3);
  margin-top: 2px;
}
.nc-detail-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.nc-detail-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--t-text-1);
}
.nc-detail-time {
  font-size: 12px;
  color: var(--t-text-3);
  margin: 8px 0;
}
.nc-detail-content {
  font-size: 13px;
  line-height: 1.8;
  color: var(--t-text-2);
  background: var(--t-content-bg);
  border-radius: 8px;
  padding: 12px;
  min-height: 80px;
}
.nc-history {
  max-height: 420px;
  overflow: auto;
}
.nc-h-tag {
  flex-shrink: 0;
}

@media (max-width: 768px) {
  .notice-center {
    height: 36px;
    grid-template-columns: repeat(3, 36px);
  }

  .nc-ref {
    width: 36px;
    height: 34px;
    font-size: 18px;
  }
}

:global(.notice-popover) {
  max-width: calc(100vw - 20px);
}
</style>
