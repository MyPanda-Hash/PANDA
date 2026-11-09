<!-- FinishInPreviewDialog.vue — 入库预览：生产加工单 → 产成品入库单
     选择入库仓库 + 按剩余量确认本次入库数量，避免整单重复入库。 -->
<template>
  <el-dialog
    :model-value="modelValue"
    :title="tt('生成产成品入库单 · 入库确认')"
    width="1000px"
    append-to-body
    @update:model-value="close"
    @open="onOpen"
  >
    <div class="fi-bar">
      <span class="fi-label">{{ tt('入库仓库') }}</span>
      <el-select v-model="warehouse" size="small" style="width: 180px">
        <el-option v-for="w in warehouses" :key="w.code" :label="w.name" :value="w.name" />
      </el-select>
      <span class="fi-tip">
        {{ tt('建议入库数量 = 计划数量 − 已入库数量，可以分批入库。') }}
      </span>
    </div>

    <div v-if="!loading && rows.length" class="fi-sum">
      <span class="fi-total">
        {{ tt('本次入库') }} {{ pickedCount }} {{ tt('行，合计') }} {{ pickedQty }}
      </span>
    </div>

    <el-table
      ref="tableRef"
      :data="rows"
      v-loading="loading"
      size="small"
      border
      height="320"
      @selection-change="onSel"
    >
      <el-table-column type="selection" width="45" />
      <el-table-column :label="tt('产品编码')" prop="产品编码" width="110" />
      <el-table-column :label="tt('产品名称')" prop="产品名称" min-width="150" show-overflow-tooltip />
      <el-table-column :label="tt('规格型号')" prop="规格型号" min-width="120" show-overflow-tooltip />
      <el-table-column :label="tt('计划数量')" width="100" align="right">
        <template #default="{ row }">{{ fmt(row['计划数量']) }}</template>
      </el-table-column>
      <el-table-column :label="tt('已入库')" width="100" align="right">
        <template #default="{ row }">{{ fmt(row['已入库数量']) }}</template>
      </el-table-column>
      <el-table-column :label="tt('剩余入库')" width="100" align="right">
        <template #default="{ row }">{{ fmt(row['剩余入库数量']) }}</template>
      </el-table-column>
      <el-table-column :label="tt('入库数量')" width="130" align="right">
        <template #default="{ row }">
          <el-input-number
            v-model="row['入库数量']"
            :min="0"
            :max="Number(row['剩余入库数量'])"
            :controls="false"
            :disabled="!isPicked(row)"
            size="small"
            style="width: 100%"
          />
        </template>
      </el-table-column>
    </el-table>

    <template #footer>
      <el-button @click="close">{{ tt('取消') }}</el-button>
      <el-button type="primary" :loading="generating" :disabled="pickedQty <= 0" @click="confirm">
        {{ tt('确认生成产成品入库单') }}
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
const warehouse = ref('成品仓')
const warehouses = ref([{ code: 'CK03', name: '成品仓' }])

const pickedCount = computed(() => picked.value.filter((r) => Number(r['入库数量']) > 0).length)
const pickedQty = computed(() =>
  picked.value.reduce((sum, r) => sum + (Number(r['入库数量']) || 0), 0)
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
    // 仓库档案读取失败不阻断入库，保留默认成品仓
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
    const list = await engine.previewFinishIn({ docNo: props.docNo })
    rows.value = (list || []).map((r) => ({
      ...r,
      入库数量: Number(r['建议入库数量']) > 0 ? Number(r['建议入库数量']) : 0,
    }))
    await nextTick()
    for (const row of rows.value) {
      if (Number(row['入库数量']) > 0) tableRef.value?.toggleRowSelection(row, true)
    }
    if (!rows.value.some((r) => Number(r['建议入库数量']) > 0)) {
      ElMessage.info(tt('该生产加工单已全部入库，无需再生成产成品入库单'))
    }
  } catch (e) {
    ElMessage.error(engine.errMsg(e) || tt('入库预览加载失败'))
  } finally {
    loading.value = false
  }
}

async function confirm() {
  const lines = picked.value
    .filter((r) => Number(r['入库数量']) > 0)
    .map((r) => ({ 存货编码: r['产品编码'], 数量: Number(r['入库数量']) }))
  if (!lines.length) return ElMessage.warning(tt('请填写大于 0 的入库数量'))
  generating.value = true
  try {
    const res = await engine.callButton({
      panelCode: 'MANU_ORDER',
      buttonName: '生成产成品入库单',
      formData: { 编号: props.docNo },
      buttonParam: { lines, warehouse: warehouse.value },
    })
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
.fi-bar {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 10px;
}
.fi-label {
  font-size: 13px;
  color: var(--el-text-color-regular);
}
.fi-tip {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 18px;
}
.fi-sum {
  display: flex;
  margin-bottom: 8px;
  font-size: 13px;
}
.fi-total {
  margin-left: auto;
  color: var(--el-text-color-regular);
}
</style>