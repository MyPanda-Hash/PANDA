<!-- FinishInspectPickDialog.vue — 成品检验单的「生单」
     只列「已报完全部工序、且还没生成过成品报检单」的加工单；
     勾选后生成成品报检单，单号 = 加工单号-JY，与加工单单号直接对得上。 -->
<template>
  <el-dialog
    :model-value="modelValue"
    :title="tt('生成成品检验单 · 选生产加工单')"
    width="min(1040px, 96vw)"
    append-to-body
    @update:model-value="close"
    @open="load"
  >
    <p class="fip-tip">
      {{ tt('只列已报完全部工序、且还没生成过成品报检单的加工单。生成的报检单号 = 加工单号-JY，同一张加工单不能重复生成。') }}
    </p>

    <el-table
      :data="rows"
      v-loading="loading"
      size="small"
      border
      height="340"
      @selection-change="onSel"
    >
      <el-table-column type="selection" width="46" />
      <el-table-column label="加工单号" width="190">
        <template #default="{ row }"><span class="mono">{{ row['加工单号'] }}</span></template>
      </el-table-column>
      <el-table-column label="客户" prop="客户" min-width="150" show-overflow-tooltip />
      <el-table-column label="产品" min-width="200" show-overflow-tooltip>
        <template #default="{ row }">{{ row['产品编码'] }} {{ row['产品名称'] }}</template>
      </el-table-column>
      <el-table-column label="数量" width="90" align="right">
        <template #default="{ row }">{{ qty(row['数量']) }}</template>
      </el-table-column>
      <el-table-column label="工序进度" width="110" align="center">
        <template #default="{ row }">
          <b class="ok">{{ row['已完成工序数'] }}</b><span class="dim"> / {{ row['工序数'] }} 道</span>
        </template>
      </el-table-column>
      <el-table-column label="报检单号" min-width="190">
        <template #default="{ row }"><span class="mono">{{ row['报检单号'] }}</span></template>
      </el-table-column>
    </el-table>

    <el-empty v-if="!loading && !rows.length" description="没有可报检的加工单：需要先把全部工序报工完成" :image-size="60" />

    <template #footer>
      <span class="fip-foot">{{ tt('本次生成') }} {{ picked.length }} {{ tt('张') }}</span>
      <el-button size="small" @click="close">{{ tt('取消') }}</el-button>
      <el-button size="small" type="primary" :loading="submitting" :disabled="!picked.length" @click="submit">
        {{ tt('生成成品检验单') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { tt } from '@/i18n'
import { usePanelRuntime } from '@core/panel-runtime'

defineProps({ modelValue: { type: Boolean, default: false } })
const emit = defineEmits(['update:modelValue', 'generated'])

const engine = usePanelRuntime()
const rows = ref([])
const picked = ref([])
const loading = ref(false)
const submitting = ref(false)

function qty(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return '0'
  return n === Math.round(n) ? String(n) : String(Number(n.toFixed(2)))
}

async function load() {
  loading.value = true
  picked.value = []
  try {
    rows.value = (await engine.finishInspectCandidates()) || []
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || tt('加载加工单失败'))
  } finally {
    loading.value = false
  }
}

function onSel(list) {
  picked.value = list || []
}

async function submit() {
  if (!picked.value.length) return
  submitting.value = true
  const ok = []
  const fail = []
  try {
    for (const row of picked.value) {
      try {
        const res = await engine.finishInspectFromMo(row['加工单号'])
        if (res?.['编号']) ok.push(res['编号'])
      } catch (e) {
        fail.push(`${row['加工单号']}：${engine.errMsg(e) || tt('生成失败')}`)
      }
    }
    if (ok.length) ElMessage.success(`${tt('已生成成品检验单')}：${ok.join('、')}`)
    if (fail.length) ElMessage.warning(fail.join('；'))
    if (ok.length || fail.length) emit('generated', ok)
    await load()
  } finally {
    submitting.value = false
  }
}

function close() {
  emit('update:modelValue', false)
}
</script>

<style scoped>
.fip-tip {
  margin: 0 0 8px;
  font-size: 12px;
  line-height: 20px;
  color: #8c8c8c;
}

.fip-foot {
  float: left;
  font-size: 12px;
  line-height: 28px;
  color: rgba(0, 0, 0, 0.45);
}

.mono {
  font-family: Consolas, "Courier New", monospace;
  font-size: 13px;
}

.ok { color: #1677ff; font-variant-numeric: tabular-nums; }
.dim { color: rgba(0, 0, 0, 0.45); }

:deep(.el-table) { font-size: 13px; }
:deep(.el-table .cell) { font-variant-numeric: tabular-nums; }
</style>
