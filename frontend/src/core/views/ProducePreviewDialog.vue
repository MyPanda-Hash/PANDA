<!-- ProducePreviewDialog.vue — 生单预览：销售订单 → 生产加工单
     逐行显示需求/现存/已下单生产量，默认只勾选不足的行并带出建议生产数量，
     用户可勾选与改数量，避免有货的也一起投产造成过度生产。 -->
<template>
  <el-dialog
    :model-value="modelValue"
    :title="tt('生成生产加工单 · 生产明细确认')"
    width="1040px"
    append-to-body
    @update:model-value="close"
    @open="load"
  >
    <div class="pp-tip">
      {{ tt('现存量取自成品仓。建议生产数量 = 需求 − 现存量 − 已下单生产数量，只勾选需要投产的行。') }}
    </div>

    <div v-if="!loading && rows.length" class="pp-sum">
      <span v-if="shortageCount" class="pp-warn">
        {{ shortageCount }} {{ tt('行库存不足') }}
      </span>
      <span v-else class="pp-ok">{{ tt('全部有货，无需投产') }}</span>
      <span class="pp-total">
        {{ tt('本次投产') }} {{ pickedCount }} {{ tt('行，合计') }} {{ pickedQty }} {{ tt('件') }}
      </span>
    </div>

    <el-table
      ref="tableRef"
      :data="rows"
      v-loading="loading"
      size="small"
      border
      height="360"
      @selection-change="onSel"
    >
      <el-table-column type="selection" width="45" :selectable="() => true" />
      <el-table-column :label="tt('存货编码')" prop="存货编码" width="100" />
      <el-table-column :label="tt('存货名称')" prop="存货名称" min-width="130" show-overflow-tooltip />
      <el-table-column :label="tt('规格型号')" prop="规格型号" min-width="110" show-overflow-tooltip />
      <el-table-column :label="tt('需求数量')" width="90" align="right">
        <template #default="{ row }">{{ fmt(row['需求数量']) }}</template>
      </el-table-column>
      <el-table-column :label="tt('现存量')" width="90" align="right">
        <template #default="{ row }">
          <span :class="{ 'pp-short': row['是否不足'] }">{{ fmt(row['现存量']) }}</span>
        </template>
      </el-table-column>
      <el-table-column :label="tt('已下单生产')" width="100" align="right">
        <template #default="{ row }">{{ fmt(row['已下单生产数量']) }}</template>
      </el-table-column>
      <el-table-column :label="tt('生产数量')" width="120" align="right">
        <template #default="{ row }">
          <el-input-number
            v-model="row['生产数量']"
            :min="0"
            :controls="false"
            :disabled="!isPicked(row)"
            size="small"
            style="width: 100%"
          />
        </template>
      </el-table-column>
      <el-table-column :label="tt('状态')" width="70" align="center">
        <template #default="{ row }">
          <span v-if="row['是否不足']" class="pp-short">{{ tt('不足') }}</span>
          <span v-else class="pp-ok">{{ tt('已够') }}</span>
        </template>
      </el-table-column>
    </el-table>

    <template #footer>
      <el-button @click="close">{{ tt('取消') }}</el-button>
      <el-button type="primary" :loading="generating" :disabled="pickedQty <= 0" @click="confirm">
        {{ tt('确认生成生产加工单') }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { usePanelRuntime } from '@core/panel-runtime'
import { tt } from '@/i18n'

const engine = usePanelRuntime()

const props = defineProps({
  modelValue: Boolean,
  docNo: String,
})
const emit = defineEmits(['update:modelValue', 'generated'])

const rows = ref([])
const loading = ref(false)
const generating = ref(false)
const tableRef = ref(null)
const picked = ref([])

const shortageCount = computed(() => rows.value.filter((r) => r['是否不足']).length)
const pickedCount = computed(() => picked.value.filter((r) => Number(r['生产数量']) > 0).length)
const pickedQty = computed(() =>
  picked.value.reduce((sum, r) => sum + (Number(r['生产数量']) || 0), 0)
)

function fmt(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return v ?? ''
  return Math.round(n * 100) / 100
}

function isPicked(row) {
  return picked.value.includes(row)
}

function onSel(sel) {
  picked.value = sel
}

async function load() {
  if (!props.docNo) return
  loading.value = true
  rows.value = []
  picked.value = []
  try {
    const list = await engine.previewProduce({ docNo: props.docNo })
    // 默认只勾选不足的行，生产数量默认取建议值；已够的行留空不投产
    rows.value = (list || []).map((r) => ({
      ...r,
      生产数量: Number(r['建议生产数量']) > 0 ? Number(r['建议生产数量']) : 0,
    }))
    await nextTick()
    for (const row of rows.value) {
      if (Number(row['生产数量']) > 0) tableRef.value?.toggleRowSelection(row, true)
    }
    if (!shortageCount.value && rows.value.length) {
      ElMessage.info(tt('成品仓库存已覆盖全部需求，无需投产；如需预生产请直接新增生产加工单'))
    }
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || tt('生单预览加载失败'))
  } finally {
    loading.value = false
  }
}

async function confirm() {
  const lines = picked.value
    .filter((r) => Number(r['生产数量']) > 0)
    .map((r) => ({ 存货编码: r['存货编码'], 数量: Number(r['生产数量']) }))
  if (!lines.length) return ElMessage.warning(tt('请填写大于 0 的生产数量'))
  generating.value = true
  try {
    const res = await engine.callButton({
      panelCode: 'SO_ORDER',
      buttonName: '生成生产加工单',
      formData: { 编号: props.docNo },
      buttonParam: { lines },
    })
    // 成功提示与跳转交给父页面处理：列表页/表单页对「推式生单」已有统一的落地方式
    // （跳到目标面板并定位新单），这里不重复弹提示，避免双 toast。
    emit('generated', res)
    close()
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || tt('生成失败'))
  } finally {
    generating.value = false
  }
}

function close() {
  emit('update:modelValue', false)
}
</script>

<style scoped>
.pp-tip {
  margin-bottom: 10px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 18px;
}
.pp-sum {
  display: flex;
  gap: 16px;
  align-items: center;
  margin-bottom: 8px;
  font-size: 13px;
}
.pp-warn {
  color: var(--el-color-warning);
  font-weight: 600;
}
.pp-ok {
  color: var(--el-color-success);
}
.pp-total {
  margin-left: auto;
  color: var(--el-text-color-regular);
}
.pp-short {
  color: var(--el-color-danger);
}
</style>