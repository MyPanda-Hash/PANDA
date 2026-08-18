<template>
  <div class="dashboard">
    <div class="welcome card">
      <div>
        <div class="hello">{{ greeting }}，{{ user.realName }}！</div>
        <div class="meta">工厂：{{ user.factoryName }} · {{ today }} · 班次：白班</div>
      </div>
      <div class="quick" v-if="quickEntries.length">
        <el-button
          v-for="q in quickEntries"
          :key="q.key"
          :type="q.key === 'newOrder' ? 'primary' : 'default'"
          @click="go(q.path, q.title)"
        >{{ q.title }}</el-button>
      </div>
    </div>

    <el-row :gutter="12" v-if="desk.showKpi">
      <el-col :span="6" v-for="k in kpis" :key="k.title">
        <div class="card kpi">
          <div class="kpi-icon" :style="{ background: k.color }"><el-icon :size="22"><component :is="k.icon" /></el-icon></div>
          <div>
            <div class="kpi-value">{{ k.value }}</div>
            <div class="kpi-title">{{ k.title }}</div>
          </div>
        </div>
      </el-col>
    </el-row>

    <el-row :gutter="12" class="mt" v-if="desk.showProgress || desk.showTodo">
      <el-col :span="desk.showTodo ? 14 : 24" v-if="desk.showProgress">
        <div class="card">
          <div class="card-title">生产进度（生产加工单）</div>
          <el-table :data="orders" size="small">
            <el-table-column prop="编号" label="工单号" width="160" />
            <el-table-column prop="产品" label="产品" min-width="140" />
            <el-table-column prop="数量" label="数量" width="80" />
            <el-table-column label="状态" width="130">
              <template #default="{ row }">
                <el-tag :type="statusTag(row['状态'])" size="small">{{ row['状态'] }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="进度" width="170">
              <template #default="{ row }">
                <el-progress :percentage="statusPercent(row['状态'])" :stroke-width="10" />
              </template>
            </el-table-column>
            <el-table-column prop="时间" label="创建时间" width="120" />
          </el-table>
          <div v-if="!orders.length" class="empty">暂无生产加工单</div>
        </div>
      </el-col>
      <el-col :span="desk.showProgress ? 10 : 24" v-if="desk.showTodo">
        <div class="card">
          <div class="card-title">我的待办（{{ todos.length }}）</div>
          <el-timeline style="padding: 8px 4px">
            <el-timeline-item v-for="(t, i) in todos" :key="i" :timestamp="t['时间']" :type="t['状态'] === '待审批' ? 'warning' : 'primary'">
              {{ t['类型'] }} {{ t['编号'] }}（{{ t['状态'] }}）
            </el-timeline-item>
          </el-timeline>
          <div v-if="!todos.length" class="empty">暂无待办</div>
        </div>
      </el-col>
    </el-row>

    <el-row :gutter="12" class="mt" v-if="desk.showProgress">
      <el-col :span="14">
        <div class="card">
          <div class="card-title">单据统计（真实数据）</div>
          <el-table :data="docStats" size="small">
            <el-table-column prop="panelName" label="单据类型" min-width="120" />
            <el-table-column prop="count" label="总单量" width="80" align="right" />
            <el-table-column label="草稿" width="70" align="right">
              <template #default="{ row }">{{ row.status['草稿'] || 0 }}</template>
            </el-table-column>
            <el-table-column label="已审核" width="80" align="right">
              <template #default="{ row }">{{ row.status['已审核'] || 0 }}</template>
            </el-table-column>
            <el-table-column label="审批中" width="80" align="right">
              <template #default="{ row }">{{ row.status['审批中'] || 0 }}</template>
            </el-table-column>
            <el-table-column label="其他" width="70" align="right">
              <template #default="{ row }">{{ othersOf(row) }}</template>
            </el-table-column>
          </el-table>
        </div>
      </el-col>
      <el-col :span="10">
        <div class="card">
          <div class="card-title">档案与组织概览</div>
          <div class="arch-grid">
            <div class="arch-item"><div class="arch-num">{{ archives.invItems }}</div><div class="arch-label">存货物品</div></div>
            <div class="arch-item"><div class="arch-num">{{ archives.empCount }}</div><div class="arch-label">员工</div></div>
            <div class="arch-item"><div class="arch-num">{{ archives.deptCount }}</div><div class="arch-label">部门</div></div>
            <div class="arch-item"><div class="arch-num">{{ archives.whCount }}</div><div class="arch-label">仓库</div></div>
            <div class="arch-item"><div class="arch-num">{{ archives.roleCount }}</div><div class="arch-label">角色</div></div>
            <div class="arch-item"><div class="arch-num">{{ kpis.invItems }}</div><div class="arch-label">BOM 数</div></div>
          </div>
        </div>
        <div class="card mt">
          <div class="card-title">最新单据</div>
          <el-table :data="latest" size="small" max-height="180">
            <el-table-column prop="panel" label="类型" width="110" />
            <el-table-column prop="编号" label="单号" min-width="130" />
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <el-tag :type="statusTag(row['状态'])" size="small">{{ row['状态'] }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="时间" label="时间" width="120" />
          </el-table>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { useTabsStore } from '@/stores/tabs'
import { useAppStore } from '@/stores/app'
import request from '@core/request'

const user = useUserStore()
const tabs = useTabsStore()
const app = useAppStore()
const router = useRouter()

const desk = computed(() => app.deskSettings)

const QUICK_DEFS = [
  { key: 'newOrder', title: '新建加工单', path: '/panelx/form/MANU_ORDER' },
  { key: 'quickReport', title: '快速报工', path: '/prod/shop/procReport' },
  { key: 'board', title: '生产看板', path: '/prod/manufacture/board' },
]

const quickEntries = computed(() => QUICK_DEFS.filter((q) => desk.value.quick.includes(q.key)))

const today = new Date().toLocaleDateString('zh-CN')
const greeting = computed(() => {
  const h = new Date().getHours()
  if (h < 6) return '凌晨好'
  if (h < 12) return '上午好'
  if (h < 18) return '下午好'
  return '晚上好'
})

const stats = ref({ kpis: {}, docStats: [], progress: [], todos: [], archives: {}, latest: [] })

const kpis = computed(() => [
  { title: '进行中工单', value: stats.value.kpis.moActive ?? '-', icon: 'Odometer', color: '#e8f1ff' },
  { title: '审批中单据', value: stats.value.kpis.approvePending ?? '-', icon: 'Stamp', color: '#fdeef0' },
  { title: '工序汇报单', value: stats.value.kpis.prTotal ?? '-', icon: 'EditPen', color: '#e7f9ef' },
  { title: '存货物品', value: stats.value.archives.invItems ?? '-', icon: 'Grid', color: '#fff6e8' },
])

const orders = computed(() => stats.value.progress || [])
const todos = computed(() => stats.value.todos || [])
const docStats = computed(() => stats.value.docStats || [])
const archives = computed(() => stats.value.archives || {})
const latest = computed(() => stats.value.latest || [])

function statusPercent(st) {
  return { 草稿: 10, 已审核: 35, 生产中: 60, 审批中: 45, 已完工: 100, 已关闭: 100, 已中止: 100 }[st] ?? 20
}
function statusTag(st) {
  return { 已审核: 'success', 已完工: 'success', 生产中: 'primary', 审批中: 'warning', 草稿: 'info', 已中止: 'danger', 已关闭: 'danger' }[st] || 'info'
}
function othersOf(row) {
  const s = row.status || {}
  const total = row.count || 0
  const known = (s['草稿'] || 0) + (s['已审核'] || 0) + (s['审批中'] || 0)
  return Math.max(0, total - known)
}

onMounted(async () => {
  try {
    const r = await request.get('/dashboard/stats')
    stats.value = r?.data || stats.value
  } catch (e) {
    // 接口失败时保留空看板
  }
})

function go(path, title) {
  router.push(path)
  tabs.open({ path, title })
}
</script>

<style scoped>
.card {
  background: #fff;
  border-radius: 10px;
  padding: 16px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.05);
}
.dark .card {
  background: #26272e;
}
.welcome {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  background: linear-gradient(120deg, #2563eb, #3b82f6);
  color: #fff;
}
.hello {
  font-size: 18px;
  font-weight: 600;
}
.meta {
  font-size: 12px;
  opacity: 0.85;
  margin-top: 4px;
}
.quick .el-button {
  margin-left: 8px;
}
.kpi {
  display: flex;
  align-items: center;
  gap: 12px;
}
.kpi-icon {
  width: 46px;
  height: 46px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #2563eb;
}
.kpi-value {
  font-size: 22px;
  font-weight: 700;
}
.kpi-title {
  font-size: 12px;
  color: #9ca3af;
}
.mt {
  margin-top: 12px;
}
.card-title {
  font-weight: 600;
  margin-bottom: 10px;
}
.empty {
  color: #9ca3af;
  font-size: 12px;
  padding: 12px 4px;
}
.arch-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
}
.arch-item {
  background: #f8fafc;
  border-radius: 8px;
  padding: 12px 8px;
  text-align: center;
}
.dark .arch-item {
  background: #2c2d35;
}
.arch-num {
  font-size: 20px;
  font-weight: 700;
  color: #2563eb;
}
.arch-label {
  font-size: 12px;
  color: #9ca3af;
  margin-top: 2px;
}
</style>
