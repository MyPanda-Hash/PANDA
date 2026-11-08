<!-- InvoiceLinePickDialog.vue — 采购发票行级选来源单：勾选入库/退货单行并按本次开票数量带入表体 -->
<template>
  <el-dialog
    :model-value="modelValue"
    :title="tt('选入库单行')"
    width="min(1240px, 96vw)"
    append-to-body
    @update:model-value="close"
    @open="onOpen"
  >
    <div class="pick-bar">
      <el-input
        v-model="keyword"
        clearable
        :prefix-icon="Search"
        :placeholder="tt('搜索单号 / 存货 / 供应商')"
        @keyup.enter="load"
        @clear="load"
      />
      <el-button :loading="loading" @click="load">{{ tt('查询') }}</el-button>
      <span class="pick-meta">
        {{ modeLabel }}<template v-if="supplier"> · {{ tt('供应商') }}：{{ supplier }}</template>
      </span>
    </div>

    <el-table
      :data="rows"
      v-loading="loading"
      size="small"
      border
      height="360"
      :empty-text="tt('没有可开票的来源行')"
      @selection-change="onSelectionChange"
    >
      <el-table-column type="selection" width="45" fixed="left" :selectable="rowSelectable" />
      <el-table-column
        v-for="c in columns"
        :key="c.key"
        :prop="c.key"
        :label="tt(c.key)"
        :width="c.width"
        :min-width="c.minWidth"
        :align="c.align || 'left'"
        :show-overflow-tooltip="!c.numeric"
      >
        <template v-if="c.numeric" #default="{ row }">
          <span class="pick-num">{{ numberText(row[c.key]) }}</span>
        </template>
      </el-table-column>
      <el-table-column :label="tt('本次开票数量')" width="124" align="right" fixed="right">
        <template #default="{ row }">
          <el-input-number
            :model-value="qtyOf(row)"
            :min="0"
            :max="maxQty(row)"
            :precision="4"
            :step="1"
            :controls="false"
            size="small"
            style="width: 100%"
            :disabled="!isSelected(row)"
            @update:model-value="(value) => setQty(row, value)"
          />
        </template>
      </el-table-column>
    </el-table>

    <div class="pick-foot">
      <span class="pick-count">{{ tt('已选') }} {{ selectedRows.length }} {{ tt('行') }}</span>
      <span class="pick-tip">{{ tt('本次开票数量默认等于未开票数量，可修改') }}</span>
      <el-button @click="close">{{ tt('取消') }}</el-button>
      <el-button type="primary" :disabled="!selectedRows.length" @click="confirmPick">{{ tt('确定') }}</el-button>
    </div>
  </el-dialog>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import { usePanelRuntime } from '@core/panel-runtime'
import { tt } from '@/i18n'

const engine = usePanelRuntime()

const props = defineProps({
  modelValue: Boolean,
  /** purchase：采购入库单；return：采购退货单 */
  mode: { type: String, default: 'purchase' },
  supplier: { type: String, default: '' },
  panelCode: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue', 'confirm'])

/** 列顺序与后端 invoiceSourceLines 返回字段一致；数字列右对齐 */
const columns = [
  { key: '来源面板', width: 100 },
  { key: '来源单号', width: 130 },
  { key: '来源行号', width: 72, align: 'center' },
  { key: '存货编码', width: 110 },
  { key: '存货名称', minWidth: 150 },
  { key: '规格型号', width: 110 },
  { key: '计量单位', width: 76, align: 'center' },
  { key: '入库数量', width: 96, align: 'right', numeric: true },
  { key: '已开票数量', width: 100, align: 'right', numeric: true },
  { key: '未开票数量', width: 100, align: 'right', numeric: true },
  { key: '单价', width: 90, align: 'right', numeric: true },
  { key: '税率%', width: 76, align: 'right', numeric: true },
  { key: '含税单价', width: 100, align: 'right', numeric: true },
  { key: '金额', width: 100, align: 'right', numeric: true },
  { key: '税额', width: 100, align: 'right', numeric: true },
  { key: '含税金额', width: 110, align: 'right', numeric: true },
]

const rows = ref([])
const loading = ref(false)
const keyword = ref('')
const selectedRows = ref([])
/** 行键 → 本次开票数量（未勾选的行不保留值） */
const qtyMap = reactive({})

const modeLabel = computed(() => (props.mode === 'return' ? tt('采购退货单') : tt('采购入库单')))
const selectedKeys = computed(() => new Set(selectedRows.value.map(rowKey)))

function rowKey(row) {
  return [row?.['来源面板'], row?.['来源单号'], row?.['来源行号']]
    .map((value) => String(value ?? '').trim())
    .join('|')
}

function maxQty(row) {
  const max = Number(row['未开票数量'])
  return Number.isFinite(max) && max > 0 ? max : 0
}

function rowSelectable(row) {
  return maxQty(row) > 0
}

function isSelected(row) {
  return selectedKeys.value.has(rowKey(row))
}

function round4(value) {
  const n = Number(value)
  return Number.isFinite(n) ? Math.round(n * 10000) / 10000 : 0
}

function qtyOf(row) {
  return qtyMap[rowKey(row)] ?? 0
}

function setQty(row, value) {
  const max = maxQty(row)
  let qty = round4(value)
  if (qty < 0) qty = 0
  if (max > 0 && qty > max) qty = max
  qtyMap[rowKey(row)] = qty
}

function numberText(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return value === undefined || value === null ? '' : String(value)
  return n.toLocaleString('zh-CN', { maximumFractionDigits: 4 })
}

/** 关键字过滤字段：单号 / 存货 / 供应商（后端 supplier 固定时仍覆盖规格型号等可见列） */
const SEARCH_FIELDS = ['来源面板', '来源单号', '存货编码', '存货名称', '规格型号', '供应商', '供应商编码']

function matchesKeyword(row, keyword) {
  if (!keyword) return true
  const needle = keyword.toLowerCase()
  return SEARCH_FIELDS.some((field) => String(row[field] ?? '').toLowerCase().includes(needle))
}

function onSelectionChange(selection) {
  selectedRows.value = selection
  const keys = new Set(selection.map(rowKey))
  for (const key of Object.keys(qtyMap)) {
    if (!keys.has(key)) delete qtyMap[key]
  }
  for (const row of selection) {
    const key = rowKey(row)
    if (qtyMap[key] === undefined || qtyMap[key] === null) qtyMap[key] = round4(maxQty(row))
  }
}

function close() {
  emit('update:modelValue', false)
}

function onOpen() {
  keyword.value = ''
  rows.value = []
  selectedRows.value = []
  for (const key of Object.keys(qtyMap)) delete qtyMap[key]
  load()
}

async function load() {
  loading.value = true
  const keywordText = String(keyword.value || '').trim()
  const supplier = props.supplier || ''
  try {
    // 表头未带供应商时，后端 keyword 只匹配单号/存货，供应商名会被直接过滤掉；
    // 此时取全量后在前端按「单号/存货/供应商」统一过滤（来源行数据量小）。
    const res = await engine.invoiceSourceLines({
      mode: props.mode === 'return' ? 'return' : 'purchase',
      supplier,
      keyword: supplier ? keywordText : '',
    })
    const list = Array.isArray(res?.list) ? res.list : []
    rows.value = keywordText ? list.filter((row) => matchesKeyword(row, keywordText)) : list
    selectedRows.value = []
    for (const key of Object.keys(qtyMap)) delete qtyMap[key]
  } catch (e) {
    rows.value = []
    selectedRows.value = []
    ElMessage.error(engine.errMsg(e) || tt('来源单据加载失败'))
  } finally {
    loading.value = false
  }
}

function confirmPick() {
  if (!selectedRows.value.length) return
  const picked = []
  for (const row of selectedRows.value) {
    const max = maxQty(row)
    const qty = round4(qtyMap[rowKey(row)] ?? max)
    if (!(qty > 0)) {
      ElMessage.warning(tt('本次开票数量必须大于 0'))
      return
    }
    if (max > 0 && qty > max) {
      ElMessage.warning(tt('本次开票数量不能大于未开票数量'))
      return
    }
    picked.push({ ...row, selectedQty: qty })
  }
  emit('confirm', picked)
  close()
}
</script>

<style scoped>
.pick-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 8px;
}
.pick-bar :deep(.el-input) {
  flex: 1 1 220px;
  min-width: 0;
}
.pick-bar :deep(.el-input__wrapper) {
  border-radius: 6px;
}
.pick-meta {
  margin-left: auto;
  min-width: 0;
  font-size: 12px;
  color: #666;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.pick-num {
  font-variant-numeric: tabular-nums;
}
.pick-foot {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}
.pick-count {
  font-size: 12px;
  color: #333;
  font-variant-numeric: tabular-nums;
}
.pick-tip {
  font-size: 12px;
  color: #999;
}
.pick-foot .el-button:first-of-type {
  margin-left: auto;
}
</style>
