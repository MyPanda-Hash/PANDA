<!-- MaterialIssueBoard.vue — 生产领料（按排产批次）
     一行 = 一个排产批次上的一种材料；批次需用量 = 整单需用量 × 批次占比。
     领料直接生成材料出库单（不走领料申请单），生成后立即提交审批：
     审批中/已审批的批次不能再领，被驳回后可重新领料；审批通过才落库存。
     视觉：容器挂 .adx，取全局设计库语义层（--d-*）。 -->
<template>
  <div class="adx mi">
    <header class="mi-head">
      <div class="mi-title">
        <h2>生产领料{{ moNo ? ' · ' + moNo : '' }}</h2>
        <span class="mi-sub">
          按排产批次领料：一个批次一张出库单，批次需用量 = 整单需用量 × 批次占比
          <span class="dot">·</span>出库单生成后自动提交审批，审批通过才扣库存
        </span>
      </div>
      <el-button @click="moNo ? switchOrder() : $router.back()">{{ moNo ? '换单' : '返回' }}</el-button>
    </header>

    <!-- 未选加工单：先选一张已审核的加工单 -->
    <template v-if="!moNo">
      <el-table :data="orders" v-loading="loading" class="mi-table" height="calc(100vh - 300px)">
        <el-table-column label="加工单号" prop="编号" width="180" />
        <el-table-column label="单据日期" prop="单据日期" width="120" />
        <el-table-column label="客户" prop="客户" min-width="150" />
        <el-table-column label="产品" min-width="240">
          <template #default="{ row }">{{ productOf(row) }}</template>
        </el-table-column>
        <el-table-column label="数量" width="110" align="right">
          <template #default="{ row }"><span class="num">{{ qty(qtyOf(row)) }}</span></template>
        </el-table-column>
        <el-table-column label="操作" width="96" align="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="pickOrder(row)">领料</el-button>
          </template>
        </el-table-column>
      </el-table>
    </template>

    <template v-else>
      <section class="mi-metrics">
        <div class="metric">
          <span class="m-label">可领批次</span>
          <span class="m-line"><b class="m-value">{{ kpi.todo }}</b><i class="m-unit">批</i></span>
          <span class="m-hint">共 {{ kpi.total }} 行 · {{ kpi.materials }} 种材料</span>
        </div>
        <div class="metric">
          <span class="m-label">待审批出库</span>
          <span class="m-line"><b class="m-value">{{ kpi.pending }}</b><i class="m-unit">批</i></span>
          <span class="m-hint">审批通过才会扣库存</span>
        </div>
        <div class="metric">
          <span class="m-label">出库已审批</span>
          <span class="m-line"><b class="m-value">{{ kpi.approved }}</b><i class="m-unit">批</i></span>
          <span class="m-hint">已领满 {{ kpi.done }} 行</span>
        </div>
      </section>

      <el-table :data="rows" v-loading="loading" class="mi-table" height="calc(100vh - 380px)">
        <el-table-column label="排产批次" prop="排产单号" width="150" />
        <el-table-column label="工序" width="90">
          <template #default="{ row }">{{ row['工序名称'] || row['工序编码'] }}</template>
        </el-table-column>
        <el-table-column label="批次数量" width="96" align="right">
          <template #default="{ row }"><span class="num">{{ qty(row['批次数量']) }}</span></template>
        </el-table-column>
        <el-table-column label="材料" min-width="160">
          <template #default="{ row }">
            <span>{{ row['材料名称'] || row['材料编码'] }}</span>
            <span class="mi-code">{{ row['材料编码'] }}</span>
          </template>
        </el-table-column>
        <el-table-column label="整单需用" width="100" align="right">
          <template #default="{ row }"><span class="num muted">{{ qty(row['整单需用量']) }}</span></template>
        </el-table-column>
        <el-table-column label="批次需用" width="100" align="right">
          <template #default="{ row }"><span class="num">{{ qty(row['批次需用量']) }}</span></template>
        </el-table-column>
        <el-table-column label="已领" width="86" align="right">
          <template #default="{ row }"><span class="num muted">{{ qty(row['已领数量']) }}</span></template>
        </el-table-column>
        <el-table-column label="剩余可领" width="104" align="right">
          <template #default="{ row }">
            <b class="num" :class="Number(row['剩余可领']) > 0 ? 'is-open' : 'is-zero'">{{ qty(row['剩余可领']) }}</b>
          </template>
        </el-table-column>
        <el-table-column label="出库单" width="160">
          <template #default="{ row }">
            <el-button v-if="row['出库单号']" link type="primary" @click="openDoc(row['出库单号'])">
              {{ row['出库单号'] }}
            </el-button>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="出库状态" width="112">
          <template #default="{ row }">
            <span v-if="row['出库状态']" class="st" :class="'is-' + stateTone(row['出库状态'])">
              {{ row['出库状态'] }}
            </span>
            <span v-else class="muted">未领料</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="112" align="right" fixed="right">
          <template #default="{ row }">
            <el-button
              v-if="row['可否领料'] && isBatchHead(row)"
              type="primary"
              plain
              size="small"
              @click="openIssue(row)"
            >
              {{ row['出库状态'] === '已驳回' ? '重新领料' : '领料' }}
            </el-button>
            <span v-else-if="row['可否领料']" class="muted">同批领</span>
            <el-button
              v-else-if="row['出库状态'] === '草稿' && isBatchHead(row)"
              link
              type="primary"
              @click="submitDraft(row['出库单号'])"
            >
              提交审批
            </el-button>
            <span v-else-if="row['出库状态'] === '待审批'" class="muted">等审批</span>
            <span v-else-if="row['出库状态'] === '草稿'" class="muted">待提交</span>
            <span v-else class="muted">已领满</span>
          </template>
        </el-table-column>
      </el-table>
    </template>

    <footer class="mi-foot">
      同一批次同一时间只能有一张在途出库单：审批中或已审批通过时不能再领料，被驳回后可重新领料。
    </footer>

    <!-- 领料弹窗：批次信息 → 材料数量 → 领料人/仓库，三段式 -->
    <el-dialog v-model="visible" :title="`领料 · ${form.排产单号 || ''}`" width="min(640px, 94vw)">
      <div class="d-meta">
        <div class="d-row">
          <span class="d-k">排产批次</span>
          <span class="d-v num">{{ form.排产单号 }}</span>
        </div>
        <div class="d-row">
          <span class="d-k">工序</span>
          <span class="d-v">{{ form.工序名称 }}</span>
        </div>
        <div class="d-row">
          <span class="d-k">批次数量</span>
          <span class="d-v num">{{ qty(form.批次数量) }} 件</span>
        </div>
        <div class="d-row">
          <span class="d-k">加工单</span>
          <span class="d-v num">{{ moNo }}</span>
        </div>
      </div>

      <div class="d-block">
        <div class="d-block-head">本次领料</div>
        <div class="d-lines">
          <div class="d-line d-line-head">
            <span>材料</span>
            <span class="ta-r">剩余可领</span>
            <span class="ta-r">本次数量</span>
          </div>
          <div v-for="line in form.lines" :key="line['材料编码']" class="d-line">
            <span class="d-mat">
              {{ line['材料名称'] || line['材料编码'] }}
              <i class="mi-code">{{ line['材料编码'] }}</i>
            </span>
            <span class="ta-r num muted">{{ qty(line['剩余可领']) }}</span>
            <span class="ta-r">
              <el-input-number
                v-model="line.数量"
                :min="0"
                :max="Number(line['剩余可领'])"
                :step="1"
                size="small"
                controls-position="right"
              />
            </span>
          </div>
        </div>
      </div>

      <div class="d-form">
        <label class="d-field">
          <span class="d-label">领料人</span>
          <el-input v-model="form.领料人" placeholder="默认当前操作者" />
        </label>
        <label class="d-field">
          <span class="d-label">出库仓库</span>
          <el-input v-model="form.仓库" placeholder="留空按材料预出仓库" />
        </label>
      </div>

      <p class="d-note">
        生成材料出库单并自动提交审批；审批通过才扣减库存，驳回后可重新领料。
      </p>

      <template #footer>
        <el-button @click="visible = false">取消</el-button>
        <el-button type="primary" :loading="submitting" @click="submit">生成并提交审批</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { usePanelRuntime } from '@core/panel-runtime'
import { useTabsStore } from '@/stores/tabs'
import { useUserStore } from '@/stores/user'

const engine = usePanelRuntime()
const route = useRoute()
const router = useRouter()
const tabs = useTabsStore()
const user = useUserStore()

const moNo = ref(String(route.query.code || ''))
const rows = ref([])
const orders = ref([])
const loading = ref(false)
const submitting = ref(false)
const visible = ref(false)
const form = ref({ 排产单号: '', 工序名称: '', 批次数量: 0, lines: [], 领料人: '', 仓库: '' })

// 领料人默认填当前操作者账号（可改）：现场领料是谁点的就记谁
const operatorName = computed(() => user.realName || user.account || '')

const kpi = computed(() => {
  const all = rows.value
  const stateOf = (name) => new Set(all.filter((r) => r['出库状态'] === name).map((r) => r['排产单号'])).size
  return {
    total: all.length,
    todo: new Set(all.filter((r) => r['可否领料']).map((r) => r['排产单号'])).size,
    materials: new Set(all.map((r) => r['材料编码'])).size,
    done: all.filter((r) => Number(r['剩余可领']) <= 0).length,
    pending: stateOf('待审批'),
    approved: stateOf('已审批'),
  }
})

function qty(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return '0'
  return n === Math.round(n) ? String(n) : n.toFixed(2)
}
function qtyOf(row) {
  const p = (row.detail?.products || [])[0] || {}
  return p['数量'] || 0
}
function productOf(row) {
  const p = (row.detail?.products || [])[0] || {}
  return [p['产品名称'], p['规格型号']].filter(Boolean).join(' ') || '—'
}
function stateTone(state) {
  if (state === '已审批') return 'ok'
  if (state === '待审批') return 'warn'
  if (state === '已驳回') return 'bad'
  return 'mute'
}

/** 同一排产批次只在第一行挂按钮（领料 / 补提交），其余行只提示同批领。 */
function isBatchHead(row) {
  const first = rows.value.find((r) => r['排产单号'] === row['排产单号'])
  return first === row
}

function pickOrder(row) {
  moNo.value = row['编号']
  router.replace({ path: '/mes/materialIssue', query: { code: moNo.value } })
  tabs.open({ path: '/mes/materialIssue', title: `生产领料-${moNo.value}`, query: { code: moNo.value } })
  load()
}
function switchOrder() {
  moNo.value = ''
  router.replace({ path: '/mes/materialIssue' })
  load()
}

function openDoc(no) {
  const path = '/panelx/list/MATERIAL_OUT'
  const query = { code: no }
  router.push({ path, query })
  tabs.open({ path, title: `材料出库单-${no}`, query })
}

/** 老草稿（本次改动前生成、没自动提交的）在面板上直接补提交，免得去列表里找。 */
async function submitDraft(no) {
  if (!no) return
  submitting.value = true
  try {
    await engine.callButton({ panelCode: 'MATERIAL_OUT', buttonName: '提交审批', formData: { 编号: no } })
    ElMessage.success(`材料出库单 ${no} 已提交审批`)
    await load()
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '提交审批失败')
  } finally {
    submitting.value = false
  }
}

function openIssue(row) {
  const lines = rows.value
    .filter((r) => r['排产单号'] === row['排产单号'] && Number(r['剩余可领']) > 0)
    .map((r) => ({ ...r, 数量: Number(r['剩余可领']) }))
  form.value = {
    排产单号: row['排产单号'],
    工序名称: row['工序名称'] || row['工序编码'],
    批次数量: row['批次数量'],
    lines,
    领料人: operatorName.value,
    仓库: '',
  }
  visible.value = true
}

async function submit() {
  const items = form.value.lines
    .filter((l) => Number(l.数量) > 0)
    .map((l) => ({ 材料编码: l['材料编码'], 数量: Number(l.数量) }))
  if (!items.length) return ElMessage.warning('领料数量必须大于 0')
  if (!String(form.value.领料人 || '').trim()) return ElMessage.warning('请填写领料人')
  submitting.value = true
  try {
    const res = await engine.issueBatch({
      排产单号: form.value.排产单号,
      items,
      领料人: form.value.领料人,
      仓库: form.value.仓库,
    })
    const no = res?.['编号'] || ''
    const state = res?.['单据状态'] || '审批中'
    ElMessage.success(`已生成材料出库单 ${no}（${state}，${items.length} 行），审批通过后扣库存`)
    visible.value = false
    await load()
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '领料失败')
  } finally {
    submitting.value = false
  }
}

async function load() {
  loading.value = true
  try {
    if (!moNo.value) {
      const res = await engine.queryFormDataList({
        panelCode: 'MANU_ORDER', pageNo: 1, pageSize: 200, condition: { 单据状态: '已审核' },
      })
      orders.value = (res?.list || []).filter((r) => (r['单据状态'] || '') === '已审核')
      rows.value = []
    } else {
      rows.value = (await engine.materialIssueBoard(moNo.value)) || []
    }
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || '加载领料数据失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
/* 设计变量来自全局 .adx：主色 --d-primary、成功 --d-ok、告警 --d-warn、失败 --d-bad */
.mi {
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
.mi-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.mi-title h2 {
  margin: 0;
  font-size: 16px;
  line-height: 24px;
  font-weight: 600;
  color: var(--d-text);
}
.mi-sub {
  display: block;
  margin-top: 2px;
  font-size: 12px;
  line-height: 20px;
  color: var(--d-text-3);
}
.dot {
  margin: 0 4px;
  color: var(--d-border-strong);
}

/* 指标条：一个容器三格，靠 1px 分隔线分层 */
.mi-metrics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  border: 1px solid var(--d-border);
  border-radius: var(--d-radius);
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

/* 表格：antd 口径（表头浅底、1px 边框、数字右对齐） */
.mi-table {
  flex: 1;
  min-height: 0;
  border: 1px solid var(--d-border);
  border-radius: var(--d-radius-sm);
  overflow: hidden;
}
.mi-table :deep(th.el-table__cell) {
  background: var(--d-fill-soft);
  font-weight: 500;
  color: var(--d-text-2);
}
.mi-table :deep(td.el-table__cell) {
  color: var(--d-text);
}
.mi-table :deep(.el-table__inner-wrapper::before) {
  display: none;
}
.num {
  font-variant-numeric: tabular-nums;
}
.num.is-open {
  color: var(--d-primary);
  font-weight: 600;
}
.num.is-zero {
  color: var(--d-text-3);
  font-weight: 400;
}
.muted {
  color: var(--d-text-3);
  font-size: 12px;
}
.mi-code {
  margin-left: 6px;
  font-style: normal;
  font-size: 11px;
  color: var(--d-text-3);
  font-variant-numeric: tabular-nums;
}

/* 状态词：与看板/报工同口径 */
.st {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  line-height: 18px;
  white-space: nowrap;
}
.st::before {
  content: '';
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
}
.st.is-ok { color: var(--d-ok); }
.st.is-warn { color: var(--d-warn); }
.st.is-bad { color: var(--d-bad); }
.st.is-mute { color: var(--d-text-3); }

.mi-foot {
  font-size: 12px;
  line-height: 20px;
  color: var(--d-text-3);
}

/* ===== 领料弹窗：信息块 → 材料块 → 表单 ===== */
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
.d-block {
  margin-top: 16px;
}
.d-block-head {
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--d-text);
}
.d-lines {
  border: 1px solid var(--d-border);
  border-radius: var(--d-radius-sm);
  overflow: hidden;
}
.d-line {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 110px 150px;
  align-items: center;
  gap: 12px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--d-border);
  font-size: 13px;
  color: var(--d-text);
}
.d-line:last-child {
  border-bottom: none;
}
.d-line-head {
  background: var(--d-fill-soft);
  font-size: 12px;
  color: var(--d-text-3);
}
.d-mat {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ta-r {
  text-align: right;
}
.d-line :deep(.el-input-number) {
  width: 120px;
}
.d-form {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  margin-top: 16px;
}
.d-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.d-label {
  font-size: 12px;
  line-height: 18px;
  color: var(--d-text-3);
}
.d-note {
  margin: 12px 0 0;
  font-size: 12px;
  line-height: 20px;
  color: var(--d-text-3);
}

@media (max-width: 900px) {
  .mi {
    height: auto;
    min-height: 100%;
    padding: 12px;
    gap: 12px;
  }
  .mi-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .metric:nth-child(odd) {
    border-left: 0;
  }
  .metric:nth-child(n + 3) {
    border-top: 1px solid var(--d-border);
    grid-column: 1 / -1;
  }
  .d-meta,
  .d-form {
    grid-template-columns: minmax(0, 1fr);
  }
  .d-line {
    grid-template-columns: minmax(0, 1fr) 90px;
  }
  .d-line > .ta-r:last-child {
    grid-column: 1 / -1;
  }
}
</style>
