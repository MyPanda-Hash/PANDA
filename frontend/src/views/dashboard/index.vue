<template>
  <div class="dashboard">
    <!-- 欢迎 + 班次（8:00-21:00 白班，其余夜班，到点自动切换） -->
    <div class="welcome card">
      <div class="wl-left">
        <div class="hello">{{ greeting }}，{{ user.realName }}！</div>
        <div class="meta">
          <span class="shift-badge" :class="shift.key">
            <span class="shift-ic">{{ shift.icon }}</span>{{ shift.name }}<em>{{ shift.range }}</em>
          </span>
          <span class="meta-sep">|</span>
          {{ user.factoryName }} · {{ today }} {{ nowTime }}
        </div>
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

    <!-- 看板模块切换 -->
    <div class="mod-tabs">
      <span v-for="m in MODULES" :key="m.key" class="mod-tab" :class="{ on: mod === m.key }" @click="mod = m.key">
        <el-icon><component :is="m.icon" /></el-icon>{{ m.title }}
      </span>
    </div>

    <!-- ===== 概览 ===== -->
    <template v-if="mod === 'overview'">
      <div class="dash-grid">
        <div class="card col-3" v-for="k in kpis" :key="k.title">
          <div class="kpi">
            <div class="kpi-icon" :style="{ background: k.bg, color: k.color }">
              <el-icon :size="22"><component :is="k.icon" /></el-icon>
            </div>
            <div>
              <div class="kpi-value">{{ k.value }}</div>
              <div class="kpi-title">{{ k.title }}</div>
            </div>
          </div>
        </div>

        <div class="card col-8">
          <div class="card-title">单据统计（真实单据）</div>
          <el-table :data="docStats" size="small" max-height="252">
            <el-table-column prop="panelName" label="单据类型" min-width="110" />
            <el-table-column prop="count" label="总单量" width="70" align="right" />
            <el-table-column label="草稿" width="64" align="right">
              <template #default="{ row }">{{ row.status['草稿'] || 0 }}</template>
            </el-table-column>
            <el-table-column label="已审核" width="72" align="right">
              <template #default="{ row }">{{ row.status['已审核'] || 0 }}</template>
            </el-table-column>
            <el-table-column label="审批中" width="72" align="right">
              <template #default="{ row }">{{ row.status['审批中'] || 0 }}</template>
            </el-table-column>
            <el-table-column label="其他" width="64" align="right">
              <template #default="{ row }">{{ othersOf(row) }}</template>
            </el-table-column>
          </el-table>
        </div>

        <div class="card col-4">
          <div class="card-title">我的待办（{{ todos.length }}）</div>
          <el-timeline class="tl" v-if="todos.length">
            <el-timeline-item
              v-for="(t, i) in todos"
              :key="i"
              :timestamp="t['时间']"
              :type="t['状态'] === '待审批' ? 'warning' : 'primary'"
            >{{ t['类型'] }} {{ t['编号'] }}（{{ t['状态'] }}）</el-timeline-item>
          </el-timeline>
          <div v-else class="empty">暂无待办</div>
        </div>

        <div class="card col-6">
          <div class="card-title">最新单据</div>
          <el-table :data="latest" size="small" max-height="216">
            <el-table-column prop="panel" label="类型" width="110" />
            <el-table-column prop="编号" label="单号" min-width="120" />
            <el-table-column label="状态" width="84">
              <template #default="{ row }">
                <el-tag :type="statusTag(row['状态'])" size="small">{{ row['状态'] }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="时间" label="时间" width="116" />
          </el-table>
        </div>

        <div class="card col-6">
          <div class="card-title">档案与组织概览</div>
          <div class="arch-grid">
            <div class="arch-item"><div class="arch-num">{{ archives.invItems ?? '-' }}</div><div class="arch-label">存货物品</div></div>
            <div class="arch-item"><div class="arch-num">{{ archives.empCount ?? '-' }}</div><div class="arch-label">员工</div></div>
            <div class="arch-item"><div class="arch-num">{{ archives.deptCount ?? '-' }}</div><div class="arch-label">部门</div></div>
            <div class="arch-item"><div class="arch-num">{{ archives.whCount ?? '-' }}</div><div class="arch-label">仓库</div></div>
            <div class="arch-item"><div class="arch-num">{{ archives.roleCount ?? '-' }}</div><div class="arch-label">角色</div></div>
            <div class="arch-item"><div class="arch-num">{{ kpis.invItems }}</div><div class="arch-label">BOM 数</div></div>
          </div>
        </div>
      </div>
    </template>

    <!-- ===== 生产 ===== -->
    <template v-else-if="mod === 'prod'">
      <div class="dash-grid">
        <div class="card col-4">
          <div class="card-title">工单状态分布</div>
          <div class="chart-box"><SBars :data="prod.statusDist" /></div>
        </div>
        <div class="card col-4">
          <div class="card-title">车间生产分布</div>
          <div class="chart-box"><SDonut :data="prod.workshopDist" sub="加工单" /></div>
        </div>
        <div class="card col-4">
          <div class="card-title">近 7 天新增 / 完工</div>
          <div class="chart-box"><SLine :data="prod.trend7" /></div>
        </div>
        <div class="card col-12">
          <div class="card-title">BOM 物料树（产品 → 材料，来自生产加工单真实数据）</div>
          <div class="chart-box tree-box"><STree :data="bomTree" /></div>
        </div>
      </div>
    </template>

    <!-- ===== 库存 ===== -->
    <template v-else-if="mod === 'stock'">
      <div class="dash-grid">
        <div class="card col-3"><div class="kpi-num" style="color: #22c55e">{{ stock.totalIn }}</div><div class="kpi-title">入库单量</div></div>
        <div class="card col-3"><div class="kpi-num" style="color: #f59e0b">{{ stock.totalOut }}</div><div class="kpi-title">出库单量</div></div>
        <div class="card col-3"><div class="kpi-num" style="color: #289be5">{{ stockCount }}</div><div class="kpi-title">出入库单据总数</div></div>
        <div class="card col-3"><div class="kpi-num" style="color: #8b5cf6">{{ stock.totalLines }}</div><div class="kpi-title">明细行数合计</div></div>
        <div class="card col-6">
          <div class="card-title">各单据数量</div>
          <div class="chart-box"><SBars :data="stockPanels" /></div>
        </div>
        <div class="card col-6">
          <div class="card-title">各单据明细行数</div>
          <div class="chart-box"><SBars :data="stockLines" :colors="['#8b5cf6', '#06b6d4', '#ec4899', '#84cc16', '#f97316', '#14b8a6']" /></div>
        </div>
      </div>
    </template>

    <!-- ===== 销售 ===== -->
    <template v-else-if="mod === 'sales'">
      <div class="dash-grid">
        <div class="card col-4"><div class="kpi-num" style="color: #289be5">{{ sales.total }}</div><div class="kpi-title">销售订单数</div></div>
        <div class="card col-4"><div class="kpi-num" style="color: #f59e0b">{{ sales.amount ?? 0 }}</div><div class="kpi-title">明细金额合计（元）</div></div>
        <div class="card col-4"><div class="kpi-num" style="color: #22c55e">{{ salesDone }}</div><div class="kpi-title">已审核订单</div></div>
        <div class="card col-6">
          <div class="card-title">客户订单分布</div>
          <div class="chart-box"><SDonut :data="sales.byCustomer" sub="订单" /></div>
        </div>
        <div class="card col-6">
          <div class="card-title">订单状态分布</div>
          <div class="chart-box"><SBars :data="sales.byStatus" /></div>
        </div>
      </div>
    </template>

    <!-- ===== 质量 ===== -->
    <template v-else>
      <div class="dash-grid">
        <div class="card col-3"><div class="kpi-num" style="color: #289be5">{{ quality.total }}</div><div class="kpi-title">检验明细总数</div></div>
        <div class="card col-3"><div class="kpi-num" style="color: #22c55e">{{ quality.pass }}</div><div class="kpi-title">合格数</div></div>
        <div class="card col-3"><div class="kpi-num" style="color: #ef4444">{{ quality.total - quality.pass }}</div><div class="kpi-title">非合格数</div></div>
        <div class="card col-3"><div class="kpi-num" style="color: #8b5cf6">{{ quality.passRate }}%</div><div class="kpi-title">合格率</div></div>
        <div class="card col-6">
          <div class="card-title">检验结果分布</div>
          <div class="chart-box"><SDonut :data="quality.byResult" sub="明细" /></div>
        </div>
        <div class="card col-6">
          <div class="card-title">检验结果对比</div>
          <div class="chart-box"><SBars :data="quality.byResult" /></div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { useTabsStore } from '@/stores/tabs'
import { useAppStore } from '@/stores/app'
import request from '@core/request'
import { ElNotification } from 'element-plus'
import SBars from './SBars.vue'
import SDonut from './SDonut.vue'
import SLine from './SLine.vue'
import STree from './STree.vue'

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

// ---------- 看板模块 ----------
const MODULES = [
  { key: 'overview', title: '概览', icon: 'DataBoard' },
  { key: 'prod', title: '生产', icon: 'Odometer' },
  { key: 'stock', title: '库存', icon: 'Box' },
  { key: 'sales', title: '销售', icon: 'ShoppingCart' },
  { key: 'quality', title: '质量', icon: 'Aim' },
]
const mod = ref('overview')

// ---------- 班次：8:00-21:00 白班，其余夜班；每分钟自动检查，到点自动切换 ----------
const now = ref(new Date())
const shift = computed(() => {
  const h = now.value.getHours()
  return h >= 8 && h < 21
    ? { key: 'day', name: '白班', icon: '☀️', range: '08:00 - 21:00' }
    : { key: 'night', name: '夜班', icon: '🌙', range: '21:00 - 次日 08:00' }
})
const nowTime = computed(() => now.value.toTimeString().slice(0, 5))
const today = computed(() => now.value.toLocaleDateString('zh-CN'))
const greeting = computed(() => {
  const h = now.value.getHours()
  if (h < 6) return '凌晨好'
  if (h < 12) return '上午好'
  if (h < 18) return '下午好'
  return '晚上好'
})
let shiftTimer = null
let lastShift = ''
function startShiftTimer() {
  lastShift = shift.value.key
  shiftTimer = setInterval(() => {
    now.value = new Date()
    if (shift.value.key !== lastShift) {
      lastShift = shift.value.key
      ElNotification({
        title: `已切换至${shift.value.name}`,
        message: `当前班次时段：${shift.value.range}`,
        type: shift.value.key === 'day' ? 'success' : 'info',
        duration: 4000,
      })
    }
  }, 60000)
}

// ---------- 数据加载（真实接口 /dashboard/stats，每 5 分钟刷新） ----------
const stats = ref({
  kpis: {}, docStats: [], progress: [], todos: [], archives: {}, latest: [],
  production: {}, stock: {}, sales: {}, quality: {},
})
async function load() {
  try {
    const r = await request.get('/dashboard/stats')
    if (r?.data) stats.value = { ...stats.value, ...r.data }
  } catch (e) {
    // 接口失败时保留当前看板
  }
}
let refreshTimer = null

onMounted(() => {
  load()
  refreshTimer = setInterval(load, 300000)
  startShiftTimer()
})
onBeforeUnmount(() => {
  clearInterval(refreshTimer)
  clearInterval(shiftTimer)
})

// ---------- 概览数据 ----------
const kpis = computed(() => [
  { title: '进行中工单', value: stats.value.kpis.moActive ?? '-', icon: 'Odometer', bg: '#e8f1ff', color: '#289be5' },
  { title: '审批中单据', value: stats.value.kpis.approvePending ?? '-', icon: 'Stamp', bg: '#fdeef0', color: '#ef4444' },
  { title: '工序汇报单', value: stats.value.kpis.prTotal ?? '-', icon: 'EditPen', bg: '#e7f9ef', color: '#22c55e' },
  { title: '存货物品', value: stats.value.archives.invItems ?? '-', icon: 'Grid', bg: '#fff6e8', color: '#f59e0b' },
])
const todos = computed(() => stats.value.todos || [])
const docStats = computed(() => stats.value.docStats || [])
const archives = computed(() => stats.value.archives || {})
const latest = computed(() => stats.value.latest || [])

// ---------- 生产数据 ----------
const prod = computed(() => stats.value.production || {})
const bomTree = computed(() =>
  (prod.value.bomTree || []).map((p) => ({
    label: p['产品'],
    meta: p['规格型号'] ? `规格：${p['规格型号']}` : '',
    children: (p.materials || []).map((m) => ({
      label: m['名称'],
      meta: `${m['数量']} ${m['单位']}`.trim(),
    })),
  }))
)

// ---------- 库存数据 ----------
const stock = computed(() => stats.value.stock || {})
const stockCount = computed(() => (stock.value.panels || []).reduce((s, p) => s + (p.count || 0), 0))
const stockPanels = computed(() => (stock.value.panels || []).map((p) => ({ name: p.panelName, value: p.count })))
const stockLines = computed(() => (stock.value.panels || []).map((p) => ({ name: p.panelName, value: p.lines })))

// ---------- 销售数据 ----------
const sales = computed(() => stats.value.sales || {})
const salesDone = computed(() => {
  const s = (sales.value.byStatus || []).find((x) => x.name === '已审核')
  return s ? s.value : 0
})

// ---------- 质量数据 ----------
const quality = computed(() => stats.value.quality || { total: 0, pass: 0, passRate: 0, byResult: [] })

// ---------- 工具 ----------
function othersOf(row) {
  const s = row.status || {}
  const known = (s['草稿'] || 0) + (s['已审核'] || 0) + (s['审批中'] || 0)
  return Math.max(0, (row.count || 0) - known)
}
function statusTag(st) {
  return { 已审核: 'success', 已完工: 'success', 生产中: 'primary', 审批中: 'warning', 草稿: 'info', 已中止: 'danger', 已关闭: 'danger' }[st] || 'info'
}
function go(path, title) {
  router.push(path)
  tabs.open({ path, title })
}
</script>

<style scoped>
.card {
  background: var(--t-card-bg);
  border-radius: 10px;
  padding: 14px 16px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.05);
  min-width: 0;
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
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  opacity: 0.92;
  margin-top: 5px;
  flex-wrap: wrap;
}
.shift-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 10px;
  border-radius: 20px;
  font-size: 12px;
  font-weight: 600;
  background: rgba(255, 255, 255, 0.18);
}
.shift-badge.day {
  background: rgba(255, 220, 130, 0.28);
}
.shift-badge.night {
  background: rgba(160, 170, 255, 0.32);
}
.shift-badge em {
  font-style: normal;
  font-weight: 400;
  opacity: 0.85;
}
.meta-sep {
  opacity: 0.5;
}
.quick .el-button {
  margin-left: 8px;
}

/* 模块切换 */
.mod-tabs {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
}
.mod-tab {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 7px 16px;
  font-size: 13px;
  color: var(--t-text-2);
  background: var(--t-card-bg);
  border: 1px solid var(--t-border);
  border-radius: 20px;
  cursor: pointer;
  white-space: nowrap;
  flex-shrink: 0;
}
.mod-tab.on {
  color: #fff;
  background: var(--t-primary);
  border-color: var(--t-primary);
  font-weight: 600;
}

/* 统一网格：12 列，排列有致 */
.dash-grid {
  display: grid;
  grid-template-columns: repeat(12, 1fr);
  gap: 12px;
}
.col-3 { grid-column: span 3; }
.col-4 { grid-column: span 4; }
.col-6 { grid-column: span 6; }
.col-8 { grid-column: span 8; }
.col-12 { grid-column: span 12; }
@media (max-width: 768px) {
  .col-3, .col-4, .col-6, .col-8, .col-12 { grid-column: span 12; }
}

.card-title {
  font-weight: 600;
  margin-bottom: 10px;
  font-size: 14px;
}
.chart-box {
  min-height: 220px;
  display: flex;
  flex-direction: column;
  justify-content: center;
}
.tree-box {
  min-height: auto;
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
  flex-shrink: 0;
}
.kpi-value {
  font-size: 22px;
  font-weight: 700;
}
.kpi-title {
  font-size: 12px;
  color: var(--t-text-3);
  margin-top: 1px;
}
.kpi-num {
  font-size: 26px;
  font-weight: 700;
}
.tl {
  padding: 6px 2px;
}
.empty {
  color: var(--t-text-3);
  font-size: 12px;
  padding: 16px 2px;
}
.arch-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
}
.arch-item {
  background: var(--t-border-light);
  border-radius: 8px;
  padding: 12px 8px;
  text-align: center;
}
.arch-num {
  font-size: 20px;
  font-weight: 700;
  color: var(--t-primary);
}
.arch-label {
  font-size: 12px;
  color: var(--t-text-3);
  margin-top: 2px;
}
</style>