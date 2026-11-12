<!-- ProcessReportBoard.vue — 工序报工工作台
     一行一个排产批次，按生产加工单分组（同一张加工单的工序挨在一起，一眼看出做到第几道）。
     放行规则 B：本道有剩余量、且前一道已有报工即可开工，允许工序重叠生产；
     报工只生成「只含该工序一行」的工序汇报单，避免整单被某道已报满的工序卡住。 -->
<template>
  <div class="adx rp-page">
    <header class="rp-head">
      <div>
        <h2>工序报工</h2>
        <span class="rp-sub">按排产批次报工：先排产才有批次，累计报工不超过批次数量；首件未检时只放首件量</span>
      </div>
      <div class="rp-actions">
        <el-radio-group v-model="scope">
          <el-radio-button label="todo">只看可报工</el-radio-button>
          <el-radio-button label="all">全部批次</el-radio-button>
        </el-radio-group>
        <el-button :loading="loading" @click="load">刷新</el-button>
      </div>
    </header>

    <section class="rp-kpis">
      <div class="kpi"><span class="kpi-label">可报工序</span><b class="kpi-value">{{ kpi.todo }}</b><span class="kpi-hint">共 {{ kpi.total }} 个批次</span></div>
      <div class="kpi"><span class="kpi-label">涉及加工单</span><b class="kpi-value">{{ kpi.orders }}</b><span class="kpi-hint">在制 {{ kpi.runningOrders }}</span></div>
      <div class="kpi"><span class="kpi-label">已报满</span><b class="kpi-value">{{ kpi.done }}</b><span class="kpi-hint">占全部 {{ kpi.doneRate }}%</span></div>
      <div class="kpi"><span class="kpi-label">等待前置</span><b class="kpi-value">{{ kpi.blocked }}</b><span class="kpi-hint">前道尚未报工</span></div>
    </section>

    <div class="rp-bar">
      <el-select v-model="moNo" clearable filterable placeholder="全部生产加工单" style="width: 260px">
        <el-option v-for="mo in mos" :key="mo.no" :label="`${mo.no} · ${mo.product}`" :value="mo.no">
          <span class="opt-no">{{ mo.no }}</span>
          <span class="opt-prod">{{ mo.product }}（{{ mo.done }}/{{ mo.total }} 批完工）</span>
        </el-option>
      </el-select>
      <el-select v-model="workshop" clearable placeholder="全部车间" style="width: 140px">
        <el-option v-for="w in workshops" :key="w" :label="w" :value="w" />
      </el-select>
      <el-input v-model="keyword" clearable style="width: 240px" placeholder="搜索加工单号 / 产品 / 工序" />
      <span class="rp-tip">一行一个批次；同一加工单按工序顺序排列，报满一批才轮到下一道。</span>
    </div>

    <div class="rp-list" v-loading="loading">
      <!-- 一个生产加工单一个线框框区，桌面一行两个，多的往下排 -->
      <section v-for="group in groups" :key="group.mo" class="mo">
        <header class="mo-head">
          <span class="mo-no">{{ group.mo }}</span>
          <span class="mo-prod">{{ group.product }}</span>
          <span class="mo-meta">{{ group.items.length }} 批 · 完工 {{ group.done }} 批</span>
          <span class="mo-bar"><i :style="{ width: group.percent + '%' }" /></span>
          <span class="mo-pct">{{ group.percent }}%</span>
        </header>

        <div class="mo-rows">
          <div v-for="row in group.items" :key="row['排产单号']" class="br">
            <span class="br-no">{{ row['排产单号'] }}</span>
            <span class="br-proc">{{ row['工序名称'] || row['工序编码'] }}</span>
            <span class="br-shop" :title="row['生产车间'] || ''">
              {{ [row['生产车间'], row['班组'], row['设备']].filter(Boolean).join(' · ') || '未指定车间' }}
            </span>
            <span class="br-qty">
              <b>{{ qty(row['已报数量']) }}</b><span class="sep">/</span>{{ qty(row['批次数量']) }}<i>{{ row['工序单位'] || '件' }}</i>
            </span>
            <span class="br-rest" :class="{ zero: num(row['剩余数量']) <= 0 }">待报 {{ qty(row['剩余数量']) }}</span>
            <span class="br-state">
              <el-tooltip v-if="stateOf(row).tone === 'warn'" :content="row['不可报工原因'] || '不可报工'" placement="top">
                <span class="st is-warn">{{ stateOf(row).text }}</span>
              </el-tooltip>
              <span v-else class="st" :class="'is-' + stateOf(row).tone">{{ stateOf(row).text }}</span>
            </span>
            <span class="br-op">
              <el-button v-if="row['可否报工']" size="small" type="primary" plain @click="openReport(row)">报工</el-button>
              <el-button v-else-if="num(row['剩余数量']) > 0" size="small" @click="openReport(row)">去首检</el-button>
              <span v-else class="dim">—</span>
            </span>
          </div>
        </div>
      </section>

      <div v-if="!groups.length && !loading" class="rp-empty">
        <p>{{ scope === 'todo' ? '当前没有可以报工的批次：先排产，或等前道工序报满。' : '没有匹配的排产批次。' }}</p>
        <el-button link type="primary" @click="scope = scope === 'todo' ? 'all' : 'todo'">
          {{ scope === 'todo' ? '查看全部批次' : '只看可报工' }}
        </el-button>
      </div>
    </div>

    <footer class="rp-foot">
      报工按排产批次提交，立即生成工序汇报单（免审批，落库即已审核）；累计报工不超过批次数量。
    </footer>

    <!-- 报工弹窗：首检先过关，再填数量；首检没建单/没审/不合格都只给「去首检」 -->
    <el-dialog v-model="dialogVisible" :title="`报工 · ${form.工序名称 || ''}`" width="520px">
      <div class="d-meta">
        <div class="d-row">
          <span class="d-k">排产批次</span>
          <span class="d-v num">{{ form.排产单号 }}</span>
        </div>
        <div class="d-row">
          <span class="d-k">加工单</span>
          <span class="d-v num">{{ form.加工单号 }}</span>
        </div>
        <div class="d-row">
          <span class="d-k">车间 / 设备</span>
          <span class="d-v">{{ [form.生产车间, form.班组, form.设备].filter(Boolean).join(' · ') || '—' }}</span>
        </div>
        <div class="d-row">
          <span class="d-k">批次 / 已报</span>
          <span class="d-v num">{{ qty(form.批次数量) }} / {{ qty(form.已报数量) }} {{ unit }}</span>
        </div>
      </div>

      <section class="d-inspect" :class="'is-' + inspectTone">
        <header class="di-head">
          <span class="di-title">首件检验</span>
          <span class="st" :class="'is-' + inspectTone">{{ inspectToneText }}</span>
          <span v-if="inspectNo" class="di-no">{{ inspectNo }}</span>
        </header>
        <p class="di-text">{{ inspectText }}</p>
        <div class="di-ops">
          <el-button
            v-if="!inspectPassed && !inspectNo"
            type="primary"
            :loading="generating"
            @click="createFirstInspect"
          >
            生成首件报检单
          </el-button>
          <el-button v-if="inspectNo" type="primary" plain @click="goInspect">
            去首件检验
          </el-button>
        </div>
      </section>

      <template v-if="inspectPassed">
        <div class="d-qty">
          <label class="dq-item">
            <span class="dq-label">合格数量</span>
            <el-input-number v-model="form.合格数量" :min="0" :max="Number(form.剩余数量)" :step="1" controls-position="right" />
          </label>
          <label class="dq-item">
            <span class="dq-label">不合格数量</span>
            <el-input-number v-model="form.不合格数量" :min="0" :max="Number(form.剩余数量)" :step="1" controls-position="right" />
          </label>
        </div>
        <div class="d-sum" :class="{ 'is-over': reportTotal > Number(form.剩余数量) }">
          <span>本次报工 <b>{{ qty(reportTotal) }}</b> {{ unit }}</span>
          <span class="d-sum-sep">·</span>
          <span>剩余可报 {{ qty(form.剩余数量) }} {{ unit }}</span>
          <span v-if="reportTotal > Number(form.剩余数量)" class="d-sum-warn">超出剩余</span>
        </div>
      </template>

      <template #footer>
        <el-button @click="dialogVisible = false">关闭</el-button>
        <el-button v-if="inspectPassed" type="primary" :loading="submitting" @click="submit">确认报工</el-button>
      </template>
    </el-dialog>
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
const submitting = ref(false)
const generating = ref(false)
const scope = ref('todo')
const moNo = ref('')
const workshop = ref('')
const keyword = ref('')
const dialogVisible = ref(false)
const form = ref({ 加工单号: '', 工序编码: '', 工序名称: '', 排产单号: '', 批次数量: 0, 已报数量: 0, 剩余数量: 0 })
// 首检是「工序 × 加工单」级的门：同一工序的首个批次检过，后续批次共用，不需要再检
const inspectState = ref('未建单')
const inspectNo = ref('')

const unit = computed(() => form.value.工序单位 || '件')
const inspectPassed = computed(() => inspectState.value === '已通过')
const inspectTone = computed(() => (inspectPassed.value ? 'ok' : inspectState.value === '不合格' ? 'bad' : 'warn'))
const inspectToneText = computed(() => ({
  已通过: '已通过',
  待审批: '待审核',
  不合格: '不合格',
  未建单: '未建单',
}[inspectState.value] || '未检验'))
const inspectText = computed(() => {
  switch (inspectState.value) {
    case '已通过':
      return `首件检验已通过（${inspectNo.value}），可以按批次报工；累计报工不超过批次数量。`
    case '待审批':
      return `首件报检单 ${inspectNo.value} 还没审核通过：首件判定合格后才能报工。`
    case '不合格':
      return `首件报检单 ${inspectNo.value} 判定不合格：按处置（返工/返修/让步接收/报废）重检合格后才能报工。`
    default:
      return '该工序还没有首件报检单：先做首件检验，首件合格后才能报工。同一工序的首件只检一次，后续批次直接报工。'
  }
})
const reportTotal = computed(() => num(form.value.合格数量) + num(form.value.不合格数量))

function num(v) {
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}

const workshops = computed(() => [...new Set(rows.value.map((r) => r['生产车间']).filter(Boolean))])

const mos = computed(() => {
  const map = new Map()
  for (const r of rows.value) {
    const no = r['加工单号']
    if (!no) continue
    if (!map.has(no)) map.set(no, { no, product: r['产品名称'] || '', total: 0, done: 0 })
    const g = map.get(no)
    g.total += 1
    if (num(r['剩余数量']) <= 0) g.done += 1
  }
  return [...map.values()].sort((a, b) => String(b.no).localeCompare(String(a.no)))
})

/** 筛选后的批次，按加工单分组：同一张单的工序挨在一起，按批次号（= 工序顺序）升序 */
const groups = computed(() => {
  let list = rows.value
  if (scope.value === 'todo') list = list.filter((r) => r['可否报工'])
  if (moNo.value) list = list.filter((r) => r['加工单号'] === moNo.value)
  if (workshop.value) list = list.filter((r) => r['生产车间'] === workshop.value)
  const kw = keyword.value.trim().toLowerCase()
  if (kw) {
    list = list.filter((r) =>
      [r['加工单号'], r['产品名称'], r['工序名称'], r['产品编码'], r['排产单号']]
        .some((v) => String(v || '').toLowerCase().includes(kw))
    )
  }
  const byMo = new Map()
  for (const r of list) {
    const mo = r['加工单号'] || '—'
    if (!byMo.has(mo)) byMo.set(mo, { mo, product: r['产品名称'] || '', items: [], done: 0, planned: 0, reported: 0 })
    const g = byMo.get(mo)
    g.items.push(r)
    g.planned += num(r['批次数量'])
    g.reported += num(r['已报数量'])
    if (num(r['剩余数量']) <= 0) g.done += 1
  }
  const out = []
  for (const g of byMo.values()) {
    g.items.sort((a, b) => String(a['排产单号'] || '').localeCompare(String(b['排产单号'] || '')))
    g.percent = g.planned > 0 ? Math.round((g.reported / g.planned) * 100) : 0
    out.push(g)
  }
  return out.sort((a, b) => String(b.mo).localeCompare(String(a.mo)))
})

const kpi = computed(() => {
  const all = rows.value
  const orders = new Map()
  for (const r of all) {
    const no = r['加工单号']
    if (!no) continue
    const done = num(r['剩余数量']) <= 0
    orders.set(no, (orders.get(no) || false) || !done)
  }
  return {
    total: all.length,
    todo: all.filter((r) => r['可否报工']).length,
    orders: orders.size,
    runningOrders: [...orders.values()].filter(Boolean).length,
    done: all.filter((r) => num(r['剩余数量']) <= 0).length,
    doneRate: all.length ? Math.round((all.filter((r) => num(r['剩余数量']) <= 0).length / all.length) * 100) : 0,
    blocked: all.filter((r) => !r['可否报工'] && num(r['剩余数量']) > 0).length,
  }
})

function qty(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return '0'
  return n === Math.round(n) ? String(n) : n.toFixed(2)
}

// 状态词与「排产批次看板」同口径：可报工（成功）/ 已报满（中性）/ 等前置（告警）
function stateOf(row) {
  if (row['可否报工']) return { text: '可报工', tone: 'ok' }
  if (num(row['剩余数量']) <= 0) return { text: '已报满', tone: 'mute' }
  if (row['首检是否通过'] === false) {
    const s = String(row['首检状态'] || '')
    return { text: s === '不合格' ? '首检不合格' : s === '待审批' ? '待首检' : '待首检', tone: 'warn' }
  }
  return { text: '等前置', tone: 'warn' }
}

function openReport(row) {
  form.value = { ...row, 合格数量: Number(row['剩余数量']), 不合格数量: 0 }
  inspectState.value = String(row['首检状态'] || '未建单')
  inspectNo.value = String(row['首检单号'] || '')
  dialogVisible.value = true
}

/** 首检单还没建：直接按该批次所属工序补建一张（一工序一单，已存在则返回原单号）。 */
async function createFirstInspect() {
  generating.value = true
  try {
    const res = await engine.firstInspectByBatch(form.value['排产单号'])
    inspectNo.value = String(res?.['编号'] || '')
    inspectState.value = String(res?.['首检状态'] || '待审批')
    ElMessage.success(res?.['已存在']
      ? `该工序已有首件报检单 ${inspectNo.value}（${inspectState.value}）`
      : `已生成首件报检单 ${inspectNo.value}，请质检判定合格后再报工`)
    await load()
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '生成首件报检单失败')
  } finally {
    generating.value = false
  }
}

/** 跳到首件报检单（有单看单，没单先建） */
async function goInspect() {
  if (!inspectNo.value) await createFirstInspect()
  const no = inspectNo.value
  if (!no) return
  const path = '/panelx/list/FIRST_INSPECT'
  const query = { code: no }
  router.push({ path, query })
  tabs.open({ path, title: `首件报检单-${no}`, query })
}

async function submit() {
  const qualified = Number(form.value.合格数量 || 0)
  const rejected = Number(form.value.不合格数量 || 0)
  const q = qualified + rejected
  if (!(q > 0)) return ElMessage.warning('报工数量必须大于 0（合格 + 不合格）')
  if (q > Number(form.value['剩余数量']) + 1e-6) return ElMessage.warning('超过批次剩余可报数量')
  submitting.value = true
  try {
    const res = await engine.reportByBatch(form.value['排产单号'], qualified, rejected)
    const no = res?.['编号'] || ''
    ElMessage.success(`已生成工序汇报单 ${no}（已审核）`)
    dialogVisible.value = false
    await load()
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '报工失败')
  } finally {
    submitting.value = false
  }
}

async function load() {
  loading.value = true
  try {
    rows.value = (await engine.reportBatchBoard()) || []
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '加载报工数据失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
/* 与「排产批次看板」同一套容器与色彩：变量来自全局 .adx */
.rp-page {
  padding: 16px;
  height: 100%;
  display: flex;
  flex-direction: column;
  gap: 16px;
  background: var(--d-card);
  border: 1px solid var(--d-border);
  border-radius: var(--d-radius);
  min-height: 0;
}
.rp-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.rp-head h2 {
  margin: 0;
  font-size: 16px;
  line-height: 24px;
  font-weight: 600;
  color: var(--d-text);
}
.rp-sub {
  display: block;
  margin-top: 2px;
  font-size: 12px;
  line-height: 20px;
  color: var(--d-text-3);
}
.rp-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}
/* 指标条：一个容器四格，靠 1px 分隔线分层 */
.rp-kpis {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  border: 1px solid var(--d-border);
  border-radius: var(--d-radius);
  background: var(--d-card);
}
.kpi {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 12px 16px;
  min-width: 0;
}
.kpi + .kpi {
  border-left: 1px solid var(--d-border);
}
.kpi-label {
  font-size: 12px;
  line-height: 18px;
  color: var(--d-text-3);
}
.kpi-value {
  font-size: 24px;
  line-height: 32px;
  font-weight: 600;
  color: var(--d-text);
  font-variant-numeric: tabular-nums;
}
.kpi-hint {
  font-size: 12px;
  line-height: 18px;
  color: var(--d-text-3);
}
.rp-bar {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
.rp-tip {
  font-size: 12px;
  color: var(--d-text-3);
  margin-left: auto;
}
/* 下拉项 teleport 到 body，不在 .adx 作用域内 → 用 :root 上的全局 token */
.opt-no { color: var(--t-text-1); margin-right: 8px; font-variant-numeric: tabular-nums; }
.opt-prod { color: var(--t-text-3); font-size: 12px; }
.rp-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  align-content: start;
  align-items: start;
}

/* 生产加工单 = 一个 1px 线框框区，桌面一行两个，多的往下排 */
.mo {
  min-width: 0;
  border: 1px solid var(--d-border);
  border-radius: var(--d-radius-sm);
  background: var(--d-card);
  overflow: hidden;
}
.mo-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--d-border);
  background: var(--d-fill-soft);
  font-size: 12px;
  line-height: 18px;
}
.mo-no {
  color: var(--d-text);
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
.mo-prod {
  color: var(--d-text-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mo-meta {
  color: var(--d-text-3);
  white-space: nowrap;
}
.mo-bar {
  flex: none;
  margin-left: auto;
  width: 84px;
  height: 4px;
  background: var(--d-fill);
  border-radius: 2px;
  overflow: hidden;
}
.mo-bar i {
  display: block;
  height: 100%;
  background: var(--d-primary);
}
.mo-pct {
  width: 32px;
  text-align: right;
  color: var(--d-text-3);
  font-variant-numeric: tabular-nums;
}

/* 框内批次行：单行栅格，行间 1px 分隔线，悬停出浅底 */
.mo-rows {
  display: flex;
  flex-direction: column;
}
.br {
  display: grid;
  grid-template-columns: 112px 52px minmax(0, 1fr) 90px 72px 70px 68px;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--d-border);
  font-size: 12px;
  line-height: 20px;
  color: var(--d-text-2);
}
.br:last-child {
  border-bottom: none;
}
.br:hover {
  background: var(--d-fill-soft);
}
.br-no {
  color: var(--d-text-2);
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.2px;
}
.br-proc {
  font-size: 13px;
  color: var(--d-text);
  white-space: nowrap;
}
.br-shop {
  color: var(--d-text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.br-qty {
  text-align: right;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.br-qty b {
  font-size: 14px;
  font-weight: 600;
  color: var(--d-text);
}
.br-qty .sep {
  margin: 0 2px;
  color: var(--d-border-strong);
}
.br-qty i {
  margin-left: 2px;
  font-style: normal;
  color: var(--d-text-3);
}
.br-rest {
  text-align: right;
  font-weight: 500;
  color: var(--d-primary);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.br-rest.zero {
  font-weight: 400;
  color: var(--d-text-3);
}
.br-state {
  display: flex;
  justify-content: flex-end;
  min-width: 0;
}
.br-op {
  text-align: right;
  white-space: nowrap;
}

/* 状态词：可报工（绿点）/ 已报满（灰字）/ 等前置（橙色 chip，悬停看原因） */
.st {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-left: auto;
  font-size: 12px;
  line-height: 18px;
  color: var(--d-text-3);
  white-space: nowrap;
}
.st::before {
  content: '';
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
}
.st.is-ok {
  color: var(--d-ok);
}
.st.is-warn {
  padding: 0 6px;
  color: var(--d-warn);
  background: var(--d-warn-bg);
  border-radius: 4px;
}
.st.is-warn::before {
  display: none;
}
.dim {
  font-size: 12px;
  color: var(--d-text-3);
}
.rp-empty {
  grid-column: 1 / -1;
  margin: auto;
  padding: 40px 16px;
  text-align: center;
}
.rp-empty p {
  margin: 0;
  font-size: 12px;
  line-height: 20px;
  color: var(--d-text-3);
}
.rp-foot {
  font-size: 12px;
  line-height: 20px;
  color: var(--d-text-3);
}

/* ===== 报工弹窗：信息块 → 首检块 → 数量区，三段式 ===== */
.d-meta {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 6px 16px;
  padding: 12px;
  background: var(--d-fill-soft);
  border-radius: var(--d-radius-sm);
}
.d-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
  min-width: 0;
  font-size: 12px;
  line-height: 20px;
}
.d-k {
  flex: none;
  color: var(--d-text-3);
}
.d-v {
  color: var(--d-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.d-v.num {
  font-variant-numeric: tabular-nums;
}

/* 首检块：通过时绿、未检/待审时警示、不合格时红 */
.d-inspect {
  margin-top: 12px;
  padding: 12px;
  border: 1px solid var(--d-border);
  border-radius: var(--d-radius-sm);
}
.d-inspect.is-ok {
  background: var(--d-ok-bg);
  border-color: transparent;
}
.d-inspect.is-warn {
  background: var(--d-warn-bg);
  border-color: transparent;
}
.d-inspect.is-bad {
  background: var(--d-bad-bg);
  border-color: transparent;
}
.di-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.di-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--d-text);
}
.di-no {
  font-size: 12px;
  color: var(--d-text-3);
  font-variant-numeric: tabular-nums;
}
.di-text {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 20px;
  color: var(--d-text-2);
}
.di-ops {
  display: flex;
  gap: 8px;
  margin-top: 10px;
}
.di-ops:empty {
  display: none;
}
.st.is-bad {
  color: var(--d-bad);
}

/* 数量区：两个数字框并排 + 合计行 */
.d-qty {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin-top: 16px;
}
.dq-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.dq-label {
  font-size: 12px;
  line-height: 18px;
  color: var(--d-text-3);
}
.dq-item :deep(.el-input-number) {
  width: 100%;
}
.d-sum {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin-top: 12px;
  font-size: 12px;
  line-height: 20px;
  color: var(--d-text-3);
  font-variant-numeric: tabular-nums;
}
.d-sum b {
  font-size: 15px;
  color: var(--d-text);
}
.d-sum-sep {
  color: var(--d-border-strong);
}
.d-sum.is-over {
  color: var(--d-bad);
}
.d-sum.is-over b {
  color: var(--d-bad);
}
.d-sum-warn {
  margin-left: auto;
  color: var(--d-bad);
}
@media (max-width: 1200px) {
  .kpi-value {
    font-size: 20px;
    line-height: 28px;
  }
}
/* 窄屏一列：框区上下排，逐行读 */
@media (max-width: 1100px) {
  .rp-list {
    grid-template-columns: minmax(0, 1fr);
    gap: 12px;
  }
}
/* 手机：框内批次行折叠成两行，仍不外溢 */
@media (max-width: 768px) {
  .br {
    grid-template-columns: 1fr auto;
    row-gap: 2px;
  }
  .br-no {
    grid-column: 1 / -1;
  }
  .br-shop {
    grid-column: 1 / -1;
  }
  .br-qty {
    text-align: left;
  }
  .br-rest {
    text-align: left;
  }
  .br-state {
    justify-content: flex-start;
  }
  .br-op {
    text-align: right;
  }
}
@media (max-width: 768px) {
  .rp-page {
    height: auto;
    min-height: 100%;
    padding: 12px;
    gap: 12px;
  }
  .rp-kpis {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .kpi {
    padding: 10px 12px;
  }
  .kpi:nth-child(odd) {
    border-left: 0;
  }
  .kpi:nth-child(n + 3) {
    border-top: 1px solid var(--d-border);
  }
  .rp-bar .el-select,
  .rp-bar .el-input {
    width: 100% !important;
  }
  .rp-tip {
    margin-left: 0;
  }
}
</style>
