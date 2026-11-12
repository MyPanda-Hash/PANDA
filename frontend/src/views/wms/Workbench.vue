<!-- Workbench.vue — 仓内作业工作台（拣货 / 上架 / 复核 / 波次）
     任务来自已审核的出入库单，作业进度落 wms_task：拣了多少、上架到哪个货位、复核结论。
     库存口径不变：库存仍由单据审核落 stock_txn，这里只记录「现场怎么做的」。
     视觉：容器挂 .adx，取全局设计库语义层（--d-*）。 -->
<template>
  <div class="adx wb">
    <header class="wb-head">
      <div class="wb-title">
        <h2>仓内作业</h2>
        <span class="wb-sub">
          拣货 → 复核 → 上架：任务由已审核单据同步而来，重复同步不会造重复任务
          <span class="dot">·</span>作业进度只记现场，不重复扣库存
        </span>
      </div>
      <div class="wb-actions">
        <el-button @click="sync('PICK')">同步拣货任务</el-button>
        <el-button @click="sync('PUTAWAY')">同步上架任务</el-button>
        <el-button type="primary" :disabled="!pendingPicks" @click="wave">编波次（待拣 {{ pendingPicks }}）</el-button>
      </div>
    </header>

    <section class="wb-metrics">
      <div v-for="m in metrics" :key="m.label" class="wb-metric">
        <span class="wb-metric-label">{{ m.label }}</span>
        <span class="wb-metric-value" :class="m.tone">{{ m.value }}</span>
      </div>
    </section>

    <el-tabs v-model="tab" class="wb-tabs" @tab-change="load">
      <el-tab-pane label="拣货" name="PICK" />
      <el-tab-pane label="上架" name="PUTAWAY" />
      <el-tab-pane label="复核" name="REVIEW" />
    </el-tabs>

    <!-- 拣货 / 上架任务 -->
    <el-table v-if="tab !== 'REVIEW'" :data="rows" v-loading="loading" class="wb-table" height="calc(100vh - 380px)">
      <el-table-column prop="波次号" label="波次" width="132">
        <template #default="{ row }">{{ row['波次号'] || '—' }}</template>
      </el-table-column>
      <el-table-column prop="来源单号" label="来源单号" width="168" />
      <el-table-column prop="仓库" label="仓库" width="96" />
      <el-table-column v-if="tab === 'PICK'" prop="来源货位" label="拣货位" width="96">
        <template #default="{ row }">{{ row['来源货位'] || '—' }}</template>
      </el-table-column>
      <el-table-column v-else prop="目标货位" label="上架货位" width="110">
        <template #default="{ row }">{{ row['目标货位'] || '待上架' }}</template>
      </el-table-column>
      <el-table-column prop="存货名称" label="存货" min-width="150">
        <template #default="{ row }">
          {{ row['存货名称'] }}
          <span class="wb-code">{{ row['存货编码'] }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="计划数量" label="计划" width="92" align="right">
        <template #default="{ row }"><span class="num">{{ qty(row['计划数量']) }}</span></template>
      </el-table-column>
      <el-table-column prop="完成数量" label="实作业" width="92" align="right">
        <template #default="{ row }"><span class="num">{{ qty(row['完成数量']) }}</span></template>
      </el-table-column>
      <el-table-column prop="差异数量" label="差异" width="88" align="right">
        <template #default="{ row }">
          <span class="num" :class="{ warn: Number(row['差异数量']) > 0 }">{{ qty(row['差异数量']) }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="状态" label="状态" width="96">
        <template #default="{ row }">
          <span class="wb-tag" :class="tagOf(row['状态'])">{{ row['状态'] }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="复核" label="复核" width="92">
        <template #default="{ row }">{{ row['复核'] || '未复核' }}</template>
      </el-table-column>
      <el-table-column label="操作" width="92" align="right">
        <template #default="{ row }">
          <el-button link type="primary" :disabled="row['状态'] === '已完成'" @click="openConfirm(row)">
            确认
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 复核：按来源单号汇总 -->
    <el-table v-else :data="reviewRows" v-loading="loading" class="wb-table" height="calc(100vh - 380px)">
      <el-table-column prop="来源单号" label="来源单号" width="176" />
      <el-table-column prop="仓库" label="仓库" width="110" />
      <el-table-column prop="任务数" label="任务数" width="88" align="right">
        <template #default="{ row }"><span class="num">{{ row['任务数'] }}</span></template>
      </el-table-column>
      <el-table-column prop="已拣任务" label="已拣完" width="96" align="right">
        <template #default="{ row }"><span class="num">{{ row['已拣任务'] }}</span></template>
      </el-table-column>
      <el-table-column prop="复核" label="复核结论" width="120">
        <template #default="{ row }">{{ row['复核'] || '未复核' }}</template>
      </el-table-column>
      <el-table-column prop="复核说明" label="说明" min-width="160">
        <template #default="{ row }">{{ row['复核说明'] || '—' }}</template>
      </el-table-column>
      <el-table-column label="操作" width="168" align="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="review(row['来源单号'], '已复核', '')">复核通过</el-button>
          <el-button link type="warning" @click="review(row['来源单号'], '有差异', '数量与单据不符')">
            有差异
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dlg.visible" :title="dlg.row?.['作业类型'] === '上架' ? '上架确认' : '拣货确认'" width="420px">
      <div class="wb-dlg">
        <div class="wb-dlg-line">
          <span>{{ dlg.row?.['存货名称'] }}</span>
          <span class="wb-code">{{ dlg.row?.['来源单号'] }}</span>
        </div>
        <el-form label-width="86px">
          <el-form-item label="计划量">
            <span class="num">{{ qty(dlg.row?.['计划数量']) }}</span>
          </el-form-item>
          <el-form-item label="实作业">
            <el-input-number v-model="dlg.qty" :min="0" :max="Number(dlg.row?.['计划数量']) || 0" :step="1" />
          </el-form-item>
          <el-form-item v-if="dlg.row?.['作业类型'] === '上架'" label="目标货位" required>
            <el-input v-model="dlg.location" placeholder="如 A-01" maxlength="20" />
          </el-form-item>
          <el-form-item label="操作人">
            <el-input v-model="dlg.operator" maxlength="20" />
          </el-form-item>
        </el-form>
      </div>
      <template #footer>
        <el-button @click="dlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="dlg.saving" @click="confirm">确认</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import request from '@core/request'
import { useUserStore } from '@/stores/user'

const user = useUserStore()
const tab = ref('PICK')
const loading = ref(false)
const tasks = ref([])
const summary = ref({})
const dlg = reactive({ visible: false, row: null, qty: 0, location: '', operator: '', saving: false })

const metrics = computed(() => [
  { label: '任务', value: summary.value['任务数'] ?? 0 },
  { label: '待作业', value: summary.value['待作业'] ?? 0, tone: 'warn' },
  { label: '部分完成', value: summary.value['部分完成'] ?? 0 },
  { label: '已完成', value: summary.value['已完成'] ?? 0, tone: 'ok' },
  { label: '未复核任务', value: summary.value['未复核任务'] ?? 0 },
])

const rows = computed(() => tasks.value.filter((t) => typeOf(t) === tab.value))
const pendingPicks = computed(() => tasks.value.filter((t) => typeOf(t) === 'PICK' && t['状态'] === '待作业').length)

/** 按来源单号汇总复核用：一张出库单是否拣完、复核结论。 */
const reviewRows = computed(() => {
  const map = new Map()
  for (const t of tasks.value) {
    const key = t['来源单号']
    if (!map.has(key)) {
      map.set(key, {
        来源单号: key,
        仓库: t['仓库'],
        任务数: 0,
        已拣任务: 0,
        复核: t['复核'] || '',
        复核说明: t['复核说明'] || '',
      })
    }
    const row = map.get(key)
    row['任务数'] += 1
    if (t['状态'] === '已完成') row['已拣任务'] += 1
    if (!row['复核'] && t['复核']) row['复核'] = t['复核']
  }
  return [...map.values()]
})

function typeOf(task) {
  return task['作业类型'] === '上架' ? 'PUTAWAY' : 'PICK'
}

function tagOf(status) {
  if (status === '已完成') return 'ok'
  if (status === '部分完成') return 'info'
  return 'warn'
}

function qty(v) {
  const n = Number(v || 0)
  return Number.isInteger(n) ? String(n) : n.toFixed(2)
}

async function load() {
  loading.value = true
  try {
    const [list, sum] = await Promise.all([
      request.get('/px/wms/tasks'),
      request.get('/px/wms/summary'),
    ])
    tasks.value = list?.data || []
    summary.value = sum?.data || {}
  } finally {
    loading.value = false
  }
}

async function sync(type) {
  const r = await request.post('/px/wms/sync', { 类型: type })
  ElMessage.success(`同步完成：新增 ${r?.data?.['新增任务'] ?? 0} 条、已有 ${r?.data?.['已有任务'] ?? 0} 条`)
  await load()
}

async function wave() {
  const r = await request.post('/px/wms/wave', {})
  const wave = r?.data?.['波次号']
  if (!wave) {
    ElMessage.info('没有待编波的拣货任务')
    return
  }
  ElMessage.success(`已编波次 ${wave}，含 ${r.data['任务数']} 条任务`)
  await load()
}

function openConfirm(row) {
  dlg.visible = true
  dlg.row = row
  dlg.qty = Number(row['计划数量']) || 0
  dlg.location = row['目标货位'] || ''
  dlg.operator = user.realName || user.account || '仓管'
}

async function confirm() {
  if (dlg.row?.['作业类型'] === '上架' && Number(dlg.qty) >= Number(dlg.row?.['计划数量']) && !dlg.location.trim()) {
    ElMessage.warning('上架完成必须填目标货位')
    return
  }
  dlg.saving = true
  try {
    await request.post('/px/wms/confirm', {
      id: dlg.row.id,
      数量: dlg.qty,
      货位: dlg.location,
      操作人: dlg.operator,
    })
    dlg.visible = false
    ElMessage.success('已记录作业量')
    await load()
  } catch (e) {
    ElMessage.error(e?.message || '确认失败')
  } finally {
    dlg.saving = false
  }
}

async function review(sourceNo, result, remark) {
  try {
    await request.post('/px/wms/review', { 来源单号: sourceNo, 结果: result, 备注: remark })
    ElMessage.success(`${sourceNo} 已标记${result}`)
    await load()
  } catch (e) {
    ElMessage.error(e?.message || '复核失败')
  }
}

onMounted(load)
</script>

<style scoped>
.wb {
  padding: 14px 18px 0;
  /* 内容超过视口时撑开页面滚动，而不是把下半截裁掉 */
  min-height: 100%;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  background: var(--d-bg, #f6f7f8);
}

.wb-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.wb-title h2 {
  margin: 0;
  font-size: 16px;
  line-height: 24px;
  color: var(--d-text, #1f2d3d);
}

.wb-sub {
  display: block;
  margin-top: 4px;
  font-size: 12px;
  line-height: 20px;
  color: var(--d-text-3, #8b9893);
}

.dot {
  margin: 0 6px;
  color: var(--d-border-strong, #dce4e1);
}

.wb-actions {
  display: flex;
  gap: 8px;
  flex: 0 0 auto;
}

.wb-metrics {
  display: flex;
  gap: 10px;
  margin: 12px 0 4px;
}

.wb-metric {
  flex: 1;
  padding: 8px 12px;
  border: 1px solid var(--d-border, #edf1ef);
  border-radius: 6px;
  background: #fff;
}

.wb-metric-label {
  display: block;
  font-size: 12px;
  line-height: 18px;
  color: var(--d-text-3, #8b9893);
}

.wb-metric-value {
  display: block;
  font-size: 18px;
  line-height: 26px;
  font-variant-numeric: tabular-nums;
  color: var(--d-text, #1f2d3d);
}

.wb-metric-value.ok { color: var(--d-ok, #52c41a); }
.wb-metric-value.warn { color: var(--d-warn, #faad14); }

.wb-tabs {
  margin-top: 4px;
}

.wb-table {
  background: #fff;
}

.wb-code {
  margin-left: 6px;
  font-size: 12px;
  color: var(--d-text-3, #8b9893);
  font-variant-numeric: tabular-nums;
}

.num {
  font-variant-numeric: tabular-nums;
}

.num.warn {
  color: var(--d-warn, #faad14);
}

.wb-tag {
  display: inline-block;
  padding: 0 6px;
  border-radius: 4px;
  font-size: 12px;
  line-height: 20px;
  border: 1px solid var(--d-border-strong, #dce4e1);
  color: var(--d-text-2, #5b6b66);
}

.wb-tag.ok {
  border-color: #b7eb8f;
  background: #f6ffed;
  color: #389e0d;
}

.wb-tag.info {
  border-color: #91caff;
  background: #f0f7ff;
  color: #0958d9;
}

.wb-tag.warn {
  border-color: #ffe58f;
  background: #fffbe6;
  color: #d48806;
}

.wb-dlg-line {
  display: flex;
  justify-content: space-between;
  margin-bottom: 10px;
  font-size: 13px;
  color: var(--d-text, #1f2d3d);
}
</style>
