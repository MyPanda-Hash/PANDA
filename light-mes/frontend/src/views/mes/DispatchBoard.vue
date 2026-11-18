<!-- DispatchBoard.vue — 排产批次看板（Kanban）
     定位：只读监控，不做业务操作。
     三列口径：未开工 = 还没排产的工序（加工单的可排产量）；生产中 = 已排产、还没报满的批次；
     完工 = 首检通过并报满的批次。
     数据源：/px/pendingPlanBoard（待排工序）+ /px/reportBatchBoard（排产批次）。
     报工在「工序报工」工作台按批次报；材料出库从领料申请单生成。
     视觉：容器挂 .adx，取全局设计库语义层（--d-*），主色走品牌绿。 -->
<template>
  <div class="adx db">
    <header class="db-head">
      <div class="db-title">
        <h2>排产批次看板</h2>
        <span class="db-sub">
          未排产的工序在「未开工」，排产即进「生产中」，报满转「完工」<span class="db-dot">·</span>数据时间 {{ updatedAt }}
        </span>
      </div>
      <div class="db-tools">
        <el-select v-model="moNo" clearable filterable placeholder="全部生产加工单" class="f-mo">
          <el-option v-for="mo in mos" :key="mo.no" :label="`${mo.no} · ${mo.product}`" :value="mo.no">
            <span class="opt-no">{{ mo.no }}</span>
            <span class="opt-prod">
              {{ mo.product }}（{{ mo.done }}/{{ mo.total }} 批完工<template v-if="mo.pending">，{{ mo.pending }} 道待排</template>）
            </span>
          </el-option>
        </el-select>
        <el-select v-model="workshop" clearable placeholder="全部车间" class="f-shop">
          <el-option v-for="w in workshops" :key="w" :label="w" :value="w" />
        </el-select>
        <el-input v-model="keyword" clearable placeholder="搜索批次 / 加工单 / 产品 / 工序" class="f-kw" />
        <el-button :loading="loading" @click="load">刷新</el-button>
        <el-button type="primary" @click="goReport">去报工</el-button>
      </div>
    </header>

    <section class="db-metrics">
      <div class="metric">
        <span class="m-label">生产中批次</span>
        <span class="m-line"><b class="m-value">{{ kpi.producing }}</b><i class="m-unit">批</i></span>
        <span class="m-hint">已排产 {{ kpi.total }} 个批次</span>
      </div>
      <div class="metric">
        <span class="m-label">已完工批次</span>
        <span class="m-line"><b class="m-value">{{ kpi.done }}</b><i class="m-unit">批</i></span>
        <span class="m-hint">占已排产 {{ kpi.doneRate }}%</span>
      </div>
      <div class="metric">
        <span class="m-label">待排工序</span>
        <span class="m-line"><b class="m-value">{{ kpi.pendingCount }}</b><i class="m-unit">道</i></span>
        <span class="m-hint">可排 {{ fmt(kpi.pendingQty) }} 件</span>
      </div>
      <div class="metric">
        <span class="m-label">报工完成率</span>
        <span class="m-line"><b class="m-value">{{ kpi.rate }}</b><i class="m-unit">%</i></span>
        <span class="m-bar"><i :style="{ width: kpi.rate + '%' }" /></span>
      </div>
    </section>

    <div class="db-board" v-loading="loading">
      <section v-for="col in columns" :key="col.key" class="lane" :class="'lane-' + col.key">
        <header class="lane-head">
          <i class="lane-dot" />
          <span class="lane-title">{{ col.title }}</span>
          <span class="lane-count">{{ colCounts[col.key] }}</span>
          <span v-if="colQty[col.key] > 0" class="lane-qty">
            {{ col.key === 'todo' ? '可排' : '待报' }} {{ fmt(colQty[col.key]) }}
          </span>
        </header>

        <div class="lane-body">
          <template v-for="group in grouped[col.key]" :key="group.mo">
            <div class="grp-head">
              <span class="grp-no">{{ group.mo }}</span>
              <span class="grp-prod">{{ group.product }}</span>
              <span class="grp-n">{{ group.items.length }} {{ col.key === 'todo' ? '道' : '批' }}</span>
            </div>
            <article
              v-for="(row, i) in group.items"
              :key="row.pending ? `${row['加工单号']}|${row['工序编码']}|${i}` : row['排产单号']"
              class="card"
              :class="{ 'is-pending': row.pending }"
            >
              <div class="card-head">
                <span class="card-no">{{ row.pending ? '待排产' : row['排产单号'] }}</span>
                <span v-if="row.pending && row['工序顺序']" class="card-no">第 {{ row['工序顺序'] }} 道</span>
                <span v-if="flagOf(row).tone === 'warn'" class="card-flag is-warn" :title="row['不可报工原因']">
                  {{ flagOf(row).text }}
                </span>
                <span v-else class="card-flag" :class="'is-' + flagOf(row).tone">{{ flagOf(row).text }}</span>
              </div>
              <div class="card-title">
                <span class="t-prod">{{ row['产品名称'] || '—' }}</span>
                <span class="t-sep">·</span>
                <span class="t-proc">{{ row['工序名称'] }}</span>
              </div>
              <div class="card-meta">
                <span>{{ row['生产车间'] || '未指定车间' }}</span>
                <span v-if="row['班组']" class="meta-i">{{ row['班组'] }}</span>
                <span v-if="row['设备']" class="meta-i">{{ row['设备'] }}</span>
              </div>
              <template v-if="row.pending">
                <div class="card-qty">
                  <span class="q-now">可排 {{ fmt(row['批次数量']) }}</span>
                  <span class="q-unit">{{ row['工序单位'] || '件' }}</span>
                  <span class="q-rest">计划 {{ fmt(row['计划数量']) }}</span>
                </div>
              </template>
              <template v-else>
                <div class="card-qty">
                  <span class="q-now">{{ fmt(row['已报数量']) }}</span>
                  <span class="q-sep">/</span>
                  <span class="q-all">{{ fmt(row['批次数量']) }}</span>
                  <span class="q-unit">{{ row['工序单位'] || '件' }}</span>
                  <span class="q-rest" :class="{ 'is-zero': num(row['剩余数量']) <= 0 }">
                    剩 {{ fmt(row['剩余数量']) }}
                  </span>
                </div>
                <div class="q-bar"><i :style="{ width: percentOf(row) + '%' }" /></div>
              </template>
            </article>
          </template>

          <div v-if="!grouped[col.key].length" class="lane-empty">
            <p>{{ col.empty }}</p>
            <el-button v-if="col.action === 'report' && kpi.canReport > 0" link type="primary" @click="goReport">
              去报工 {{ kpi.canReport }} 个可报批次
            </el-button>
            <el-button v-else-if="col.action === 'plan'" link type="primary" @click="goPlan">去工单排产</el-button>
          </div>
        </div>
      </section>
    </div>

    <footer class="db-foot">
      只读看板：未排产的工序去「工单排产」排批次，报工在「工序报工」工作台按批次报；派工单不需要审批。
    </footer>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import { usePanelRuntime } from '@core/panel-runtime'

const engine = usePanelRuntime()
const router = useRouter()

const rows = ref([])
const pending = ref([])
const loading = ref(false)
const moNo = ref('')
const workshop = ref('')
const keyword = ref('')
const updatedAt = ref('—')

const columns = [
  { key: 'todo', title: '未开工', empty: '没有待排产的工序：都已排成批次了。', action: 'plan' },
  { key: 'doing', title: '生产中', empty: '没有在制批次：已排产的批次要么完工了，要么还没排产。', action: 'report' },
  { key: 'done', title: '完工', empty: '还没有首检通过并报满的批次。', action: '' },
]

function num(v) {
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}

function fmt(v) {
  const n = num(v)
  return Number.isInteger(n) ? String(n) : n.toFixed(2).replace(/\.?0+$/, '')
}

function percentOf(row) {
  const planned = num(row['批次数量'])
  if (planned <= 0) return 0
  return Math.min(100, Math.round((num(row['已报数量']) / planned) * 100))
}

// 卡片右上角状态词：可报工（成功）/ 已报满（中性）/ 待首检·等前置（警告，带原因）/ 待排产（中性）
function flagOf(row) {
  if (row.pending) return { text: '待排产', tone: 'mute' }
  const reason = String(row['不可报工原因'] || '')
  if (row['可否报工'] !== false) return { text: '可报工', tone: 'ok' }
  if (reason === '批次已报满') return { text: '已报满', tone: 'mute' }
  if (row['首检是否通过'] === false) {
    return { text: String(row['首检状态']) === '不合格' ? '首检不合格' : '待首检', tone: 'warn' }
  }
  return { text: reason || '不可报工', tone: 'warn' }
}

// 未排产的工序套成和批次一样的行形状，卡片模板只需要多一个 pending 分支
function pendingRow(p) {
  const qty = num(p['可排产量'])
  return {
    pending: true,
    '排产单号': '',
    '加工单号': p['加工单号'],
    '产品名称': p['产品名称'],
    '工序编码': p['工序编码'],
    '工序名称': p['工序名称'],
    '工序顺序': p['工序顺序'],
    '生产车间': p['生产车间'],
    '班组': '',
    '设备': '',
    '工序单位': p['工序单位'] || '件',
    '批次数量': qty,
    '已报数量': 0,
    '剩余数量': qty,
    '计划数量': num(p['计划数量']),
    '可否报工': false,
    '不可报工原因': '',
  }
}

const workshops = computed(() => [...new Set(
  [...rows.value, ...pending.value].map((r) => r['生产车间']).filter(Boolean),
)])

// 生产加工单清单：带产品名与完工批次比，用于筛选器展示（待排工序的加工单也要在里面）
const mos = computed(() => {
  const map = new Map()
  const touch = (no, product) => {
    if (!no) return null
    if (!map.has(no)) map.set(no, { no, product: product || '', total: 0, done: 0, pending: 0 })
    return map.get(no)
  }
  for (const r of rows.value) {
    const g = touch(r['加工单号'], r['产品名称'])
    if (!g) continue
    g.total += 1
    if (num(r['剩余数量']) <= 0) g.done += 1
  }
  for (const p of pending.value) {
    const g = touch(p['加工单号'], p['产品名称'])
    if (g) g.pending += 1
  }
  return [...map.values()].sort((a, b) => String(b.no).localeCompare(String(a.no)))
})

const matches = (r) => {
  if (moNo.value && r['加工单号'] !== moNo.value) return false
  if (workshop.value && r['生产车间'] !== workshop.value) return false
  const kw = keyword.value.trim().toLowerCase()
  if (!kw) return true
  return ['排产单号', '加工单号', '产品名称', '工序名称']
    .some((k) => String(r[k] || '').toLowerCase().includes(kw))
}

// 列 → 加工单分组：同一张加工单的批次/待排工序挨在一起
const grouped = computed(() => {
  const todo = pending.value.filter(matches).map(pendingRow)
  const doing = []
  const done = []
  for (const r of rows.value) {
    if (!matches(r)) continue
    if (num(r['剩余数量']) <= 0) done.push(r)
    else doing.push(r)
  }
  const result = {}
  for (const [key, list] of Object.entries({ todo, doing, done })) {
    const byMo = new Map()
    for (const r of list) {
      const mo = r['加工单号'] || '—'
      if (!byMo.has(mo)) byMo.set(mo, { mo, product: r['产品名称'] || '', items: [] })
      byMo.get(mo).items.push(r)
    }
    for (const g of byMo.values()) {
      // 待排工序按工序顺序排，批次按批次号排（≈ 工序顺序）
      g.items.sort((a, b) => (a.pending && b.pending)
        ? num(a['工序顺序']) - num(b['工序顺序'])
        : String(a['排产单号'] || '').localeCompare(String(b['排产单号'] || '')))
    }
    result[key] = [...byMo.values()].sort((a, b) => String(b.mo).localeCompare(String(a.mo)))
  }
  return result
})

const colCounts = computed(() => {
  const out = {}
  for (const col of columns) out[col.key] = grouped.value[col.key].reduce((n, g) => n + g.items.length, 0)
  return out
})

// 各列数量合计：未开工=可排产量，生产中=待报量，完工=已报总量
const colQty = computed(() => ({
  todo: grouped.value.todo.reduce((n, g) => n + g.items.reduce((m, r) => m + num(r['剩余数量']), 0), 0),
  doing: grouped.value.doing.reduce((n, g) => n + g.items.reduce((m, r) => m + num(r['剩余数量']), 0), 0),
  done: 0,
}))

const kpi = computed(() => {
  const batches = rows.value
  const done = batches.filter((r) => num(r['剩余数量']) <= 0).length
  const producing = batches.length - done
  let planned = 0
  let reported = 0
  for (const r of batches) {
    planned += num(r['批次数量'])
    reported += num(r['已报数量'])
  }
  const rate = planned > 0 ? Math.round((reported / planned) * 100) : 0
  const doneRate = batches.length > 0 ? Math.round((done / batches.length) * 100) : 0
  const pendingQty = pending.value.reduce((n, r) => n + num(r['可排产量']), 0)
  return {
    total: batches.length, done, producing, doneRate, rate,
    pendingCount: pending.value.length, pendingQty,
    canReport: batches.filter((r) => r['可否报工'] !== false).length,
  }
})

function goReport() {
  router.push('/mes/processReport')
}

function goPlan() {
  router.push('/mes/plan')
}

async function load() {
  loading.value = true
  try {
    const [batches, unplanned] = await Promise.all([
      engine.reportBatchBoard(),
      engine.pendingPlanBoard(),
    ])
    rows.value = batches || []
    pending.value = unplanned || []
    const d = new Date()
    updatedAt.value = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '加载排产批次失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
/* 设计变量来自全局 .adx（src/styles/index.css）：中性 --d-text* / --d-border*，
   语义 --d-ok / --d-warn / --d-bad，主色 --d-primary（品牌绿 #116a5b）。 */
.db {
  height: 100%;
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 16px;
  background: var(--d-card);
  border: 1px solid var(--d-border);
  border-radius: var(--d-radius);
  min-height: 0;
}

/* ===== 页头：标题 + 筛选（控件高 32，与设计库 controlHeight 一致） ===== */
.db-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.db-title h2 {
  margin: 0;
  font-size: 16px;
  line-height: 24px;
  font-weight: 600;
  color: var(--d-text);
}
.db-sub {
  display: block;
  margin-top: 2px;
  font-size: 12px;
  line-height: 20px;
  color: var(--d-text-3);
}
.db-dot {
  margin: 0 4px;
  color: var(--d-border-strong);
}
.db-tools {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}
/* 筛选控件定宽：选择器写到 .db-tools 下，避免与全局 .el-select 宽度规则比源码顺序 */
.db-tools .f-mo { width: 220px; }
.db-tools .f-shop { width: 132px; }
.db-tools .f-kw { width: 200px; }
/* 下拉项被 teleport 到 body，不在 .adx 作用域内 → 这里用挂在 :root 的全局 token */
.opt-no {
  margin-right: 8px;
  color: var(--t-text-1);
  font-variant-numeric: tabular-nums;
}
.opt-prod {
  color: var(--t-text-3);
  font-size: 12px;
}

/* ===== 指标条：一个容器四格，靠 1px 分隔线分层（不堆四个卡片） ===== */
.db-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  border: 1px solid var(--d-border);
  border-radius: var(--d-radius);
  background: var(--d-card);
}
.metric {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 12px 16px;
  min-width: 0;
}
.metric + .metric {
  border-left: 1px solid var(--d-border);
}
.m-label {
  font-size: 12px;
  line-height: 18px;
  color: var(--d-text-3);
}
.m-line {
  display: flex;
  align-items: baseline;
  gap: 4px;
  font-variant-numeric: tabular-nums;
}
.m-value {
  font-size: 24px;
  line-height: 32px;
  font-weight: 600;
  color: var(--d-text);
}
.m-unit {
  font-style: normal;
  font-size: 12px;
  color: var(--d-text-3);
}
.m-hint {
  font-size: 12px;
  line-height: 18px;
  color: var(--d-text-3);
}
.m-bar {
  display: block;
  height: 4px;
  margin-top: 4px;
  background: var(--d-fill);
  border-radius: 4px;
  overflow: hidden;
}
.m-bar i {
  display: block;
  height: 100%;
  background: var(--d-primary);
  border-radius: 4px;
}

/* ===== 三列看板 ===== */
.db-board {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
}
.lane {
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: var(--d-fill-soft);
  border-radius: var(--d-radius);
}
.lane-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-bottom: 1px solid var(--d-border);
}
.lane-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--d-border-strong);
}
.lane-doing .lane-dot { background: var(--d-primary); }
.lane-done .lane-dot { background: var(--d-ok); }
.lane-title {
  font-size: 13px;
  line-height: 20px;
  font-weight: 600;
  color: var(--d-text);
}
.lane-count {
  min-width: 20px;
  padding: 0 6px;
  font-size: 11px;
  line-height: 18px;
  text-align: center;
  color: var(--d-text-2);
  background: var(--d-fill);
  border-radius: 8px;
  font-variant-numeric: tabular-nums;
}
.lane-qty {
  margin-left: auto;
  font-size: 12px;
  color: var(--d-text-3);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.lane-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* 加工单分组标题：滚动时吸顶，长列里始终知道自己在看哪张加工单 */
.grp-head {
  position: sticky;
  top: -8px;
  z-index: 1;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 2px 4px;
  background: var(--d-fill-soft);
  font-size: 12px;
  line-height: 18px;
}
.grp-no {
  color: var(--d-text);
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
.grp-prod {
  color: var(--d-text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.grp-n {
  margin-left: auto;
  color: var(--d-text-3);
  white-space: nowrap;
}

/* ===== 批次卡片：白底 + 1px 边框，0 层不加阴影 ===== */
.card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 12px;
  background: var(--d-card);
  border: 1px solid var(--d-border);
  border-radius: var(--d-radius-sm);
  transition: border-color 0.15s;
}
.card:hover {
  border-color: var(--d-border-strong);
}
/* 待排产的工序：还不是批次，用浅底和虚线边框区分（语义区分，不是装饰） */
.card.is-pending {
  background: var(--d-fill-soft);
  border-style: dashed;
}
.card-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.card-no {
  font-size: 12px;
  line-height: 18px;
  color: var(--d-text-3);
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.2px;
}
.card-flag {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  line-height: 18px;
  color: var(--d-text-3);
  white-space: nowrap;
}
.card-flag::before {
  content: '';
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
}
.card-flag.is-ok { color: var(--d-ok); }
.card-flag.is-warn {
  padding: 0 6px;
  color: var(--d-warn);
  background: var(--d-warn-bg);
  border-radius: 4px;
  max-width: 60%;
  overflow: hidden;
  text-overflow: ellipsis;
}
.card-flag.is-warn::before { display: none; }

.card-title {
  font-size: 14px;
  line-height: 20px;
  font-weight: 500;
  color: var(--d-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.t-sep {
  margin: 0 4px;
  color: var(--d-border-strong);
  font-weight: 400;
}
.t-proc {
  font-weight: 400;
  color: var(--d-text-2);
}
.card-meta {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  line-height: 16px;
  color: var(--d-text-3);
  overflow: hidden;
  white-space: nowrap;
}
.meta-i::before {
  content: '·';
  margin-right: 4px;
  color: var(--d-border-strong);
}

.card-qty {
  display: flex;
  align-items: baseline;
  gap: 3px;
  font-variant-numeric: tabular-nums;
}
.q-now {
  font-size: 15px;
  line-height: 20px;
  font-weight: 600;
  color: var(--d-text);
}
.q-sep { color: var(--d-border-strong); }
.q-all {
  font-size: 13px;
  color: var(--d-text-2);
}
.q-unit {
  font-size: 11px;
  color: var(--d-text-3);
}
.q-rest {
  margin-left: auto;
  font-size: 12px;
  color: var(--d-text-2);
  white-space: nowrap;
}
.q-rest.is-zero { color: var(--d-text-3); }

.q-bar {
  height: 4px;
  background: var(--d-fill);
  border-radius: 4px;
  overflow: hidden;
}
.q-bar i {
  display: block;
  height: 100%;
  background: var(--d-primary);
  border-radius: 4px;
}
.lane-done .q-bar i { background: var(--d-ok); }
.lane-todo .q-bar i { background: var(--d-border-strong); }

/* ===== 空态：说明为什么空 + 一个可执行动作 ===== */
.lane-empty {
  margin: auto;
  padding: 24px 16px;
  text-align: center;
  max-width: 260px;
}
.lane-empty p {
  margin: 0;
  font-size: 12px;
  line-height: 20px;
  color: var(--d-text-3);
}

.db-foot {
  font-size: 12px;
  line-height: 20px;
  color: var(--d-text-3);
  padding-top: 0;
}

/* ===== 响应式：1440 / 768 / 375 三档无横向滚动 ===== */
@media (max-width: 1200px) {
  .db-tools { width: 100%; justify-content: flex-start; }
  .db-tools .f-mo { width: 240px; }
}
@media (max-width: 1100px) {
  .db { height: auto; min-height: 100%; }
  .db-board { grid-template-columns: minmax(0, 1fr); }
  .lane { min-height: 200px; }
  .lane-body { max-height: 420px; }
}
@media (max-width: 768px) {
  .db { padding: 12px; gap: 12px; }
  .db-tools .el-select,
  .db-tools .el-input { width: 100% !important; }
  .db-tools .el-button { flex: 1 1 auto; }
  .db-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .metric { padding: 10px 12px; }
  .metric:nth-child(odd) { border-left: 0; }
  .metric:nth-child(n + 3) { border-top: 1px solid var(--d-border); }
  .lane-body { max-height: none; }
}
@media (max-width: 480px) {
  .db-metrics { grid-template-columns: minmax(0, 1fr); }
  .metric:nth-child(n + 2) {
    border-left: 0;
    border-top: 1px solid var(--d-border);
  }
  .m-value { font-size: 20px; line-height: 28px; }
}
</style>
