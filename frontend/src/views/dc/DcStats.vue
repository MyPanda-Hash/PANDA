<!-- DcStats.vue — 状态统计（数采）
     按时间范围统计设备在线率、在线/离线数量与报警分布；柱状用 div 高度，不引图表库。
     视觉：容器挂 .adx，取全局设计库语义层（--d-*）。 -->
<template>
  <div class="adx dc dc-stats">
    <header class="dc-head">
      <div class="dc-title">
        <h2>状态统计</h2>
        <span class="dc-sub">
          按时间范围统计设备在线率、在线 / 离线数量与报警分布
          <span class="dot">·</span>在线时长由网关心跳与实际上报推算，未接入设备单独计数
        </span>
      </div>
      <div class="dc-actions">
        <el-date-picker v-model="range" type="daterange" unlink-panels value-format="YYYY-MM-DD"
                        size="default" start-placeholder="开始日期" end-placeholder="结束日期"
                        :clearable="false" class="dc-range" @change="onRangeChange" />
        <el-button :loading="loading" @click="load(true)">刷新</el-button>
      </div>
    </header>

    <section class="dc-metrics">
      <div v-for="m in kpiCards" :key="m.label" class="dc-metric">
        <span class="dc-metric-label">{{ m.label }}</span>
        <span class="dc-metric-value" :class="m.tone">{{ m.value }}</span>
      </div>
    </section>

    <section class="dc-card">
      <div class="dc-card-head">
        <span>在线 / 离线趋势</span>
        <span class="dc-legend">
          <i class="lg on"></i>在线
          <i class="lg off"></i>离线
        </span>
      </div>
      <div v-if="trend.length" class="dc-trend-wrap">
        <div class="dc-trend">
          <div v-for="(t, i) in trend" :key="i" class="dc-trend-col" :title="trendTip(t)">
            <div class="dc-trend-stack">
              <i class="bar off" :style="{ height: barHeight(t.offline) }"></i>
              <i class="bar on" :style="{ height: barHeight(t.online) }"></i>
            </div>
            <span class="dc-trend-label">{{ t.label }}</span>
          </div>
        </div>
      </div>
      <div v-else class="dc-empty-block">
        <span>区间内没有设备在线记录</span>
        <span class="dc-hint">网关上报后这里才会出现柱状趋势</span>
        <el-button type="primary" @click="goConfig">去 IOT设备配置</el-button>
      </div>
    </section>

    <div class="dc-stats-grid">
      <section class="dc-card">
        <div class="dc-card-head">
          <span>按设备在线率</span>
          <span class="dc-hint">共 {{ byDevice.length }} 台设备</span>
        </div>
        <el-table v-loading="loading" :data="byDevice" empty-text="区间内没有设备记录；先到「IOT设备配置」登记设备">
          <el-table-column label="设备" min-width="150" show-overflow-tooltip>
            <template #default="{ row }">
              <span class="dc-code">{{ row.deviceCode || '—' }}</span>
              <span v-if="row.deviceName" class="dc-muted"> · {{ row.deviceName }}</span>
            </template>
          </el-table-column>
          <el-table-column label="在线率" width="120" align="right">
            <template #default="{ row }">
              <!-- 从未接入的设备没有状态日志，在线率是未知而不是 0% -->
              <span class="num" :class="{ muted: row.onlineRate === null || row.onlineRate === undefined }">
                {{ rateText(row.onlineRate) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="在线时长" width="112" align="right">
            <template #default="{ row }"><span class="num dc-muted">{{ formatSeconds(row.onlineSeconds) }}</span></template>
          </el-table-column>
          <el-table-column label="离线时长" width="112" align="right">
            <template #default="{ row }"><span class="num dc-muted">{{ formatSeconds(row.offlineSeconds) }}</span></template>
          </el-table-column>
          <el-table-column label="状态变化" width="92" align="right">
            <template #default="{ row }"><span class="num">{{ row.statusChanges ?? 0 }}</span></template>
          </el-table-column>
          <el-table-column label="报警" width="76" align="right">
            <template #default="{ row }">
              <span class="num" :class="{ alarm: Number(row.alarmCount) > 0 }">{{ row.alarmCount ?? 0 }}</span>
            </template>
          </el-table-column>
          <el-table-column label="当前" width="84">
            <template #default="{ row }">
              <span class="dc-tag" :class="isOnline(row.currentStatus) ? 'ok' : 'muted'">
                {{ isOnline(row.currentStatus) ? '在线' : '离线' }}
              </span>
            </template>
          </el-table-column>
        </el-table>
      </section>

      <section class="dc-card">
        <div class="dc-card-head">
          <span>报警按级别</span>
          <span class="dc-hint">共 {{ alarmTotal }} 条 · 未处理 {{ unhandled }}</span>
        </div>
        <div v-if="byLevel.length" class="dc-levels">
          <div v-for="lv in byLevel" :key="lv.level" class="dc-level">
            <span class="dc-level-name">
              <span class="dc-tag" :class="levelTone(lv.level)">{{ lv.level || '未分级' }}</span>
            </span>
            <span class="dc-level-track"><i :style="{ width: levelWidth(lv.count) }"></i></span>
            <span class="dc-level-num num">{{ lv.count }}</span>
          </div>
        </div>
        <div v-else class="dc-empty-block">
          <span>区间内没有报警记录</span>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import { getDcStats } from '@/business/dcApi'
import { formatSeconds, rateText, trendMax } from '@/business/dc-view'

const LEVEL_TONES = { 高: 'alarm', 中: 'warn', 低: 'muted', critical: 'alarm', major: 'warn', minor: 'muted', warning: 'warn', info: 'muted' }

const router = useRouter()
const loading = ref(false)
const summary = ref({})
const byDevice = ref([])
const trend = ref([])
const byLevel = ref([])
const alarmTotal = ref(0)
const unhandled = ref(0)
const range = ref(defaultRange())

function ymd(date) {
  const y = date.getFullYear()
  const m = String(date.getMonth() + 1).padStart(2, '0')
  const d = String(date.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

/** 默认最近 30 天（含今天） */
function defaultRange() {
  const to = new Date()
  const from = new Date(to.getTime() - 29 * 24 * 3600 * 1000)
  return [ymd(from), ymd(to)]
}

const kpiCards = computed(() => {
  const s = summary.value || {}
  const total = num(s.deviceTotal, byDevice.value.length)
  const online = num(s.onlineNow, 0)
  const offline = num(s.offlineNow, Math.max(0, total - online))
  const connected = num(s.connectedTotal, Math.max(0, total - num(s.disabledTotal, 0)))
  const idle = Math.max(0, total - connected)
  const rate = s.onlineRate
  return [
    {
      label: '在线率',
      value: rateText(rate),
      tone: Number.isFinite(Number(rate)) && rate !== null && rate !== undefined
        ? (Number(rate) >= 90 ? 'ok' : Number(rate) >= 60 ? 'warn' : 'alarm')
        : '',
    },
    { label: '在线', value: online, tone: 'ok' },
    { label: '离线', value: offline, tone: offline > 0 ? 'warn' : '' },
    { label: '未接入', value: idle, tone: idle > 0 ? 'warn' : '' },
  ]
})

const maxTrend = computed(() => trendMax(trend.value))

const maxLevel = computed(() => byLevel.value.reduce((m, lv) => Math.max(m, num(lv.count, 0)), 0))

function num(v, fallback) {
  const n = Number(v)
  return Number.isFinite(n) ? n : fallback
}

function isOnline(status) {
  return status === '在线' || status === 'online' || status === true
}

function levelTone(level) {
  return LEVEL_TONES[level] || 'muted'
}

function levelWidth(count) {
  const max = maxLevel.value
  if (!max) return '2%'
  return `${Math.max(2, Math.round((num(count, 0) / max) * 100))}%`
}

function barHeight(value) {
  const total = maxTrend.value || 1
  return `${Math.max(2, Math.round((num(value, 0) / total) * 100))}%`
}

function trendTip(t) {
  return `${t.label} · 在线 ${num(t.online, 0)} / 离线 ${num(t.offline, 0)}`
}

function goConfig() {
  router.push('/mes/dc/config')
}

function onRangeChange(v) {
  if (!Array.isArray(v) || v.length !== 2) return
  load()
}

async function load(showToast) {
  loading.value = true
  try {
    const [from, to] = range.value || defaultRange()
    const data = await getDcStats(from, to)
    summary.value = data?.summary || {}
    byDevice.value = Array.isArray(data?.byDevice) ? data.byDevice : []
    trend.value = Array.isArray(data?.trend) ? data.trend : []
    const a = data?.alarms || {}
    byLevel.value = Array.isArray(a.byLevel) ? a.byLevel : []
    alarmTotal.value = num(a.total, 0)
    unhandled.value = num(a.unhandled, 0)
    if (showToast) ElMessage.success('已刷新')
  } catch (e) {
    byDevice.value = []
    trend.value = []
    byLevel.value = []
    alarmTotal.value = 0
    unhandled.value = 0
    ElMessage.error(e?.message || '加载状态统计失败')
  } finally {
    loading.value = false
  }
}

onMounted(() => load())
</script>

<style scoped>
.dc {
  padding: 14px 18px 18px;
  min-height: 100%;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  gap: 10px;
  background: var(--d-bg, var(--d-fill-soft));
}

.dc-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.dc-title h2 {
  margin: 0;
  font-size: 16px;
  line-height: 24px;
  color: var(--d-text, var(--d-text));
}

.dc-sub {
  display: block;
  margin-top: 4px;
  font-size: 12px;
  line-height: 20px;
  color: var(--d-text-3, var(--d-text-3));
}

.dot {
  margin: 0 6px;
  color: var(--d-border-strong, var(--d-border-strong));
}

.dc-actions {
  display: flex;
  gap: 8px;
  align-items: center;
  flex: 0 0 auto;
}

.dc-range { width: 250px; }

.dc-metrics {
  display: flex;
  gap: 10px;
}

.dc-metric {
  flex: 1;
  padding: 8px 12px;
  border: 1px solid var(--d-border, var(--d-border));
  border-radius: 6px;
  background: var(--d-card);
}

.dc-metric-label {
  display: block;
  font-size: 12px;
  line-height: 18px;
  color: var(--d-text-3, var(--d-text-3));
}

.dc-metric-value {
  display: block;
  font-size: 18px;
  line-height: 26px;
  font-variant-numeric: tabular-nums;
  color: var(--d-text, var(--d-text));
}

.dc-metric-value.ok { color: var(--d-ok, var(--d-ok)); }
.dc-metric-value.warn { color: var(--d-warn, var(--d-warn)); }
.dc-metric-value.alarm { color: var(--d-bad); }

.dc-card {
  border: 1px solid var(--d-border, var(--d-border));
  border-radius: 6px;
  background: var(--d-card);
  padding: 8px 12px 12px;
  min-width: 0;
}

.dc-card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 4px 0 8px;
  font-size: 13px;
  color: var(--d-text, var(--d-text));
}

.dc-legend {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--d-text-3, var(--d-text-3));
}

.lg {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 4px;
}

.lg.on { background: #1f8a5b; }
.lg.off { background: var(--d-border-strong); }

.dc-trend-wrap {
  overflow-x: auto;
  padding-bottom: 2px;
}

.dc-trend {
  display: flex;
  align-items: flex-end;
  gap: 6px;
  height: 168px;
  min-width: 100%;
}

.dc-trend-col {
  flex: 1 0 18px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: flex-end;
  height: 100%;
}

.dc-trend-stack {
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  gap: 1px;
  width: 100%;
  height: 140px;
}

.dc-trend-stack .bar {
  display: block;
  width: 100%;
  border-radius: 4px 2px 0 0;
}

.dc-trend-stack .bar.on { background: #1f8a5b; }
.dc-trend-stack .bar.off { background: var(--d-border-strong); }

.dc-trend-label {
  margin-top: 4px;
  font-size: 11px;
  line-height: 16px;
  color: var(--d-text-3, var(--d-text-3));
  white-space: nowrap;
}

.dc-stats-grid {
  display: grid;
  grid-template-columns: minmax(0, 2fr) minmax(0, 1fr);
  gap: 10px;
  align-items: start;
}

.dc-levels {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 4px 0;
}

.dc-level {
  display: flex;
  align-items: center;
  gap: 10px;
}

.dc-level-name { flex: 0 0 auto; }

.dc-level-track {
  flex: 1;
  height: 8px;
  border-radius: 4px;
  background: var(--d-bg, var(--d-fill-soft));
  overflow: hidden;
}

.dc-level-track i {
  display: block;
  height: 100%;
  background: #b94d3f;
  border-radius: 4px;
}

.dc-level-num {
  flex: 0 0 36px;
  text-align: right;
  font-size: 13px;
  color: var(--d-text, var(--d-text));
}

.dc-code {
  font-variant-numeric: tabular-nums;
  color: var(--d-text, var(--d-text));
}

.dc-muted, .num.muted { color: var(--d-text-3, var(--d-text-3)); }

.dc-hint { font-size: 12px; color: var(--d-text-3, var(--d-text-3)); }

.num { font-variant-numeric: tabular-nums; }
.num.alarm { color: var(--d-bad); }

.dc-tag {
  display: inline-block;
  padding: 0 6px;
  border-radius: 4px;
  font-size: 12px;
  line-height: 20px;
  border: 1px solid var(--d-border-strong, var(--d-border-strong));
  color: var(--d-text-2, var(--d-text-2));
}

.dc-tag.ok { border-color: var(--d-ok-bg); background: var(--d-ok-bg); color: var(--d-ok); }
.dc-tag.warn { border-color: var(--d-warn-bg); background: var(--d-warn-bg); color: var(--d-warn); }
.dc-tag.alarm { border-color: var(--d-bad-bg); background: var(--d-bad-bg); color: var(--d-bad); }
.dc-tag.muted { color: var(--d-text-3, var(--d-text-3)); }

.dc-empty-block {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 28px 0;
  font-size: 13px;
  color: var(--d-text-2, var(--d-text-2));
}

@media (max-width: 900px) {
  .dc-metrics { flex-wrap: wrap; }
  .dc-stats-grid { grid-template-columns: 1fr; }
}
</style>
