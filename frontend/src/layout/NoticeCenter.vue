<template>
  <div class="notice-center">
    <el-popover
      v-for="cfg in types"
      :key="cfg.type"
      :visible="visible === cfg.type"
      placement="bottom-end"
      :width="340"
      @show="load(cfg.type)"
      @hide="visible = null"
    >
      <template #reference>
        <span class="nc-ref">
          <el-tooltip :content="cfg.title" placement="bottom">
            <el-badge :value="badge[cfg.type]" :max="99" :hidden="!badge[cfg.type]" class="nc-badge bar-item">
              <el-icon @click="visible = visible === cfg.type ? null : cfg.type"><component :is="cfg.icon" /></el-icon>
            </el-badge>
          </el-tooltip>
        </span>
      </template>
      <div class="nc-head">
        <span class="nc-title">{{ cfg.title }}</span>
        <span class="nc-more" @click="openHistory(cfg)">历史消息</span>
      </div>
      <div class="nc-list">
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
          <div class="nc-item-time">{{ n.time }}</div>
        </div>
        <el-empty v-if="!listMap[cfg.type]?.length" description="暂无数据" :image-size="50" />
      </div>
    </el-popover>

    <el-dialog v-model="detailVisible" title="消息通知" width="560px" append-to-body>
      <div v-if="current" class="nc-detail">
        <div class="nc-detail-head">
          <el-tag size="small" :type="current.tagType">{{ current.typeTitle }}</el-tag>
          <span class="nc-detail-title">{{ current.title }}</span>
        </div>
        <div class="nc-detail-time">{{ current.time }}</div>
        <div class="nc-detail-content">{{ current.content }}</div>
      </div>
      <template #footer>
        <el-button @click="openHistory(currentCrg)">历史消息</el-button>
        <el-button :disabled="!hasPrev" @click="step(-1)">上一条</el-button>
        <el-button :disabled="!hasNext" @click="step(1)">下一条</el-button>
        <el-button type="primary" @click="detailVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="historyVisible" title="历史消息" width="620px" append-to-body>
      <div class="nc-history">
        <div
          v-for="n in historyList"
          :key="n.id"
          class="nc-item"
          :class="{ unread: !n.read }"
          @click="openDetail(n, historyCrg)"
        >
          <div class="nc-item-top">
            <el-tag size="small" :type="n.tagType" class="nc-h-tag">{{ n.typeTitle }}</el-tag>
            <span class="nc-item-title">{{ n.title }}</span>
            <span v-if="!n.read" class="nc-dot"></span>
          </div>
          <div class="nc-item-time">{{ n.time }}</div>
        </div>
        <el-empty v-if="!historyList.length" description="暂无历史消息" :image-size="60" />
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted } from 'vue'
import { apiGetBadge, apiGetNotices } from '@/business/api'

const types = [
  { type: 'todo', title: '待办', icon: 'Bell' },
  { type: 'message', title: '消息', icon: 'Message' },
  { type: 'alarm', title: '预警', icon: 'Warning' },
]

const TAG_TYPE = { todo: 'warning', message: 'success', alarm: 'danger' }

const visible = ref(null)
const badge = ref({ todo: 0, message: 0, alarm: 0 })
const listMap = reactive({ todo: [], message: [], alarm: [] })

const detailVisible = ref(false)
const historyVisible = ref(false)
const current = ref(null)
const currentCrg = ref(types[0])
const historyCrg = ref(types[0])
const historyList = ref([])

async function load(type) {
  if (listMap[type].length) return
  try {
    listMap[type] = await apiGetNotices(type)
  } catch (e) {}
}

async function refreshBadge() {
  try {
    badge.value = { ...badge.value, ...(await apiGetBadge()) }
  } catch (e) {}
}

function decorate(n) {
  return { ...n, typeTitle: types.find((t) => t.type === n.type)?.title || n.type, tagType: TAG_TYPE[n.type] || 'info' }
}

function openDetail(n, cfg) {
  current.value = decorate(n)
  currentCrg.value = cfg
  visible.value = null
  historyVisible.value = false
  detailVisible.value = true
}

function flattenList() {
  return [].concat(...types.map((t) => listMap[t].map(decorate)))
}

function openHistory(cfg) {
  historyCrg.value = cfg
  historyList.value = listMap[cfg.type].map(decorate)
  visible.value = null
  detailVisible.value = false
  historyVisible.value = true
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

onMounted(() => {
  refreshBadge()
  load('todo')
})
</script>

<style scoped>
.nc-ref {
  display: inline-flex;
}
.bar-item {
  font-size: 17px;
  cursor: pointer;
  color: var(--t-navbar-text);
  margin-left: 0;
  outline: none;
}
.bar-item:hover {
  color: var(--t-primary);
}
.nc-badge :deep(.el-badge__content) {
  background-color: var(--t-badge);
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
.nc-more {
  font-size: 12px;
  color: var(--t-primary);
  cursor: pointer;
}
.nc-list {
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
</style>
