<!-- ProcessReportPickDialog.vue — 工序汇报单的「选生产加工单」
     一张加工单一行，带每道工序的计划/已报/未报；
     只能选「本道未报满 + 前道都报满」的工序，报工数量默认未报数量。
     之前走通用选单会把整张加工单的工序一起塞进一张汇报单，还会撞「已全部报工 / 没有可执行数量」。 -->
<template>
  <el-dialog
    :model-value="modelValue"
    :title="tt('选生产加工单 · 工序报工')"
    width="min(1080px, 96vw)"
    append-to-body
    @update:model-value="close"
    @open="load"
  >
    <p class="prp-tip">
      {{ tt('一道工序一张汇报单：先选加工单，再选这道工序报工。前道没报完的工序标「待生产」，已报满的不能再报。') }}
    </p>

    <el-table
      :data="rows"
      v-loading="loading"
      size="small"
      border
      height="220"
      highlight-current-row
      :row-class-name="moRowClass"
      @current-change="pickMo"
    >
      <el-table-column label="加工单号" width="180">
        <template #default="{ row }"><span class="mono">{{ row['加工单号'] }}</span></template>
      </el-table-column>
      <el-table-column label="产品" min-width="200" show-overflow-tooltip>
        <template #default="{ row }">{{ row['产品编码'] }} {{ row['产品名称'] }}</template>
      </el-table-column>
      <el-table-column label="客户" prop="客户" min-width="150" show-overflow-tooltip />
      <el-table-column label="数量" width="90" align="right">
        <template #default="{ row }">{{ qty(row['加工单数量']) }}</template>
      </el-table-column>
      <el-table-column label="报工进度" width="110" align="center">
        <template #default="{ row }">
          <b :class="row['已完成工序数'] ? 'ok' : 'zero'">{{ row['已完成工序数'] }}</b>
          <span class="dim"> / {{ row['工序数'] }} 道</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="110" align="center">
        <template #default="{ row }">
          <el-tag size="small" :type="row['全部完成'] ? 'info' : (row['已完成工序数'] ? 'warning' : 'success')" effect="light">
            {{ row['全部完成'] ? '全部报工' : (row['已完成工序数'] ? '部分报工' : '待报工') }}
          </el-tag>
        </template>
      </el-table-column>
    </el-table>

    <div v-if="pickedMo" class="prp-proc-head">
      <span class="prp-proc-title">{{ tt('工序明细') }} · {{ pickedMo['加工单号'] }}</span>
      <span class="prp-proc-sub">{{ tt('已报') }} {{ pickedMo['已完成工序数'] }} / {{ pickedMo['工序数'] }} {{ tt('道') }}</span>
    </div>
    <el-empty v-else description="先在上表选中一张加工单" :image-size="60" />

    <el-table v-if="pickedMo" :data="pickedMo.processes" size="small" border height="240">
      <el-table-column label="序" prop="工序顺序" width="52" align="center" />
      <el-table-column label="工序" min-width="130">
        <template #default="{ row }">{{ row['工序名称'] || row['工序编码'] }}</template>
      </el-table-column>
      <el-table-column label="计划" width="90" align="right">
        <template #default="{ row }">{{ qty(row['累计报工'] + row['未报数量']) }}</template>
      </el-table-column>
      <el-table-column label="已报" width="90" align="right">
        <template #default="{ row }">{{ qty(row['累计报工']) }}</template>
      </el-table-column>
      <el-table-column label="未报" width="90" align="right">
        <template #default="{ row }">
          <b :class="Number(row['未报数量']) > 0 ? 'ok' : 'zero'">{{ qty(row['未报数量']) }}</b>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="120" align="center">
        <template #default="{ row }">
          <el-tag v-if="row['是否完成']" size="small" type="info" effect="light">{{ tt('已报满') }}</el-tag>
          <el-tag v-else-if="row['可否报工']" size="small" type="warning" effect="light">{{ tt('可报工') }}</el-tag>
          <el-tag v-else size="small" type="success" effect="light">{{ tt(row['不可报原因'] || '待生产') }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="选择" width="90" align="center">
        <template #default="{ row }">
          <el-radio
            v-model="pickedCode"
            :label="row['工序编码']"
            :disabled="!row['可否报工']"
            @change="onPickProcess(row)"
          ><span /></el-radio>
        </template>
      </el-table-column>
    </el-table>

    <div v-if="pickedProcess" class="prp-form">
      <span class="prp-form-label">{{ tt('报工数量') }}</span>
      <el-input-number
        v-model="qtyValue"
        :min="0"
        :max="Number(pickedProcess['未报数量'])"
        :step="1"
        size="small"
        controls-position="right"
        style="width: 140px"
      />
      <span class="prp-form-hint">
        {{ tt('工序') }} {{ pickedProcess['工序名称'] }} · {{ tt('未报') }} {{ qty(pickedProcess['未报数量']) }}
      </span>
    </div>

    <template #footer>
      <span class="prp-foot-hint">{{ tt('报工不需要审批，生成即已审核') }}</span>
      <el-button size="small" @click="close">{{ tt('取消') }}</el-button>
      <el-button size="small" type="primary" :loading="submitting" :disabled="!canSubmit" @click="submit">
        {{ tt('报工') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { tt } from '@/i18n'
import { usePanelRuntime } from '@core/panel-runtime'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'generated'])

const engine = usePanelRuntime()

const rows = ref([])
const loading = ref(false)
const submitting = ref(false)
const pickedMo = ref(null)
const pickedProcess = ref(null)
const pickedCode = ref('')
const qtyValue = ref(0)

const canSubmit = computed(() => Boolean(pickedMo.value && pickedProcess.value && Number(qtyValue.value) > 0))

function qty(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return '0'
  return n === Math.round(n) ? String(n) : String(Number(n.toFixed(2)))
}

function moRowClass({ row }) {
  return row === pickedMo.value ? 'is-picked' : ''
}

async function load() {
  loading.value = true
  pickedMo.value = null
  pickedProcess.value = null
  pickedCode.value = ''
  qtyValue.value = 0
  try {
    rows.value = (await engine.processReportPickBoard()) || []
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || tt('加载加工单失败'))
  } finally {
    loading.value = false
  }
}

function pickMo(row) {
  pickedMo.value = row || null
  pickedProcess.value = null
  pickedCode.value = ''
  qtyValue.value = 0
}

function onPickProcess(row) {
  pickedProcess.value = row
  pickedCode.value = row['工序编码']
  qtyValue.value = Number(row['未报数量']) || 0
}

async function submit() {
  if (!canSubmit.value) return
  submitting.value = true
  try {
    const res = await engine.reportProcess(pickedMo.value['加工单号'], pickedProcess.value['工序编码'], Number(qtyValue.value))
    const no = res?.['编号'] || ''
    ElMessage.success(no ? `${tt('已生成工序汇报单')} ${no}` : tt('报工成功'))
    emit('generated', no ? [no] : [])
    await load()
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || tt('报工失败'))
  } finally {
    submitting.value = false
  }
}

function close() {
  emit('update:modelValue', false)
}
</script>

<style scoped>
.prp-tip {
  margin: 0 0 8px;
  font-size: 12px;
  line-height: 20px;
  color: var(--d-text-3);
}

.prp-proc-head {
  display: flex;
  align-items: baseline;
  gap: 12px;
  margin: 12px 0 8px;
}

.prp-proc-title {
  font-size: 13px;
  font-weight: 600;
  color: rgba(0, 0, 0, 0.88);
}

.prp-proc-sub {
  font-size: 12px;
  color: rgba(0, 0, 0, 0.45);
}

.prp-form {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
}

.prp-form-label {
  font-size: 13px;
  color: rgba(0, 0, 0, 0.88);
}

.prp-form-hint {
  font-size: 12px;
  color: rgba(0, 0, 0, 0.45);
}

.prp-foot-hint {
  float: left;
  font-size: 12px;
  line-height: 28px;
  color: rgba(0, 0, 0, 0.45);
}

.mono {
  font-family: Consolas, "Courier New", monospace;
  font-size: 13px;
}

.ok { color: var(--t-primary); font-variant-numeric: tabular-nums; }
.zero { color: rgba(0, 0, 0, 0.45); font-variant-numeric: tabular-nums; }
.dim { color: rgba(0, 0, 0, 0.45); }

:deep(.el-table) { font-size: 13px; }
:deep(.el-table .cell) { font-variant-numeric: tabular-nums; }
:deep(.el-table__row.is-picked > td) { background: var(--t-primary-tint); }
</style>
