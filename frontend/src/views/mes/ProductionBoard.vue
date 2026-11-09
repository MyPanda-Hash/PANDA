<!-- ProductionBoard.vue — 生产进度看板
     数据源：MANU_ORDER_TRACKER（生产加工单跟踪工具，按「加工单 × 工序」出数）。
     用途：车间/计划员一眼看出哪张单卡在哪道工序、整体完成多少、还差多少没开工。 -->
<template>
  <div class="pb-page">
    <header class="pb-head">
      <div>
        <h2>生产进度看板</h2>
        <span class="pb-sub">按加工单 × 工序汇总已审核报工量；数据时间 {{ updatedAt }}</span>
      </div>
      <div class="pb-actions">
        <el-radio-group v-model="scope" size="small">
          <el-radio-button label="active">在制</el-radio-button>
          <el-radio-button label="all">全部</el-radio-button>
        </el-radio-group>
        <el-button size="small" @click="load">刷新</el-button>
      </div>
    </header>

    <!-- 指标卡：不套装饰渐变、不加阴影，白底 + 1px 边框 -->
    <section class="pb-kpis">
      <div class="kpi">
        <span class="kpi-label">加工单</span>
        <b class="kpi-value">{{ kpi.orders }}</b>
        <span class="kpi-hint">在制 {{ kpi.activeOrders }}</span>
      </div>
      <div class="kpi">
        <span class="kpi-label">工序数</span>
        <b class="kpi-value">{{ kpi.processes }}</b>
        <span class="kpi-hint">进行中 {{ kpi.running }}</span>
      </div>
      <div class="kpi">
        <span class="kpi-label">已完工工序</span>
        <b class="kpi-value">{{ kpi.done }}</b>
        <span class="kpi-hint">未开工 {{ kpi.notStarted }}</span>
      </div>
      <div class="kpi">
        <span class="kpi-label">整体完成率</span>
        <b class="kpi-value">{{ kpi.rate }}%</b>
        <span class="kpi-hint">按报工数量 / 计划数量</span>
      </div>
    </section>

    <div class="pb-bar">
      <el-select v-model="workshop" size="small" clearable placeholder="全部车间" style="width: 160px">
        <el-option v-for="w in workshops" :key="w" :label="w" :value="w" />
      </el-select>
      <el-radio-group v-model="status" size="small">
        <el-radio-button label="">全部状态</el-radio-button>
        <el-radio-button label="未开工">未开工</el-radio-button>
        <el-radio-button label="进行中">进行中</el-radio-button>
        <el-radio-button label="完工">完工</el-radio-button>
      </el-radio-group>
      <el-input v-model="keyword" size="small" clearable style="width: 240px" placeholder="搜索加工单号 / 产品 / 工序" />
    </div>

    <el-table
      :data="filtered"
      v-loading="loading"
      size="small"
      border
      height="calc(100vh - 340px)"
      :row-class-name="rowClass"
      @row-click="openOrder"
    >
      <el-table-column label="加工单号" prop="单据编号" width="150" />
      <el-table-column label="产品" width="200" show-overflow-tooltip>
        <template #default="{ row }">{{ row['产品名称'] || row['产品编码'] || '—' }}</template>
      </el-table-column>
      <el-table-column label="工序" width="110">
        <template #default="{ row }">{{ row['工序名称'] || row['工序编码'] }}</template>
      </el-table-column>
      <el-table-column label="车间" prop="生产车间" width="110" />
      <el-table-column label="计划" width="80" align="right">
        <template #default="{ row }">{{ qty(row['计划数量']) }}</template>
      </el-table-column>
      <el-table-column label="已报工" width="90" align="right">
        <template #default="{ row }">{{ qty(row['报工数量']) }}</template>
      </el-table-column>
      <el-table-column label="进度" min-width="200">
        <template #default="{ row }">
          <div class="pb-progress">
            <div class="track"><i :class="barClass(row)" :style="{ width: pct(row) + '%' }"></i></div>
            <span class="pct">{{ pct(row) }}%</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100" align="center">
        <template #default="{ row }">
          <el-tag size="small" :type="tagType(row)">{{ row['工序完工状态'] }}</el-tag>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import { usePanelRuntime } from '@core/panel-runtime'
import { useTabsStore } from '@/stores/tabs'

const engine = usePanelRuntime()
const router = useRouter()
const tabs = useTabsStore()

const rows = ref([])
const loading = ref(false)
const scope = ref('all')
const status = ref('')
const workshop = ref('')
const keyword = ref('')
const updatedAt = ref('—')

const workshops = computed(() => [...new Set(rows.value.map((r) => r['生产车间']).filter(Boolean))])

const filtered = computed(() => {
  let list = rows.value
  if (scope.value === 'active') {
    // 在制 = 还没有全部工序完工的加工单；已全部完工的整单不占版面
    const byOrder = new Map()
    for (const r of list) {
      if (!byOrder.has(r['单据编号'])) byOrder.set(r['单据编号'], [])
      byOrder.get(r['单据编号']).push(r)
    }
    const finished = new Set(
      [...byOrder.entries()]
        .filter(([, rs]) => rs.every((r) => r['工序完工状态'] === '完工'))
        .map(([no]) => no)
    )
    list = list.filter((r) => !finished.has(r['单据编号']))
  }
  if (status.value) list = list.filter((r) => r['工序完工状态'] === status.value)
  if (workshop.value) list = list.filter((r) => r['生产车间'] === workshop.value)
  const kw = keyword.value.trim()
  if (kw) {
    list = list.filter((r) =>
      [r['单据编号'], r['产品名称'], r['工序名称'], r['产品编码']].some((v) => String(v || '').includes(kw))
    )
  }
  return list
})

const kpi = computed(() => {
  const list = filtered.value
  const orders = new Set(list.map((r) => r['单据编号']))
  const activeOrders = new Set(list.filter((r) => r['工序完工状态'] !== '完工').map((r) => r['单据编号']))
  const done = list.filter((r) => r['工序完工状态'] === '完工').length
  const running = list.filter((r) => r['工序完工状态'] === '进行中').length
  const notStarted = list.filter((r) => r['工序完工状态'] === '未开工').length
  const plan = list.reduce((s, r) => s + Number(r['计划数量'] || 0), 0)
  const reported = list.reduce((s, r) => s + Number(r['报工数量'] || 0), 0)
  const rate = plan > 0 ? Math.round((reported / plan) * 1000) / 10 : 0
  return { orders: orders.size, activeOrders: activeOrders.size, processes: list.length, done, running, notStarted, rate }
})

function pct(row) {
  const plan = Number(row['计划数量'] || 0)
  const rep = Number(row['报工数量'] || 0)
  if (plan <= 0) return 0
  return Math.min(100, Math.round((rep / plan) * 1000) / 10)
}
function qty(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return '0'
  return n === Math.round(n) ? String(n) : n.toFixed(2)
}
function tagType(row) {
  const s = row['工序完工状态']
  return s === '完工' ? 'success' : s === '进行中' ? 'warning' : 'info'
}
function barClass(row) {
  const p = pct(row)
  return p >= 100 ? 'fill done' : p > 0 ? 'fill running' : 'fill idle'
}
function rowClass({ row }) {
  return row['工序完工状态'] === '完工' ? 'pb-row-done' : ''
}
function openOrder(row) {
  const no = row['单据编号']
  if (!no) return
  const path = '/panelx/list/MANU_ORDER'
  const query = { code: no }
  router.push({ path, query })
  tabs.open({ path, title: `生产加工单-${no}`, query })
}

async function load() {
  loading.value = true
  try {
    const res = await engine.queryFormDataList({
      panelCode: 'MANU_ORDER_TRACKER',
      pageNo: 1,
      pageSize: 500,
    })
    let list = res?.list || []
    list = [...list].sort((a, b) => {
      const noA = String(a['单据编号'] || '')
      const noB = String(b['单据编号'] || '')
      if (noA !== noB) return noB.localeCompare(noA)
      return String(a['工序编码'] || '').localeCompare(String(b['工序编码'] || ''))
    })
    rows.value = list
    const d = new Date()
    updatedAt.value = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '加载生产进度失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.pb-page {
  padding: 12px 16px;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #fff;
}
.pb-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  padding-bottom: 10px;
  border-bottom: 1px solid #f0f0f0;
}
.pb-head h2 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: #1f1f1f;
}
.pb-sub {
  display: block;
  margin-top: 2px;
  font-size: 12px;
  color: #8c8c8c;
}
.pb-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}
.pb-kpis {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin: 12px 0;
}
.kpi {
  border: 1px solid #e8e8e8;
  border-radius: 6px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  background: #fff;
}
.kpi-label {
  font-size: 12px;
  color: #8c8c8c;
}
.kpi-value {
  font-size: 22px;
  line-height: 30px;
  font-weight: 600;
  color: #1f1f1f;
  font-variant-numeric: tabular-nums;
}
.kpi-hint {
  font-size: 12px;
  color: #8c8c8c;
}
.pb-bar {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 10px;
}
.pb-progress {
  display: flex;
  align-items: center;
  gap: 8px;
}
.track {
  flex: 1;
  height: 6px;
  background: #f0f0f0;
  border-radius: 3px;
  overflow: hidden;
}
.fill {
  display: block;
  height: 100%;
  border-radius: 3px;
}
.fill.done {
  background: #52c41a;
}
.fill.running {
  background: #1677ff;
}
.fill.idle {
  background: #d9d9d9;
}
.pct {
  width: 46px;
  text-align: right;
  font-size: 12px;
  color: #595959;
  font-variant-numeric: tabular-nums;
}
:deep(.pb-row-done td) {
  color: #8c8c8c;
}
</style>