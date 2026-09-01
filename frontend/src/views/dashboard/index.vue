<template>
  <div class="dashboard">
    <!-- 欢迎 + 班次（8:00-21:00 白班，其余夜班，到点自动切换） -->
    <div class="welcome card">
      <div class="wl-left">
        <div class="hello">{{ tt(greeting) }}，{{ user.realName }}！</div>
        <div class="meta">
          <span class="shift-badge" :class="shift.key">
            <span class="shift-ic">{{ shift.icon }}</span>{{ tt(shift.name) }}<em>{{ tt(shift.range) }}</em>
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
        >{{ tt(q.title) }}</el-button>
      </div>
    </div>

    <!-- 看板模块切换 -->
    <div class="mod-tabs">
      <span v-for="m in MODULES" :key="m.key" class="mod-tab" :class="{ on: mod === m.key }" @click="mod = m.key">
        <el-icon><component :is="m.icon" /></el-icon>{{ tt(m.title) }}
      </span>
    </div>

    <!-- ===== 概览 ===== -->
    <template v-if="mod === 'overview'">
      <div class="dash-grid">
        <template v-if="desk.showKpi">
          <article
            v-for="(metric, index) in kpis"
            :key="metric.title"
            class="card live-metric col-3 reveal-item"
            :class="`metric-${metric.tone}`"
            :style="{ '--reveal-delay': `${index * 55}ms` }"
          >
            <div class="metric-head">
              <span class="metric-label">{{ metric.title }}</span>
              <span class="metric-state">{{ metric.state }}</span>
            </div>
            <div class="metric-main">
              <div class="metric-icon">
                <el-icon><component :is="metric.icon" /></el-icon>
              </div>
              <div class="metric-reading">
                <strong>{{ metric.value }}</strong>
                <span>{{ metric.unit }}</span>
              </div>
              <div class="micro-bars" aria-hidden="true">
                <i
                  v-for="(value, barIndex) in metric.bars"
                  :key="barIndex"
                  :style="{ height: `${barHeight(metric.bars, value)}%` }"
                ></i>
              </div>
            </div>
            <div class="metric-foot">
              <span>{{ metric.meta }}</span>
              <span class="metric-signal"><i></i>{{ metric.signal }}</span>
            </div>
          </article>
        </template>

        <section v-if="desk.showProgress" class="card operation-core col-8 reveal-item">
          <div class="panel-heading">
            <div>
              <div class="card-title">{{ tt('生产执行核心') }}</div>
              <p>{{ tt('工单生命周期与近 7 天执行脉冲') }}</p>
            </div>
            <div class="live-chip"><i></i>LIVE · {{ tt(lastUpdatedText) }}</div>
          </div>

          <div class="core-layout">
            <div class="execution-gauge">
              <div class="gauge-visual">
                <svg viewBox="0 0 120 120" aria-hidden="true">
                  <circle class="gauge-track" cx="60" cy="60" r="48" />
                  <circle
                    class="gauge-value"
                    cx="60"
                    cy="60"
                    r="48"
                    :style="{ strokeDashoffset: gaugeOffset }"
                  />
                </svg>
                <div class="gauge-copy">
                  <strong>{{ completionRate }}%</strong>
                  <span>{{ tt('工单闭环率') }}</span>
                </div>
              </div>
              <div class="gauge-facts">
                <span><b>{{ productionTotal }}</b>{{ tt('总工单') }}</span>
                <span><b>{{ productionRunning }}</b>{{ tt('执行中') }}</span>
                <span><b>{{ productionDone }}</b>{{ tt('已完工') }}</span>
              </div>
            </div>

            <div class="execution-detail">
              <div class="trend-head">
                <span>{{ tt('新增 / 完工趋势') }}</span>
                <span>{{ tt('近 7 天') }}</span>
              </div>
              <SLine :data="prod.trend7 || []" compact />
              <div class="order-queue">
                <div v-for="order in progress.slice(0, 3)" :key="order['编号']" class="order-row">
                  <div class="order-copy">
                    <strong>{{ order['产品'] || tt('未指定产品') }}</strong>
                    <span>{{ order['编号'] }}</span>
                  </div>
                  <div class="stage-track" :aria-label="`${tt('当前状态：')}${order['状态']}`">
                    <i
                      v-for="stageIndex in 4"
                      :key="stageIndex"
                      :class="{ active: stageIndex <= orderStage(order['状态']) }"
                    ></i>
                  </div>
                  <span class="order-status" :class="statusTone(order['状态'])">{{ tt(order['状态']) }}</span>
                </div>
                <div v-if="!progress.length" class="empty compact-empty">{{ tt('暂无工单执行数据') }}</div>
              </div>
            </div>
          </div>
        </section>

        <section v-if="desk.showTodo" class="card quality-watch col-4 reveal-item">
          <div class="panel-heading">
            <div>
              <div class="card-title">{{ tt('质量与待办监测') }}</div>
              <p>{{ tt('检验结果和流程阻塞') }}</p>
            </div>
            <span class="risk-level" :class="qualityRisk.tone">{{ tt(qualityRisk.label) }}</span>
          </div>

          <div class="quality-summary">
            <div class="quality-ring">
              <svg viewBox="0 0 80 80" aria-hidden="true">
                <circle class="quality-track" cx="40" cy="40" r="32" />
                <circle
                  class="quality-value"
                  cx="40"
                  cy="40"
                  r="32"
                  :class="qualityRisk.tone"
                  :style="{ strokeDashoffset: qualityGaugeOffset }"
                />
              </svg>
              <div>
                <strong>{{ quality.total ? `${qualityRate}%` : '--' }}</strong>
                <span>{{ tt('检验合格率') }}</span>
              </div>
            </div>
            <div class="quality-facts">
              <div><span>{{ tt('检验明细') }}</span><strong>{{ quality.total || 0 }}</strong></div>
              <div><span>{{ tt('非合格 / 待判') }}</span><strong class="danger-text">{{ qualityExceptions }}</strong></div>
              <div><span>{{ tt('流程待办') }}</span><strong>{{ todos.length }}</strong></div>
            </div>
          </div>

          <div class="result-strip">
            <div v-for="result in qualityResults" :key="result.name">
              <span><i :class="result.tone"></i>{{ tt(result.name) }}</span>
              <strong>{{ result.value }}</strong>
            </div>
          </div>

          <div class="watch-message" :class="{ clear: !todos.length }">
            <el-icon><component :is="todos.length ? 'Warning' : 'CircleCheck'" /></el-icon>
            <span>{{ todos.length ? `${todos.length} ${tt('项流程等待处理')}` : tt('当前没有审批流程阻塞') }}</span>
          </div>
        </section>

        <section class="card event-stream col-7 reveal-item">
          <div class="panel-heading">
            <div>
              <div class="card-title">{{ tt('实时业务事件流') }}</div>
              <p>{{ tt('来自 SQL 业务单据的最新活动') }}</p>
            </div>
            <span class="event-count">{{ latest.length }} {{ tt('条事件') }}</span>
          </div>
          <div class="event-list">
            <div v-for="(event, index) in latest.slice(0, 6)" :key="`${event['编号']}-${index}`" class="event-row">
              <div class="event-axis"><i :class="{ pulse: index === 0 }"></i></div>
              <div class="event-icon"><el-icon><component :is="eventIcon(event.panel)" /></el-icon></div>
              <div class="event-copy">
                <strong>{{ tt(panelTitle(event.panel)) }}</strong>
                <span>{{ event['编号'] }}</span>
              </div>
              <span class="event-status" :class="statusTone(event['状态'])">{{ tt(event['状态']) }}</span>
              <time>{{ compactTime(event['时间']) }}</time>
            </div>
            <div v-if="!latest.length" class="empty">{{ tt('暂无业务事件') }}</div>
          </div>
        </section>

        <section class="card business-vitals col-5 reveal-item">
          <div class="panel-heading">
            <div>
              <div class="card-title">{{ tt('业务数据内核') }}</div>
              <p>{{ tt('单据流量与基础资源覆盖') }}</p>
            </div>
            <el-tooltip :content="tt('数据每 5 分钟自动同步')" placement="top">
              <el-button class="sync-button" text circle :loading="loadingStats" @click="load">
                <el-icon><Refresh /></el-icon>
              </el-button>
            </el-tooltip>
          </div>

          <div class="doc-volume">
            <div v-for="doc in docStats" :key="doc.panelCode" class="doc-row">
              <span>{{ tt(doc.panelName) }}</span>
              <div><i :style="{ width: `${docPercent(doc.count)}%` }"></i></div>
              <strong>{{ doc.count }}</strong>
            </div>
          </div>

          <div class="resource-grid">
            <div v-for="resource in resourceStats" :key="resource.label">
              <el-icon><component :is="resource.icon" /></el-icon>
              <strong>{{ resource.value }}</strong>
              <span>{{ tt(resource.label) }}</span>
            </div>
          </div>

          <div class="data-link">
            <span><i :class="{ error: loadError }"></i>{{ loadError ? tt('数据同步异常') : tt('SQL 数据链路在线') }}</span>
            <span>{{ loadingStats ? tt('同步中') : `${refreshLeft}s ${tt('后刷新')}` }}</span>
          </div>
        </section>
      </div>
    </template>

    <!-- ===== 生产 ===== -->
    <template v-else-if="mod === 'prod'">
      <div class="dash-grid">
        <div class="card col-4">
          <div class="card-title">{{ tt('工单状态分布') }}</div>
          <div class="chart-box"><SBars :data="prod.statusDist" /></div>
        </div>
        <div class="card col-4">
          <div class="card-title">{{ tt('车间生产分布') }}</div>
          <div class="chart-box"><SDonut :data="prod.workshopDist" :sub="tt('加工单')" /></div>
        </div>
        <div class="card col-4">
          <div class="card-title">{{ tt('近 7 天新增 / 完工') }}</div>
          <div class="chart-box"><SLine :data="prod.trend7" /></div>
        </div>
        <div class="card col-12">
          <div class="card-title">{{ tt('BOM 物料树（产品 → 材料，来自生产加工单真实数据）') }}</div>
          <div class="chart-box tree-box"><STree :data="bomTree" /></div>
        </div>
      </div>
    </template>

    <!-- ===== 库存 ===== -->
    <template v-else-if="mod === 'stock'">
      <div class="dash-grid">
        <div class="card metric-card col-3"><div class="kpi-num tone-primary">{{ stock.totalIn }}</div><div class="kpi-title">{{ tt('入库单量') }}</div></div>
        <div class="card metric-card col-3"><div class="kpi-num tone-warning">{{ stock.totalOut }}</div><div class="kpi-title">{{ tt('出库单量') }}</div></div>
        <div class="card metric-card col-3"><div class="kpi-num tone-steel">{{ stockCount }}</div><div class="kpi-title">{{ tt('出入库单据总数') }}</div></div>
        <div class="card metric-card col-3"><div class="kpi-num tone-neutral">{{ stock.totalLines }}</div><div class="kpi-title">{{ tt('明细行数合计') }}</div></div>
        <div class="card col-6">
          <div class="card-title">{{ tt('各单据数量') }}</div>
          <div class="chart-box"><SBars :data="stockPanels" /></div>
        </div>
        <div class="card col-6">
          <div class="card-title">{{ tt('各单据明细行数') }}</div>
          <div class="chart-box"><SBars :data="stockLines" :colors="['#537786', '#116a5b', '#d79a2b', '#7a8b84', '#3b8978', '#9c7650']" /></div>
        </div>
      </div>
    </template>

    <!-- ===== 销售 ===== -->
    <template v-else-if="mod === 'sales'">
      <div class="dash-grid">
        <div class="card metric-card col-4"><div class="kpi-num tone-primary">{{ sales.total }}</div><div class="kpi-title">{{ tt('销售订单数') }}</div></div>
        <div class="card metric-card col-4"><div class="kpi-num tone-warning">{{ sales.amount ?? 0 }}</div><div class="kpi-title">{{ tt('明细金额合计（元）') }}</div></div>
        <div class="card metric-card col-4"><div class="kpi-num tone-steel">{{ salesDone }}</div><div class="kpi-title">{{ tt('已审核订单') }}</div></div>
        <div class="card col-6">
          <div class="card-title">{{ tt('客户订单分布') }}</div>
          <div class="chart-box"><SDonut :data="sales.byCustomer" :sub="tt('订单')" /></div>
        </div>
        <div class="card col-6">
          <div class="card-title">{{ tt('订单状态分布') }}</div>
          <div class="chart-box"><SBars :data="sales.byStatus" /></div>
        </div>
      </div>
    </template>

    <!-- ===== 质量 ===== -->
    <template v-else>
      <div class="dash-grid">
        <div class="card metric-card col-3"><div class="kpi-num tone-steel">{{ quality.total }}</div><div class="kpi-title">{{ tt('检验明细总数') }}</div></div>
        <div class="card metric-card col-3"><div class="kpi-num tone-primary">{{ quality.pass }}</div><div class="kpi-title">{{ tt('合格数') }}</div></div>
        <div class="card metric-card col-3"><div class="kpi-num tone-danger">{{ quality.total - quality.pass }}</div><div class="kpi-title">{{ tt('非合格数') }}</div></div>
        <div class="card metric-card col-3"><div class="kpi-num tone-warning">{{ quality.passRate }}%</div><div class="kpi-title">{{ tt('合格率') }}</div></div>
        <div class="card col-6">
          <div class="card-title">{{ tt('检验结果分布') }}</div>
          <div class="chart-box"><SDonut :data="quality.byResult" :sub="tt('明细')" /></div>
        </div>
        <div class="card col-6">
          <div class="card-title">{{ tt('检验结果对比') }}</div>
          <div class="chart-box"><SBars :data="quality.byResult" /></div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { useTabsStore } from '@/stores/tabs'
import { useAppStore } from '@/stores/app'
import { tt } from '@/i18n'
import { useLocaleStore } from '@/stores/locale'
import request from '@core/request'
import { ElNotification } from 'element-plus'
import SBars from './SBars.vue'
import SDonut from './SDonut.vue'
import SLine from './SLine.vue'
import STree from './STree.vue'

const user = useUserStore()
const tabs = useTabsStore()
const app = useAppStore()
const localeStore = useLocaleStore()
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
        title: `${tt('已切换至')}${tt(shift.value.name)}`,
        message: `${tt('当前班次时段：')}${tt(shift.value.range)}`,
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
const loadingStats = ref(false)
const loadError = ref(false)
const lastUpdated = ref(null)
const refreshLeft = ref(300)
async function load() {
  if (loadingStats.value) return
  loadingStats.value = true
  try {
    const r = await request.get('/dashboard/stats')
    if (r?.data) {
      stats.value = { ...stats.value, ...r.data }
      lastUpdated.value = new Date()
      refreshLeft.value = 300
      loadError.value = false
    }
  } catch (e) {
    loadError.value = true
  } finally {
    loadingStats.value = false
  }
}
let refreshTimer = null
let countdownTimer = null

onMounted(() => {
  load()
  refreshTimer = setInterval(load, 300000)
  countdownTimer = setInterval(() => {
    if (!loadingStats.value && refreshLeft.value > 0) refreshLeft.value -= 1
  }, 1000)
  startShiftTimer()
})
onBeforeUnmount(() => {
  clearInterval(refreshTimer)
  clearInterval(countdownTimer)
  clearInterval(shiftTimer)
})

// ---------- 概览数据 ----------
const todos = computed(() => stats.value.todos || [])
const docStats = computed(() => stats.value.docStats || [])
const archives = computed(() => stats.value.archives || {})
const latest = computed(() => stats.value.latest || [])
const progress = computed(() => stats.value.progress || [])

// ---------- 生产数据 ----------
const prod = computed(() => stats.value.production || {})
const bomTree = computed(() =>
  (prod.value.bomTree || []).map((p) => ({
    label: p['产品'],
    meta: p['规格型号'] ? `${tt('规格：')}${p['规格型号']}` : '',
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

// ---------- 实时驾驶舱派生指标 ----------
const productionTotal = computed(() => Number(stats.value.kpis.moTotal || 0))
const productionDone = computed(() => {
  const item = (prod.value.statusDist || []).find((entry) => entry.name === '已完工')
  return Number(item?.value || 0)
})
const productionRunning = computed(() => {
  const item = (prod.value.statusDist || []).find((entry) => entry.name === '生产中')
  return Number(item?.value || 0)
})
const completionRate = computed(() => (
  productionTotal.value ? Math.round((productionDone.value / productionTotal.value) * 100) : 0
))
const gaugeOffset = computed(() => 301.6 * (1 - completionRate.value / 100))
const qualityRate = computed(() => Number(quality.value.passRate || 0))
const qualityGaugeOffset = computed(() => 201.1 * (1 - Math.min(100, qualityRate.value) / 100))
const qualityExceptions = computed(() => Math.max(0, Number(quality.value.total || 0) - Number(quality.value.pass || 0)))
const qualityRisk = computed(() => {
  if (!quality.value.total) return { label: '暂无检验', tone: 'neutral' }
  if (qualityRate.value >= 95) return { label: '质量稳定', tone: 'stable' }
  if (qualityRate.value >= 80) return { label: '需要关注', tone: 'attention' }
  return { label: '质量风险', tone: 'risk' }
})
const qualityResults = computed(() => {
  const tones = { 合格: 'stable', 不合格: 'risk', 让步接收: 'attention', 待检: 'neutral' }
  return (quality.value.byResult || []).map((item) => ({ ...item, tone: tones[item.name] || 'neutral' }))
})
const resourceStats = computed(() => [
  { label: '存货', value: archives.value.invItems ?? 0, icon: 'Grid' },
  { label: '部门', value: archives.value.deptCount ?? 0, icon: 'OfficeBuilding' },
  { label: '仓库', value: archives.value.whCount ?? 0, icon: 'House' },
  { label: 'BOM 产品', value: bomTree.value.length, icon: 'Connection' },
])
const lastUpdatedText = computed(() => (
  lastUpdated.value ? lastUpdated.value.toLocaleTimeString('zh-CN', { hour12: false }).slice(0, 8) : '等待同步'
))
const kpis = computed(() => {
  const trend = prod.value.trend7 || []
  const amount = Number(sales.value.amount || 0)
  return [
    {
      title: tt('生产任务负载'), value: Number(stats.value.kpis.moActive || 0), unit: tt('单'), icon: 'Odometer',
      tone: 'primary', state: tt('实时'), meta: `${tt('总计')} ${productionTotal.value} ${tt('单')}`, signal: `${productionRunning.value} ${tt('单生产中')}`,
      bars: trend.map((item) => Number(item.added || 0)),
    },
    {
      title: tt('工单闭环率'), value: completionRate.value, unit: '%', icon: 'CircleCheck',
      tone: 'steel', state: completionRate.value >= 80 ? tt('稳定') : tt('推进中'),
      meta: `${productionDone.value} / ${productionTotal.value} ${tt('已完工')}`, signal: tt('生命周期'),
      bars: trend.map((item) => Number(item.done || 0)),
    },
    {
      title: tt('检验合格率'), value: quality.value.total ? qualityRate.value : '--', unit: quality.value.total ? '%' : '', icon: 'Aim',
      tone: qualityRate.value < 80 && quality.value.total ? 'danger' : 'warning', state: tt(qualityRisk.value.label),
      meta: `${quality.value.pass || 0} / ${quality.value.total || 0} ${tt('合格')}`, signal: `${qualityExceptions.value} ${tt('项待处理')}`,
      bars: (quality.value.byResult || []).map((item) => Number(item.value || 0)),
    },
    {
      title: tt('销售订单金额'), value: formatCompact(amount), unit: tt('元'), icon: 'TrendCharts',
      tone: 'neutral', state: `${sales.value.total || 0} ${tt('张订单')}`, meta: `${salesDone.value} ${tt('张已审核')}`, signal: tt('业务流入'),
      bars: (sales.value.byCustomer || []).map((item) => Number(item.value || 0)),
    },
  ]
})

// ---------- 工具 ----------
function statusTone(status) {
  return {
    已完工: 'stable', 已审核: 'stable', 启用: 'stable', 生产中: 'running',
    审批中: 'attention', 待审批: 'attention', 草稿: 'neutral', 不合格: 'risk',
    已中止: 'risk', 已关闭: 'risk',
  }[status] || 'neutral'
}
function orderStage(status) {
  return { 草稿: 1, 已审核: 2, 生产中: 3, 已完工: 4, 已关闭: 4 }[status] || 1
}
function eventIcon(panel) {
  const name = String(panel || '')
  if (name.includes('生产') || name.includes('加工')) return 'SetUp'
  if (name.includes('销售') || name.includes('订单')) return 'ShoppingCart'
  if (name.includes('检验') || name.includes('质量')) return 'Aim'
  if (name.includes('入库') || name.includes('出库')) return 'Box'
  return 'Document'
}
const PANEL_TITLES = {
  PURCHASE_IN: '采购入库单', QUOTE_ORDER: '报价单', CUSTOMER_TRACE_SETTINGS: '客户追溯设置',
  TRACE_PRINT_TEMPLATE: '追溯打印模板', COMPANY_TRACE_SETTINGS: '企业追溯设置', QC_ITEM: '质检项目',
  MANU_ORDER: '生产加工单', SO_ORDER: '销售订单', PROCESS_REPORT: '工序汇报单',
  FINISH_IN: '产成品入库单', SALE_OUT: '销售出库单', DEPT: '部门档案',
}
function panelTitle(panel) {
  return PANEL_TITLES[panel] || panel || '业务单据'
}
function compactTime(value) {
  const text = String(value || '')
  return text.length >= 16 ? text.slice(5, 16).replace('T', ' ') : text || '--'
}
function formatCompact(value) {
  if (!Number.isFinite(value)) return '0'
  if (Math.abs(value) >= 10000) return `${(value / 10000).toFixed(value >= 100000 ? 0 : 1)}${tt('万')}`
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 0 }).format(value)
}
function barHeight(values, value) {
  const max = Math.max(...(values || []).map(Number), 1)
  return Math.max(16, Math.round((Number(value || 0) / max) * 100))
}
function docPercent(value) {
  const max = Math.max(...docStats.value.map((item) => Number(item.count || 0)), 1)
  return value ? Math.max(8, Math.round((Number(value) / max) * 100)) : 0
}
function go(path, title) {
  router.push(path)
  tabs.open({ path, title })
}

// ---------- 界面语言：切换后补齐本页词条（tt() 在渲染层读 locale ref，切换即时重算） ----------
const DICT_KEYS = [
  '凌晨好', '上午好', '下午好', '晚上好', '白班', '夜班', '21:00 - 次日 08:00',
  ...QUICK_DEFS.map((q) => q.title),
  ...MODULES.map((m) => m.title),
  ...Object.values(PANEL_TITLES), '业务单据',
  '生产执行核心', '工单生命周期与近 7 天执行脉冲', '等待同步', '工单闭环率', '总工单', '执行中', '已完工',
  '新增 / 完工趋势', '近 7 天', '未指定产品', '当前状态：', '暂无工单执行数据',
  '质量与待办监测', '检验结果和流程阻塞', '暂无检验', '质量稳定', '需要关注', '质量风险',
  '检验合格率', '检验明细', '非合格 / 待判', '流程待办', '项流程等待处理', '当前没有审批流程阻塞',
  '实时业务事件流', '来自 SQL 业务单据的最新活动', '条事件', '暂无业务事件',
  '业务数据内核', '单据流量与基础资源覆盖', '数据每 5 分钟自动同步', '数据同步异常', 'SQL 数据链路在线', '同步中', '后刷新',
  '工单状态分布', '车间生产分布', '加工单', '近 7 天新增 / 完工', 'BOM 物料树（产品 → 材料，来自生产加工单真实数据）',
  '入库单量', '出库单量', '出入库单据总数', '明细行数合计', '各单据数量', '各单据明细行数',
  '销售订单数', '明细金额合计（元）', '已审核订单', '客户订单分布', '订单', '订单状态分布',
  '检验明细总数', '合格数', '非合格数', '合格率', '检验结果分布', '明细', '检验结果对比',
  '生产任务负载', '单', '实时', '总计', '单生产中', '稳定', '推进中', '生命周期', '合格', '项待处理',
  '张订单', '张已审核', '业务流入', '元', '万', '规格：', '已切换至', '当前班次时段：',
]
watch([() => localeStore.current, docStats], async ([locale]) => {
  if (localeStore.isZh) return
  await localeStore.ensureDict(locale, [
    ...DICT_KEYS,
    ...resourceStats.value.map((r) => r.label),
    ...docStats.value.map((d) => d.panelName),
  ].flatMap((k) => String(k || '').split(' / ')))
}, { immediate: true })
</script>

<style scoped>
.card {
  background: var(--t-card-bg);
  border: 1px solid var(--t-border);
  border-radius: 6px;
  padding: 15px 16px;
  box-shadow: 0 1px 2px rgba(20, 43, 36, 0.035);
  min-width: 0;
}

.reveal-item {
  animation: panel-rise 0.42s ease both;
  animation-delay: var(--reveal-delay, 0ms);
}

@keyframes panel-rise {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}

/* 实时指标 */
.live-metric {
  position: relative;
  min-height: 128px;
  overflow: hidden;
  padding: 13px 15px 12px;
}

.live-metric::before {
  position: absolute;
  top: 0;
  right: 0;
  left: 0;
  height: 3px;
  background: var(--metric-color);
  content: '';
}

.metric-primary { --metric-color: #116a5b; --metric-soft: #e7f2ef; }
.metric-steel { --metric-color: #537786; --metric-soft: #eaf0f2; }
.metric-warning { --metric-color: #b87816; --metric-soft: #fff3dc; }
.metric-danger { --metric-color: #b94d3f; --metric-soft: #faece9; }
.metric-neutral { --metric-color: #68785c; --metric-soft: #eff2ec; }

.metric-head,
.metric-foot,
.metric-main {
  display: flex;
  align-items: center;
}

.metric-head {
  justify-content: space-between;
  gap: 12px;
}

.metric-label {
  color: var(--t-text-2);
  font-size: 12px;
  font-weight: 620;
}

.metric-state {
  max-width: 92px;
  overflow: hidden;
  color: var(--metric-color);
  font-size: 10px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.metric-main {
  min-height: 57px;
  gap: 10px;
}

.metric-icon {
  width: 34px;
  height: 34px;
  display: grid;
  flex: 0 0 auto;
  place-items: center;
  border-radius: 5px;
  background: var(--metric-soft);
  color: var(--metric-color);
  font-size: 18px;
}

.metric-reading {
  min-width: 70px;
  display: flex;
  align-items: baseline;
  gap: 4px;
  color: var(--t-text-1);
}

.metric-reading strong {
  font-size: 26px;
  font-weight: 750;
  line-height: 1;
}

.metric-reading span {
  color: var(--t-text-3);
  font-size: 11px;
}

.micro-bars {
  height: 32px;
  min-width: 58px;
  display: flex;
  flex: 1;
  align-items: flex-end;
  justify-content: flex-end;
  gap: 3px;
  overflow: hidden;
}

.micro-bars i {
  width: 5px;
  max-height: 100%;
  display: block;
  border-radius: 1px 1px 0 0;
  background: var(--metric-color);
  opacity: 0.72;
  transform-origin: bottom;
  animation: bar-grow 0.52s ease both;
}

.micro-bars i:nth-child(2n) { opacity: 0.38; }

@keyframes bar-grow {
  from { transform: scaleY(0); }
  to { transform: scaleY(1); }
}

.metric-foot {
  justify-content: space-between;
  gap: 8px;
  padding-top: 9px;
  border-top: 1px solid var(--t-border-light);
  color: var(--t-text-3);
  font-size: 10px;
  white-space: nowrap;
}

.metric-signal {
  overflow: hidden;
  text-overflow: ellipsis;
}

.metric-signal i {
  width: 5px;
  height: 5px;
  display: inline-block;
  margin-right: 5px;
  border-radius: 50%;
  background: var(--metric-color);
  vertical-align: 1px;
}

/* 面板通用标题 */
.panel-heading {
  min-height: 38px;
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}

.panel-heading .card-title {
  margin-bottom: 3px;
}

.panel-heading p {
  margin: 0;
  padding-left: 10px;
  color: var(--t-text-3);
  font-size: 11px;
}

.live-chip,
.risk-level,
.event-count {
  flex: 0 0 auto;
  color: var(--t-text-3);
  font-size: 10px;
  white-space: nowrap;
}

.live-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding-top: 3px;
  color: var(--t-primary);
  font-weight: 650;
}

.live-chip i,
.data-link i {
  width: 7px;
  height: 7px;
  display: inline-block;
  border-radius: 50%;
  background: #3e9d74;
  box-shadow: 0 0 0 4px rgba(62, 157, 116, 0.12);
  animation: live-pulse 1.8s ease-out infinite;
}

@keyframes live-pulse {
  0%, 45% { box-shadow: 0 0 0 0 rgba(62, 157, 116, 0.28); }
  100% { box-shadow: 0 0 0 7px rgba(62, 157, 116, 0); }
}

/* 生产执行核心 */
.operation-core,
.quality-watch {
  min-height: 326px;
}

.core-layout {
  min-height: 250px;
  display: grid;
  grid-template-columns: 190px minmax(0, 1fr);
  gap: 20px;
}

.execution-gauge {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding-right: 18px;
  border-right: 1px solid var(--t-border-light);
}

.execution-gauge svg {
  width: 132px;
  height: 132px;
  transform: rotate(-90deg);
}

.gauge-visual {
  position: relative;
  width: 132px;
  height: 132px;
}

.execution-gauge circle,
.quality-ring circle {
  fill: none;
  stroke-width: 8;
}

.gauge-track {
  stroke: var(--t-border-light);
}

.gauge-value {
  stroke: #116a5b;
  stroke-dasharray: 301.6;
  stroke-linecap: square;
  transition: stroke-dashoffset 0.7s ease;
}

.gauge-copy {
  position: absolute;
  inset: 0;
  display: grid;
  align-content: center;
  text-align: center;
}

.gauge-copy strong,
.gauge-copy span {
  display: block;
}

.gauge-copy strong {
  color: var(--t-text-1);
  font-size: 26px;
  line-height: 1.1;
}

.gauge-copy span {
  margin-top: 4px;
  color: var(--t-text-3);
  font-size: 10px;
}

.gauge-facts {
  width: 100%;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  margin-top: 13px;
}

.gauge-facts span {
  color: var(--t-text-3);
  font-size: 9px;
  text-align: center;
}

.gauge-facts span + span {
  border-left: 1px solid var(--t-border-light);
}

.gauge-facts b {
  display: block;
  margin-bottom: 2px;
  color: var(--t-text-1);
  font-size: 14px;
}

.execution-detail {
  min-width: 0;
}

.trend-head {
  display: flex;
  justify-content: space-between;
  color: var(--t-text-3);
  font-size: 10px;
}

.trend-head span:first-child {
  color: var(--t-text-2);
  font-weight: 620;
}

.order-queue {
  margin-top: 5px;
  border-top: 1px solid var(--t-border-light);
}

.order-row {
  min-height: 43px;
  display: grid;
  grid-template-columns: minmax(130px, 1.2fr) minmax(80px, 1fr) 62px;
  align-items: center;
  gap: 12px;
  border-bottom: 1px solid var(--t-border-light);
}

.order-copy {
  min-width: 0;
}

.order-copy strong,
.order-copy span {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.order-copy strong {
  color: var(--t-text-1);
  font-size: 11px;
}

.order-copy span {
  margin-top: 2px;
  color: var(--t-text-3);
  font-size: 9px;
}

.stage-track {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 3px;
}

.stage-track i {
  height: 4px;
  background: var(--t-border);
}

.stage-track i.active {
  background: #3e8978;
}

.order-status,
.event-status {
  min-width: 50px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  justify-self: end;
  padding: 3px 5px;
  border-radius: 3px;
  background: var(--t-content-bg);
  color: var(--t-text-2);
  font-size: 9px;
  white-space: nowrap;
}

.order-status.stable,
.event-status.stable { background: #e5f1ed; color: #116a5b; }
.order-status.running,
.event-status.running { background: #e9f0f2; color: #476f7e; }
.order-status.attention,
.event-status.attention { background: #fff2d9; color: #a2670e; }
.order-status.risk,
.event-status.risk { background: #fae9e6; color: #a94236; }

/* 质量监测 */
.risk-level {
  padding: 4px 7px;
  border: 1px solid var(--t-border);
  border-radius: 3px;
}

.risk-level.stable { border-color: #acd2c6; color: #116a5b; }
.risk-level.attention { border-color: #e7c78b; color: #9a620d; }
.risk-level.risk { border-color: #e6b0a9; color: #a94236; }

.quality-summary {
  min-height: 116px;
  display: grid;
  grid-template-columns: 118px minmax(0, 1fr);
  align-items: center;
  gap: 12px;
}

.quality-ring {
  position: relative;
  width: 108px;
  height: 108px;
}

.quality-ring svg {
  width: 100%;
  height: 100%;
  transform: rotate(-90deg);
}

.quality-track { stroke: var(--t-border-light); }
.quality-value {
  stroke: #116a5b;
  stroke-dasharray: 201.1;
  stroke-linecap: square;
  transition: stroke-dashoffset 0.7s ease;
}
.quality-value.attention { stroke: #b87816; }
.quality-value.risk { stroke: #b94d3f; }
.quality-value.neutral { stroke: #87958f; }

.quality-ring > div {
  position: absolute;
  inset: 0;
  display: grid;
  align-content: center;
  text-align: center;
}

.quality-ring strong,
.quality-ring span {
  display: block;
}

.quality-ring strong {
  color: var(--t-text-1);
  font-size: 21px;
}

.quality-ring span {
  margin-top: 2px;
  color: var(--t-text-3);
  font-size: 9px;
}

.quality-facts {
  display: grid;
  gap: 7px;
}

.quality-facts > div {
  min-height: 28px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--t-border-light);
}

.quality-facts span {
  color: var(--t-text-3);
  font-size: 10px;
}

.quality-facts strong {
  color: var(--t-text-1);
  font-size: 15px;
}

.danger-text { color: #b94d3f !important; }

.result-strip {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 1px;
  margin-top: 8px;
  background: var(--t-border-light);
  border: 1px solid var(--t-border-light);
}

.result-strip > div {
  min-height: 31px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 0 8px;
  background: var(--t-card-bg);
}

.result-strip span {
  color: var(--t-text-2);
  font-size: 9px;
}

.result-strip i {
  width: 6px;
  height: 6px;
  display: inline-block;
  margin-right: 5px;
  border-radius: 50%;
  background: #87958f;
}

.result-strip i.stable { background: #3e9d74; }
.result-strip i.attention { background: #d89a2b; }
.result-strip i.risk { background: #c15345; }

.watch-message {
  min-height: 32px;
  display: flex;
  align-items: center;
  gap: 7px;
  margin-top: 10px;
  padding: 7px 9px;
  border-left: 3px solid #d89a2b;
  background: #fff8e8;
  color: #8b611f;
  font-size: 10px;
}

.watch-message.clear {
  border-left-color: #3e9d74;
  background: #edf6f3;
  color: #2b745f;
}

/* 实时事件 */
.event-stream,
.business-vitals {
  min-height: 294px;
}

.event-count {
  padding-top: 4px;
}

.event-list {
  margin-left: 5px;
}

.event-row {
  min-height: 37px;
  display: grid;
  grid-template-columns: 14px 28px minmax(120px, 1fr) 58px 78px;
  align-items: center;
  gap: 9px;
}

.event-axis {
  position: relative;
  align-self: stretch;
}

.event-axis::after {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 6px;
  width: 1px;
  background: var(--t-border);
  content: '';
}

.event-axis i {
  position: absolute;
  z-index: 1;
  top: 15px;
  left: 3px;
  width: 7px;
  height: 7px;
  border: 2px solid var(--t-card-bg);
  border-radius: 50%;
  background: #87958f;
  box-shadow: 0 0 0 1px var(--t-border);
}

.event-axis i.pulse {
  background: #3e9d74;
  animation: event-pulse 1.8s ease-out infinite;
}

@keyframes event-pulse {
  0%, 45% { box-shadow: 0 0 0 1px #3e9d74; }
  100% { box-shadow: 0 0 0 7px rgba(62, 157, 116, 0); }
}

.event-icon {
  width: 27px;
  height: 27px;
  display: grid;
  place-items: center;
  border: 1px solid var(--t-border-light);
  border-radius: 4px;
  background: var(--t-content-bg);
  color: var(--t-primary);
  font-size: 14px;
}

.event-copy {
  min-width: 0;
}

.event-copy strong,
.event-copy span {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.event-copy strong {
  color: var(--t-text-1);
  font-size: 10px;
  font-weight: 620;
}

.event-copy span {
  margin-top: 1px;
  color: var(--t-text-3);
  font-size: 9px;
}

.event-row time {
  color: var(--t-text-3);
  font-size: 9px;
  text-align: right;
  white-space: nowrap;
}

/* SQL 数据内核 */
.sync-button {
  color: var(--t-text-3);
}

.doc-volume {
  display: grid;
  gap: 8px;
}

.doc-row {
  display: grid;
  grid-template-columns: 82px minmax(70px, 1fr) 24px;
  align-items: center;
  gap: 8px;
}

.doc-row > span {
  overflow: hidden;
  color: var(--t-text-2);
  font-size: 9px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.doc-row > div {
  height: 5px;
  overflow: hidden;
  background: var(--t-border-light);
}

.doc-row i {
  height: 100%;
  display: block;
  background: #3e8978;
  transition: width 0.5s ease;
}

.doc-row strong {
  color: var(--t-text-1);
  font-size: 10px;
  text-align: right;
}

.resource-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 6px;
  margin-top: 17px;
  padding-top: 14px;
  border-top: 1px solid var(--t-border-light);
}

.resource-grid > div {
  min-width: 0;
  text-align: center;
}

.resource-grid .el-icon {
  color: var(--t-primary);
  font-size: 15px;
}

.resource-grid strong,
.resource-grid span {
  display: block;
}

.resource-grid strong {
  margin-top: 3px;
  color: var(--t-text-1);
  font-size: 15px;
}

.resource-grid span {
  margin-top: 1px;
  overflow: hidden;
  color: var(--t-text-3);
  font-size: 9px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.data-link {
  min-height: 31px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-top: 13px;
  padding-top: 10px;
  border-top: 1px solid var(--t-border-light);
  color: var(--t-text-3);
  font-size: 9px;
}

.data-link span:first-child {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--t-text-2);
}

.data-link i {
  width: 6px;
  height: 6px;
  animation: none;
  box-shadow: none;
}

.data-link i.error {
  background: #b94d3f;
}
.welcome {
  position: relative;
  display: flex;
  justify-content: space-between;
  align-items: center;
  min-height: 82px;
  margin-bottom: 14px;
  padding: 16px 20px;
  overflow: hidden;
  border: 0;
  border-left: 4px solid #e0a83a;
  background: #173f38;
  color: #fff;
  box-shadow: 0 5px 15px rgba(19, 61, 53, 0.13);
}
.hello {
  font-size: 19px;
  font-weight: 680;
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
  border-radius: 4px;
  font-size: 12px;
  font-weight: 600;
  background: rgba(255, 255, 255, 0.18);
}
.shift-badge.day {
  background: rgba(232, 177, 68, 0.22);
}
.shift-badge.night {
  background: rgba(111, 145, 155, 0.3);
}
.shift-badge em {
  font-style: normal;
  font-weight: 400;
  opacity: 0.85;
}
.meta-sep {
  opacity: 0.5;
}
.quick {
  display: flex;
  gap: 8px;
}
.quick .el-button {
  margin-left: 0;
  border-color: rgba(255, 255, 255, 0.24);
  background: rgba(255, 255, 255, 0.94);
  color: #29453e;
}
.quick .el-button:hover {
  border-color: #ffffff;
  background: #ffffff;
  color: var(--t-primary);
}
.quick .el-button--primary {
  border-color: #e0a83a;
  background: #e0a83a;
  color: #1f302b;
}
.quick .el-button--primary:hover {
  border-color: #edba54;
  background: #edba54;
  color: #1f302b;
}

/* 模块切换 */
.mod-tabs {
  width: max-content;
  display: flex;
  gap: 2px;
  margin-bottom: 14px;
  padding: 3px;
  overflow-x: auto;
  border: 1px solid var(--t-border);
  border-radius: 6px;
  background: var(--t-card-bg);
  -webkit-overflow-scrolling: touch;
}
.mod-tab {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-height: 32px;
  padding: 6px 15px;
  font-size: 13px;
  color: var(--t-text-2);
  background: transparent;
  border: 0;
  border-radius: 4px;
  cursor: pointer;
  white-space: nowrap;
  flex-shrink: 0;
}
.mod-tab.on {
  color: var(--t-primary);
  background: var(--t-hover-bg);
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
.col-5 { grid-column: span 5; }
.col-6 { grid-column: span 6; }
.col-7 { grid-column: span 7; }
.col-8 { grid-column: span 8; }
.col-12 { grid-column: span 12; }
@media (max-width: 768px) {
  .col-3, .col-4, .col-5, .col-6, .col-7, .col-8, .col-12 { grid-column: span 12; }
}

.card-title {
  position: relative;
  padding-left: 10px;
  font-weight: 650;
  margin-bottom: 12px;
  font-size: 14px;
  color: var(--t-text-1);
}
.card-title::before {
  position: absolute;
  top: 3px;
  bottom: 3px;
  left: 0;
  width: 3px;
  background: var(--t-primary);
  content: '';
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
.kpi-title {
  font-size: 12px;
  color: var(--t-text-3);
  margin-top: 1px;
}
.kpi-num {
  font-size: 26px;
  font-weight: 700;
}
.tone-primary { color: #116a5b; }
.tone-warning { color: #b87816; }
.tone-steel { color: #537786; }
.tone-neutral { color: #68785c; }
.tone-danger { color: #b94d3f; }
.empty {
  color: var(--t-text-3);
  font-size: 12px;
  padding: 16px 2px;
}
.dashboard :deep(.el-table) {
  --el-table-header-bg-color: var(--t-sidebar-bg);
  --el-table-row-hover-bg-color: var(--t-hover-bg);
  --el-table-border-color: var(--t-border-light);
}

@media (max-width: 768px) {
  .welcome {
    align-items: flex-start;
    flex-direction: column;
    gap: 14px;
    padding: 15px 16px;
  }

  .quick {
    width: 100%;
    overflow-x: auto;
  }

  .quick .el-button {
    flex: 0 0 auto;
  }

  .mod-tabs {
    width: 100%;
  }

  .live-metric {
    min-height: 118px;
  }

  .operation-core,
  .quality-watch,
  .event-stream,
  .business-vitals {
    min-height: 0;
  }

  .core-layout {
    display: block;
  }

  .execution-gauge {
    display: grid;
    grid-template-columns: 132px minmax(0, 1fr);
    padding: 0 0 14px;
    border-right: 0;
    border-bottom: 1px solid var(--t-border-light);
  }

  .gauge-facts {
    width: auto;
    grid-template-columns: 1fr;
    align-self: center;
    gap: 7px;
    margin: 0 0 0 16px;
  }

  .gauge-facts span {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    padding-bottom: 5px;
    border-bottom: 1px solid var(--t-border-light);
    text-align: left;
  }

  .gauge-facts span + span {
    border-left: 0;
  }

  .gauge-facts b {
    margin: 0 8px 0 0;
  }

  .execution-detail {
    margin-top: 14px;
  }

  .order-row {
    grid-template-columns: minmax(115px, 1.2fr) minmax(70px, 1fr) 56px;
    gap: 8px;
  }

  .event-row {
    grid-template-columns: 14px 28px minmax(100px, 1fr) 56px;
  }

  .event-row time {
    display: none;
  }

  .doc-row {
    grid-template-columns: 90px minmax(70px, 1fr) 24px;
  }
}

@media (min-width: 769px) and (max-width: 1100px) {
  .live-metric.col-3 {
    grid-column: span 6;
  }

  .operation-core,
  .quality-watch,
  .event-stream,
  .business-vitals {
    grid-column: span 12;
  }
}

@media (prefers-reduced-motion: reduce) {
  .reveal-item,
  .micro-bars i,
  .live-chip i,
  .event-axis i.pulse {
    animation: none;
  }

  .gauge-value,
  .quality-value,
  .doc-row i {
    transition: none;
  }
}
</style>
