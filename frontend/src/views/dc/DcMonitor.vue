<!-- DcMonitor.vue — 数据监控（数采）
     左选设备，右看采集点实时值、最新 10 条流水与报警记录；报警处理写经办说明。
     数据全部来自网关上报，没有就是真没有，不造假数据。
     视觉：容器挂 .adx，取全局设计库语义层（--d-*）。 -->
<template>
  <div class="adx dc dc-monitor">
    <header class="dc-head">
      <div class="dc-title">
        <h2>数据监控</h2>
        <span class="dc-sub">
          按设备查看采集点实时值与报警记录
          <span class="dot">·</span>只读展示网关上报的数据，未接入设备显示离线
        </span>
      </div>
      <div class="dc-actions">
        <el-button :loading="loading" @click="load(true)">刷新</el-button>
      </div>
    </header>

    <div class="dc-mon-grid">
      <aside class="dc-card dc-side">
        <div class="dc-card-head">
          <span>设备列表</span>
          <span class="dc-hint">{{ onlineCount }} / {{ devices.length }} 在线</span>
        </div>
        <div v-if="devices.length" class="dc-dev-list">
          <button v-for="d in devices" :key="d.id" type="button" class="dc-dev"
                  :class="{ active: d.id === selectedId }" @click="selectDevice(d)">
            <span class="dc-dot" :class="d.online ? 'on' : 'off'"></span>
            <span class="dc-dev-main">
              <span class="dc-dev-name">{{ d.name || d.code }}</span>
              <span class="dc-dev-meta">{{ d.code }}<template v-if="d.type"> · {{ d.type }}</template></span>
            </span>
            <span v-if="Number(d.alarmOpen) > 0" class="dc-badge">{{ d.alarmOpen }}</span>
          </button>
        </div>
        <p v-else-if="loading" class="dc-empty-text">加载中…</p>
        <p v-else class="dc-empty-text">尚未登记设备，先去「IOT设备配置」新增</p>
      </aside>

      <main class="dc-main">
        <section v-if="current" class="dc-card dc-dev-head">
          <div class="dc-dev-title">
            <h3>{{ current.name || current.code }}</h3>
            <span class="dc-hint">{{ current.code }}<template v-if="current.type"> · {{ current.type }}</template></span>
          </div>
          <div class="dc-dev-side">
            <span class="dc-tag" :class="current.online ? 'ok' : 'muted'">{{ current.online ? '在线' : '离线' }}</span>
            <span class="dc-hint">最近上报：{{ fmtAt(current.lastSeenAt) }}</span>
          </div>
        </section>

        <section v-if="current" class="dc-card">
          <div class="dc-card-head">
            <span>实时数据</span>
            <span class="dc-hint">共 {{ points.length }} 个采集点</span>
          </div>
          <el-table v-loading="loading" :data="points" empty-text="该设备尚未接入采集数据（硬件上报后自动出现）">
            <el-table-column label="采集点" min-width="160" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="dc-code">{{ row.code }}</span>
                <span v-if="row.name" class="dc-muted"> · {{ row.name }}</span>
              </template>
            </el-table-column>
            <el-table-column label="实时值" min-width="110" align="right">
              <template #default="{ row }">
                <span class="dc-value" :class="pointTone(row.status)">{{ row.valueText ?? '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="单位" width="84">
              <template #default="{ row }"><span class="dc-muted">{{ row.unit || '—' }}</span></template>
            </el-table-column>
            <el-table-column label="采集时间" width="160">
              <template #default="{ row }"><span class="dc-muted">{{ fmtAt(row.at) }}</span></template>
            </el-table-column>
            <el-table-column label="状态" width="96">
              <template #default="{ row }">
                <span class="dc-tag" :class="pointTone(row.status)">{{ pointLabel(row.status) }}</span>
              </template>
            </el-table-column>
          </el-table>
        </section>

        <section v-if="current" class="dc-card">
          <div class="dc-card-head">
            <span>最新 10 条数据</span>
            <span class="dc-hint">只保留每个采集点的最新记录</span>
          </div>
          <el-table :data="samples" empty-text="该设备暂无采集数据">
            <el-table-column label="序号" width="64" align="right">
              <template #default="{ $index }"><span class="num dc-muted">{{ $index + 1 }}</span></template>
            </el-table-column>
            <el-table-column label="采集点" min-width="150" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="dc-code">{{ row.pointCode || '—' }}</span>
                <span v-if="row.pointName" class="dc-muted"> · {{ row.pointName }}</span>
              </template>
            </el-table-column>
            <el-table-column label="数值" min-width="110" align="right">
              <template #default="{ row }"><span class="dc-value">{{ row.valueText ?? '—' }}</span></template>
            </el-table-column>
            <el-table-column label="单位" width="80">
              <template #default="{ row }"><span class="dc-muted">{{ row.unit || '—' }}</span></template>
            </el-table-column>
            <el-table-column label="采集时间" width="176">
              <template #default="{ row }"><span class="dc-muted">{{ fmtAt(row.at) }}</span></template>
            </el-table-column>
          </el-table>
        </section>

        <section v-if="current" class="dc-card">
          <div class="dc-card-head">
            <span>报警记录</span>
            <span class="dc-hint">未处理 {{ unhandledCount }} / 共 {{ alarms.length }}</span>
          </div>
          <el-table v-loading="loading" :data="alarms" empty-text="该设备暂无报警记录">
            <el-table-column label="级别" width="84">
              <template #default="{ row }">
                <span class="dc-tag" :class="levelTone(row.level)">{{ row.level || '—' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="报警内容" min-width="180" show-overflow-tooltip>
              <template #default="{ row }">{{ row.message || '—' }}</template>
            </el-table-column>
            <el-table-column label="触发值" width="104" align="right">
              <template #default="{ row }"><span class="dc-muted">{{ row.valueText ?? '—' }}</span></template>
            </el-table-column>
            <el-table-column label="发生时间" width="160">
              <template #default="{ row }"><span class="dc-muted">{{ fmtAt(row.occurAt) }}</span></template>
            </el-table-column>
            <el-table-column label="处理状态" width="96">
              <template #default="{ row }">
                <span class="dc-tag" :class="row.state === '已处理' ? 'ok' : 'alarm'">{{ row.state || '未处理' }}</span>
              </template>
            </el-table-column>
            <el-table-column label="处理人 / 时间" min-width="150">
              <template #default="{ row }">
                <span class="dc-muted">{{ row.handledBy || '—' }}<template v-if="row.handledAt"> · {{ fmtAt(row.handledAt) }}</template></span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="86" align="right">
              <template #default="{ row }">
                <el-button link type="primary" :disabled="row.state === '已处理'" @click="openHandle(row)">处理</el-button>
              </template>
            </el-table-column>
          </el-table>
        </section>

        <section v-if="!current && !loading" class="dc-card">
          <div class="dc-empty-block">
            <span>还没有可监控的设备</span>
            <span class="dc-hint">先在「IOT设备配置」登记网关与设备，硬件上报后这里才有实时数据</span>
            <el-button type="primary" @click="goConfig">去 IOT设备配置</el-button>
          </div>
        </section>
      </main>
    </div>

    <el-dialog v-model="handleVisible" title="处理报警" width="min(480px, 94vw)" append-to-body>
      <p class="dc-dialog-tip">报警内容：{{ handleForm.message || '—' }}</p>
      <el-form :model="handleForm" label-width="80px" @submit.prevent>
        <el-form-item label="处理说明">
          <el-input v-model="handleForm.note" type="textarea" :rows="3" maxlength="200" show-word-limit
                    placeholder="选填，记录处置方式" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="handleVisible = false">取消</el-button>
        <el-button type="primary" :loading="handling" @click="submitHandle">确认处理</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useRouter } from 'vue-router'
import { getDcMonitor, handleDcAlarm } from '@/business/dcApi'
import { pointTone } from '@/business/dc-view'

const router = useRouter()

const POINT_LABELS = { normal: '正常', alarm: '报警', stale: '数据过期' }
const LEVEL_TONES = { 高: 'alarm', 中: 'warn', 低: 'muted', critical: 'alarm', major: 'warn', minor: 'muted', warning: 'warn', info: 'muted' }

const loading = ref(false)
const devices = ref([])
const points = ref([])
const samples = ref([])
const alarms = ref([])
const selectedId = ref(null)
const handleVisible = ref(false)
const handling = ref(false)
const handleForm = ref({ id: null, message: '', note: '' })

const current = computed(() => devices.value.find((d) => d.id === selectedId.value) || null)
const onlineCount = computed(() => devices.value.filter((d) => d.online).length)
const unhandledCount = computed(() => alarms.value.filter((a) => a.state !== '已处理').length)

function fmtAt(v) {
  if (!v) return '—'
  return String(v).replace('T', ' ').slice(0, 19)
}

function pointLabel(status) {
  return POINT_LABELS[status] || '未知'
}

function levelTone(level) {
  return LEVEL_TONES[level] || 'muted'
}

function errText(e, fallback) {
  return e?.message || fallback
}

async function load(showToast) {
  loading.value = true
  try {
    const data = await getDcMonitor(selectedId.value ?? undefined)
    devices.value = Array.isArray(data?.devices) ? data.devices : []
    points.value = Array.isArray(data?.points) ? data.points : []
    samples.value = Array.isArray(data?.samples) ? data.samples : []
    alarms.value = Array.isArray(data?.alarms) ? data.alarms : []
    if (selectedId.value !== null && !devices.value.some((d) => d.id === selectedId.value)) {
      selectedId.value = devices.value.length ? devices.value[0].id : null
      if (selectedId.value !== null) await load()
    }
    if (showToast) ElMessage.success('已刷新')
  } catch (e) {
    points.value = []
    samples.value = []
    alarms.value = []
    ElMessage.error(errText(e, '加载监控数据失败'))
  } finally {
    loading.value = false
  }
}

async function selectDevice(d) {
  if (d.id === selectedId.value) return
  selectedId.value = d.id
  points.value = []
  samples.value = []
  alarms.value = []
  await load()
}

function openHandle(row) {
  handleForm.value = { id: row.id, message: row.message || '', note: '' }
  handleVisible.value = true
}

async function submitHandle() {
  const f = handleForm.value
  if (!f.id) return
  handling.value = true
  try {
    await handleDcAlarm(f.id, f.note)
    handleVisible.value = false
    ElMessage.success('报警已处理')
    await load()
  } catch (e) {
    ElMessage.error(errText(e, '处理报警失败'))
  } finally {
    handling.value = false
  }
}

function goConfig() {
  router.push('/mes/dc/config')
}

onMounted(async () => {
  await load()
  if (selectedId.value === null && devices.value.length) {
    selectedId.value = devices.value[0].id
    await load()
  }
})
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
  flex: 0 0 auto;
}

.dc-mon-grid {
  display: grid;
  grid-template-columns: 248px minmax(0, 1fr);
  gap: 10px;
  align-items: start;
}

.dc-card {
  border: 1px solid var(--d-border, var(--d-border));
  border-radius: 6px;
  background: var(--d-card);
  padding: 8px 12px 12px;
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

.dc-side {
  position: sticky;
  top: 8px;
}

.dc-dev-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.dc-dev {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 6px 8px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  text-align: left;
  cursor: pointer;
  font: inherit;
  color: inherit;
}

.dc-dev:hover { background: var(--d-bg, var(--d-fill-soft)); }

.dc-dev.active {
  border-color: var(--d-border-strong, var(--d-border-strong));
  background: var(--d-bg, var(--d-fill-soft));
}

.dc-dev-main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}

.dc-dev-name {
  font-size: 13px;
  line-height: 20px;
  color: var(--d-text, var(--d-text));
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dc-dev-meta {
  font-size: 12px;
  line-height: 18px;
  color: var(--d-text-3, var(--d-text-3));
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dc-badge {
  min-width: 18px;
  padding: 0 5px;
  border-radius: 8px;
  background: var(--d-bad-bg);
  border: 1px solid var(--d-bad-bg);
  color: var(--d-bad);
  font-size: 12px;
  line-height: 18px;
  text-align: center;
  font-variant-numeric: tabular-nums;
}

.dc-main {
  display: flex;
  flex-direction: column;
  gap: 10px;
  min-width: 0;
}

.dc-dev-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.dc-dev-title h3 {
  margin: 0;
  font-size: 14px;
  line-height: 22px;
  color: var(--d-text, var(--d-text));
}

.dc-dev-side {
  display: flex;
  align-items: center;
  gap: 10px;
}

.dc-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--d-border-strong);
  flex: 0 0 auto;
}

.dc-dot.on { background: var(--d-ok, var(--d-ok)); }

.dc-code {
  font-variant-numeric: tabular-nums;
  color: var(--d-text, var(--d-text));
}

.dc-muted { color: var(--d-text-3, var(--d-text-3)); }
.dc-hint { font-size: 12px; color: var(--d-text-3, var(--d-text-3)); }

.dc-value {
  font-size: 14px;
  font-variant-numeric: tabular-nums;
  color: var(--d-text, var(--d-text));
}

.dc-value.ok { color: var(--d-text, var(--d-text)); }
.dc-value.alarm { color: var(--d-bad); }
.dc-value.muted { color: var(--d-text-3, var(--d-text-3)); }

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

.dc-empty-text {
  margin: 6px 0;
  font-size: 12px;
  line-height: 20px;
  color: var(--d-text-3, var(--d-text-3));
}

.dc-empty-block {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 28px 0;
  font-size: 13px;
  color: var(--d-text-2, var(--d-text-2));
}

.dc-dialog-tip {
  margin: 0 0 10px;
  font-size: 12px;
  line-height: 20px;
  color: var(--d-text-3, var(--d-text-3));
}

@media (max-width: 900px) {
  .dc-mon-grid { grid-template-columns: 1fr; }
  .dc-side { position: static; }
}
</style>
