<!-- SaleOutPreviewDialog.vue — 出库预览：销售订单 → 销售出库单
     与生成生产加工单的预览保持同一套体验：逐行显示需求/已出库/剩余待出/现存量，
     默认勾选能出的行并带出建议出库数量，可改数量，库存不够的行标红。 -->
<template>
  <el-dialog
    :model-value="modelValue"
    :title="tt('生成销售出库单 · 出库数量确认')"
    width="1060px"
    append-to-body
    @update:model-value="close"
    @open="onOpen"
  >
    <div class="so-bar">
      <span class="so-label">{{ tt('发货仓库') }}</span>
      <el-select v-model="warehouse" size="small" style="width: 180px" @change="reload">
        <el-option v-for="w in warehouses" :key="w.code" :label="w.name" :value="w.name" />
      </el-select>
      <span class="so-tip">
        {{ tt('建议出库数量 = min(剩余待出, 所选仓库现存量)；库存不足的部分请先生成生产加工单。') }}
      </span>
    </div>

    <div v-if="!loading && rows.length" class="so-sum">
      <span v-if="shortageCount" class="so-warn">{{ shortageCount }} {{ tt('行库存不足') }}</span>
      <span v-else class="so-ok">{{ tt('库存可覆盖本次出库') }}</span>
      <span class="so-total">
        {{ tt('本次出库') }} {{ pickedCount }} {{ tt('行，合计') }} {{ pickedQty }} {{ tt('件') }}
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
      <el-table-column type="selection" width="45" />
      <el-table-column :label="tt('存货编码')" prop="存货编码" width="100" />
      <el-table-column :label="tt('存货名称')" prop="存货名称" min-width="130" show-overflow-tooltip />
      <el-table-column :label="tt('规格型号')" prop="规格型号" min-width="110" show-overflow-tooltip />
      <el-table-column :label="tt('需求数量')" width="90" align="right">
        <template #default="{ row }">{{ fmt(row['需求数量']) }}</template>
      </el-table-column>
      <el-table-column :label="tt('已出库')" width="90" align="right">
        <template #default="{ row }">{{ fmt(row['已出库数量']) }}</template>
      </el-table-column>
      <el-table-column :label="tt('剩余待出')" width="95" align="right">
        <template #default="{ row }">{{ fmt(row['剩余待出数量']) }}</template>
      </el-table-column>
      <el-table-column :label="tt('现存量')" width="90" align="right">
        <template #default="{ row }">
          <span :class="{ 'so-short': row['是否不足'] }">{{ fmt(row['现存量']) }}</span>
        </template>
      </el-table-column>
      <el-table-column :label="tt('出库数量')" width="120" align="right">
        <template #default="{ row }">
          <el-input-number
            v-model="row['出库数量']"
            :min="0"
            :max="Number(row['剩余待出数量'])"
            :controls="false"
            :disabled="!isPicked(row)"
            size="small"
            style="width: 100%"
          />
        </template>
      </el-table-column>
      <el-table-column :label="tt('状态')" width="80" align="center">
        <template #default="{ row }">
          <span v-if="row['是否不足']" class="so-short">{{ tt('不足') }}</span>
          <span v-else class="so-ok">{{ tt('可出') }}</span>
        </template>
      </el-table-column>
    </el-table>

    <template #footer>
      <el-button @click="close">{{ tt('取消') }}</el-button>
      <el-button type="primary" :loading="generating" :disabled="pickedQty <= 0" @click="confirm">
        {{ tt('确认生成销售出库单') }}
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
const warehouse = ref('成品仓')
const warehouses = ref([{ code: 'CK03', name: '成品仓' }])
const generating = ref(false)
const tableRef = ref(null)
const picked = ref([])

const shortageCount = computed(() => rows.value.filter((r) => r['是否不足']).length)
const pickedCount = computed(() => picked.value.filter((r) => Number(r['出库数量']) > 0).length)
const pickedQty = computed(() =>
  picked.value.reduce((sum, r) => sum + (Number(r['出库数量']) || 0), 0)
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

/** 切换仓库后重新取库存/建议量，勾选状态按新数据重算。 */
async function reload() {
  await load()
}

async function loadWarehouses() {
  try {
    const res = await engine.queryFormDataList({ panelCode: 'WH', pageNo: 1, pageSize: 5 })
    const stores = (res?.list?.[0]?.detail?.stores) || []
    const list = stores
      .filter((s) => s && !s['停用'] && s['仓库名称'])
      .map((s) => ({ code: s['仓库编码'] || s['仓库名称'], name: s['仓库名称'] }))
    if (list.length) {
      warehouses.value = list
      if (!list.some((w) => w.name === warehouse.value)) warehouse.value = list[0].name
    }
  } catch (e) {
    // 仓库档案读取失败不阻断出库，保留默认成品仓
  }
}

async function onOpen() {
  await loadWarehouses()
  await load()
}

async function load() {
  if (!props.docNo) return
  loading.value = true
  rows.value = []
  picked.value = []
  try {
    const list = await engine.previewSaleOut({ docNo: props.docNo, warehouse: warehouse.value })
    rows.value = (list || []).map((r) => ({
      ...r,
      出库数量: Number(r['建议出库数量']) > 0 ? Number(r['建议出库数量']) : 0,
    }))
    await nextTick()
    for (const row of rows.value) {
      if (Number(row['出库数量']) > 0) tableRef.value?.toggleRowSelection(row, true)
    }
    if (!rows.value.length) {
      ElMessage.info(tt('该销售订单已全部出库，无需再生成销售出库单'))
    } else if (!shortageCount.value) {
      ElMessage.info(tt('成品仓库存可覆盖全部待出数量'))
    }
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || tt('出库预览加载失败'))
  } finally {
    loading.value = false
  }
}

async function confirm() {
  const lines = picked.value
    .filter((r) => Number(r['出库数量']) > 0)
    .map((r) => ({ 存货编码: r['存货编码'], 数量: Number(r['出库数量']) }))
  if (!lines.length) return ElMessage.warning(tt('请填写大于 0 的出库数量'))
  generating.value = true
  try {
    const res = await engine.callButton({
      panelCode: 'SO_ORDER',
      buttonName: '生成销售出库单',
      formData: { 编号: props.docNo },
      buttonParam: { lines, warehouse: warehouse.value },
    })
    // 提示与跳转交给父页面统一处理
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
.so-bar {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 10px;
}
.so-label {
  font-size: 13px;
  color: var(--el-text-color-regular);
}
.so-tip {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 18px;
}
.so-sum {
  display: flex;
  gap: 16px;
  align-items: center;
  margin-bottom: 8px;
  font-size: 13px;
}
.so-warn {
  color: var(--el-color-warning);
  font-weight: 600;
}
.so-ok {
  color: var(--el-color-success);
}
.so-total {
  margin-left: auto;
  color: var(--el-text-color-regular);
}
.so-short {
  color: var(--el-color-danger);
}
</style>